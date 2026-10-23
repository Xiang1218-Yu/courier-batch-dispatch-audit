#!/bin/sh
set -eu
umask 077
PATH=/usr/local/bin:/usr/bin:/bin
export PATH

TRUSTED_PYTHON=/usr/local/bin/python3
if [ ! -x "$TRUSTED_PYTHON" ]; then
    printf '%s\n' 'trusted verifier runtime is unavailable' >&2
    exit 0
fi

TESTS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$TESTS_DIR" = "/tests" ]; then
    BASE_DIR=/logs/verifier
    REWARD_FILE=$BASE_DIR/reward.txt
    PROGRAM_DIR=/app
else
    BASE_DIR=$(CDPATH= cd -- "$TESTS_DIR/.." && pwd)
    REWARD_FILE=${REWARD_FILE:-$BASE_DIR/reward.txt}
    PROGRAM_DIR=${PROGRAM_DIR:-$BASE_DIR/environment}
fi
# The candidate is the concrete application entrypoint; the verifier interpreter is never a candidate-controlled entry.
export PROGRAM_DIR

/bin/mkdir -p "$BASE_DIR" "$(dirname -- "$REWARD_FILE")"
/bin/chmod 0700 "$BASE_DIR"

write_reward() {
    "$TRUSTED_PYTHON" - "$REWARD_FILE" "$1" <<'PY'
import os
import sys
import tempfile
path, value = sys.argv[1:3]
directory = os.path.dirname(os.path.abspath(path)) or "."
fd, temporary = tempfile.mkstemp(prefix='.reward-', dir=directory)
try:
    with os.fdopen(fd, 'w', encoding='ascii') as stream:
        stream.write(value + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o444)
    os.replace(temporary, path)
except BaseException:
    try:
        os.unlink(temporary)
    except OSError:
        pass
    raise
PY
    /bin/chmod 0444 "$REWARD_FILE"
}

write_reward 0.000000
TMP=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/dispatch-verifier.XXXXXX")
/bin/chmod 0711 "$TMP"
CANDIDATE_WORK_ROOT="$TMP/candidate-fixtures"
/bin/mkdir -p "$CANDIDATE_WORK_ROOT"
/bin/chmod 0733 "$CANDIDATE_WORK_ROOT"
FINALIZED=0
cleanup() { /bin/rm -rf "$TMP"; }
on_exit() {
    if [ "$FINALIZED" -ne 1 ]; then
        write_reward 0.000000 || true
    fi
    cleanup
}
trap 'on_exit' EXIT
trap 'FINALIZED=0; exit 130' HUP INT TERM
export PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

"$TRUSTED_PYTHON" -m venv "$TMP/venv"
if ! env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$TMP" TMPDIR="$TMP" LANG=C.UTF-8 \
    "$TMP/venv/bin/python" -I -m pip install --no-index --find-links "$TESTS_DIR/wheels" \
    --require-hashes -r "$TESTS_DIR/requirements-test.txt" >"$TMP/pip.log" 2>&1
then
    cat "$TMP/pip.log"
    FINALIZED=1
    write_reward 0.000000
    exit 0
fi

cat > "$TMP/run_pytest.py" <<'PY'
import os
import signal
import subprocess
import sys
mode, timeout_text, test_file, output, status_path, tmp, program_dir, candidate_work_root = sys.argv[1:9]
command = [os.path.join(tmp, 'venv', 'bin', 'python'), '-I', '-m', 'pytest', '-p', 'no:cacheprovider', '-q']
if mode == 'collect':
    command.extend(['--collect-only', test_file])
else:
    command.extend([test_file, '--junitxml=' + os.path.join(tmp, 'results.xml')])
environment = {
    'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': tmp, 'TMPDIR': tmp,
    'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
    'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PROGRAM_DIR': program_dir,
    'CANDIDATE_WORK_ROOT': candidate_work_root, 'VERIFIER_DROP_CANDIDATE_PRIVILEGES': '1',
}
with open(output, 'wb') as log:
    process = None
    try:
        process = subprocess.Popen(command, cwd=tmp, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        return_code = process.wait(timeout=float(timeout_text))
    except subprocess.TimeoutExpired:
        if process is not None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        return_code = 124
with open(status_path, 'w', encoding='ascii') as stream:
    stream.write(f'{return_code}\n')
PY

run_pytest() {
    mode=$1
    timeout_seconds=$2
    output=$3
    status_path=$4
    "$TRUSTED_PYTHON" "$TMP/run_pytest.py" "$mode" "$timeout_seconds" \
        "$TESTS_DIR/test_dispatch_audit.py" "$output" "$status_path" "$TMP" \
        "$PROGRAM_DIR" "$CANDIDATE_WORK_ROOT"
}

set +e
run_pytest collect 600 "$TMP/collect.txt" "$TMP/collect-status"
COLLECT_STATUS=$?
set -e
if [ "$COLLECT_STATUS" -ne 0 ] || [ ! -f "$TMP/collect-status" ]; then
    cat "$TMP/collect.txt" 2>/dev/null || true
    FINALIZED=1
    write_reward 0.000000
    exit 0
fi
COLLECT_RC=$(cat "$TMP/collect-status")

if [ "$COLLECT_RC" -ne 0 ]; then
    cat "$TMP/collect.txt" 2>/dev/null || true
    FINALIZED=1
    write_reward 0.000000
    exit 0
fi

set +e
run_pytest run 600 "$TMP/pytest.log" "$TMP/pytest-status"
PYTEST_WRAPPER_STATUS=$?
set -e
if [ "$PYTEST_WRAPPER_STATUS" -ne 0 ] || [ ! -f "$TMP/pytest-status" ]; then
    cat "$TMP/pytest.log" 2>/dev/null || true
    FINALIZED=1
    write_reward 0.000000
    exit 0
fi
PYTEST_STATUS=$(cat "$TMP/pytest-status")

if [ ! -s "$TMP/results.xml" ]; then
    printf '%s\n' 'INCOMPLETE: pytest completion report missing'
    FINALIZED=1
    write_reward 0.000000
    exit 0
fi

case "$PYTEST_STATUS" in
    0|1)
        ;;
    *)
        cat "$TMP/pytest.log" 2>/dev/null || true
        printf '%s\n' 'INCOMPLETE: pytest did not finish normally'
        FINALIZED=1
        write_reward 0.000000
        exit 0
        ;;
esac

"$TRUSTED_PYTHON" - "$TMP/results.xml" "$TMP/collect.txt" "$REWARD_FILE" <<'PY'
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

results_path, collect_path, reward_path = sys.argv[1:4]

GROUPS = {
    "feasibility": (
        0.28,
        (
            "test_global_reserves_scarce_courier_for_rush",
            "test_route_sequence_blocks_individually_feasible_order",
            "test_rush_fairness_override_is_bounded",
        ),
    ),
    "energy": (
        0.26,
        (
            "test_energy_reserve_defers_route_feasible_order",
            "test_energy_accumulates_across_orders",
            "test_rush_cannot_borrow_safety_reserve",
        ),
    ),
    "debt": (
        0.30,
        (
            "test_debt_orders_same_level_priority",
            "test_next_debt_is_auditable",
            "test_debt_alert_precedes_partial",
            "test_debt_goal_precedes_peak_load",
        ),
    ),
    "determinism": (
        0.08,
        (
            "test_canonical_assignment_tiebreak",
            "test_reordered_input_bytes_stable",
            "test_report_contract_and_atomic_replace",
        ),
    ),
    "validation": (
        0.08,
        (
            "test_energy_schema_missing_field_preserves_report",
            "test_energy_schema_extra_field_preserves_report",
            "test_energy_reserve_exceeds_battery_preserves_report",
            "test_ledger_must_cover_order_zones_preserves_report",
            "test_nonfinite_energy_value_preserves_report",
            "test_boolean_policy_value_preserves_report",
            "test_no_courier_reason",
        ),
    ),
}
EXPECTED = frozenset(
    name for _, tests in GROUPS.values() for name in tests
)


def atomic_write(path, text):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, temporary = tempfile.mkstemp(prefix=".reward-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def compute_reward():
    if abs(sum(weight for weight, _ in GROUPS.values()) - 1.0) > 1e-12:
        return 0.0
    try:
        root = ET.parse(results_path).getroot()
        cases = root.findall(".//testcase")
        names = [case.attrib.get("name", "") for case in cases]
        if len(names) != len(set(names)):
            return 0.0
        if set(names) != EXPECTED:
            return 0.0
        if root.find(".//collection") is not None:
            return 0.0
        outcomes = {}
        for case in cases:
            name = case.attrib["name"]
            if any(
                case.find(tag) is not None
                for tag in ("failure", "error", "skipped")
            ):
                outcomes[name] = False
            else:
                outcomes[name] = True
        with open(collect_path, "r", encoding="utf-8") as stream:
            collected_text = stream.read()
        collected_tests = [
            line.strip()
            for line in collected_text.splitlines()
            if "::" in line
        ]
        if len(collected_tests) != len(EXPECTED):
            return 0.0
        if len(collected_tests) != len(set(collected_tests)):
            return 0.0
        for name in EXPECTED:
            if not any(
                line.rsplit("::", 1)[-1] == name for line in collected_tests
            ):
                return 0.0
        total = 0.0
        for weight, tests in GROUPS.values():
            passed = sum(1 for name in tests if outcomes[name])
            total += weight * passed / len(tests)
        return max(0.0, min(1.0, total))
    except Exception:
        return 0.0


atomic_write(reward_path, f"{compute_reward():.6f}\n")
PY

cat "$TMP/pytest.log"
FINALIZED=1
exit 0
