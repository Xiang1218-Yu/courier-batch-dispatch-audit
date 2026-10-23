#!/bin/sh
set -u
TESTS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$TESTS_DIR" = "/tests" ]; then
  REWARD_FILE=${REWARD_FILE:-/logs/verifier/reward.txt}
  export PROGRAM_DIR=${PROGRAM_DIR:-/app}
else
  ROOT=$(CDPATH= cd -- "$TESTS_DIR/.." && pwd)
  REWARD_FILE=${REWARD_FILE:-"$ROOT/reward.txt"}
  export PROGRAM_DIR=${PROGRAM_DIR:-"$ROOT/environment"}
fi
mkdir -p "$(dirname -- "$REWARD_FILE")"
printf '0.000000\n' > "$REWARD_FILE"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1
python3 -m venv "$TMP/venv" || exit 1
"$TMP/venv/bin/python" -m pip install --no-index --find-links "$TESTS_DIR/wheels" --require-hashes -r "$TESTS_DIR/requirements-test.txt" || exit 1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest --collect-only -q "$TESTS_DIR/test_dispatch_audit.py" >"$TMP/collect.txt" 2>&1 || exit 1
set +e
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest -p no:cacheprovider -q "$TESTS_DIR/test_dispatch_audit.py" --junitxml="$TMP/results.xml"
rc=$?
set -e
python3 - "$TMP/results.xml" "$REWARD_FILE" <<'PY2'
import sys, xml.etree.ElementTree as ET
groups={
 'test_global_reserves_scarce_courier_for_rush':'feasibility',
 'test_route_sequence_blocks_individually_feasible_order':'feasibility',
 'test_rush_fairness_override_is_bounded':'feasibility',
 'test_energy_reserve_rejects_route_feasible_order':'energy',
 'test_energy_accumulates_across_orders':'energy',
 'test_rush_cannot_borrow_safety_reserve':'energy',
 'test_debt_orders_same_level_priority':'debt',
 'test_next_debt_is_auditable':'debt',
 'test_debt_alert_precedes_partial':'debt',
 'test_debt_goal_precedes_peak_load':'debt',
 'test_canonical_assignment_tiebreak':'determinism',
 'test_reordered_input_bytes_stable':'determinism',
 'test_schema_compact_and_atomic_success':'determinism',
 'test_rejects_energy_schema_and_reserve_bounds':'validation',
 'test_rejects_ledger_coverage_and_nonfinite':'validation',
 'test_no_courier_reason':'validation'}
weights={'feasibility':.28,'energy':.26,'debt':.30,'determinism':.08,'validation':.08}
try:
 root=ET.parse(sys.argv[1]).getroot(); seen={}
 for case in root.findall('.//testcase'):
  name=case.attrib.get('name','')
  if name in groups: seen[name]=case.find('failure') is None and case.find('error') is None and case.find('skipped') is None
 score=0.0 if set(seen)!=set(groups) else sum(weights[g]*sum(seen[n] for n in groups if groups[n]==g)/sum(1 for n in groups if groups[n]==g) for g in weights)
except Exception: score=0.0
with open(sys.argv[2],'w',encoding='utf-8') as f: f.write(f'{score:.6f}\n')
PY2
exit "$rc"
