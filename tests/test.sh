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
export PYTHONDONTWRITEBYTECODE=1
python3 -m venv "$TMP/venv" || exit 1
"$TMP/venv/bin/python" -m pip install --no-index --find-links "$TESTS_DIR/wheels" --require-hashes -r "$TESTS_DIR/requirements-test.txt" || exit 1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest --collect-only -q "$TESTS_DIR/test_dispatch_audit.py" >"$TMP/collect.txt" 2>&1 || exit 1
set +e
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$TMP/venv/bin/python" -m pytest -p no:cacheprovider -q "$TESTS_DIR/test_dispatch_audit.py" --junitxml="$TMP/results.xml"
status=$?
set -e
python3 - "$TMP/results.xml" "$REWARD_FILE" <<'PY2'
import sys, xml.etree.ElementTree as ET
groups={
 'test_global_optimizer_reserves_scarce_courier_for_later_rush':'optimization','test_global_optimizer_maximizes_rush_before_more_standard_orders':'optimization','test_global_optimizer_minimizes_peak_load_after_dispatch_count':'optimization','test_global_optimizer_minimizes_total_completion_before_canonical_tiebreak':'optimization','test_canonical_assignment_vector_breaks_perfect_tie':'optimization','test_fairness_cap_allows_only_imminent_bounded_rush_override':'constraints','test_zone_cap_can_force_cross_zone_global_tradeoff':'constraints','test_route_sequence_can_make_a_plan_infeasible':'constraints','test_no_courier_is_distinguished_from_optimizer_deferral':'constraints','test_reordered_input_produces_identical_bytes':'output','test_report_schema_order_and_compact_newline':'output','test_rejects_new_policy_fields_and_planning_limits_without_side_effects':'validation','test_rejects_boolean_nonfinite_and_extra_fields_without_side_effects':'validation','test_atomic_success_replaces_sentinel_without_temporary_files':'validation'}
weights={'optimization':.42,'constraints':.28,'output':.12,'validation':.18}
try:
 root=ET.parse(sys.argv[1]).getroot(); seen={}
 for case in root.findall('.//testcase'):
  name=case.attrib.get('name',''); seen[name]=case.find('failure') is None and case.find('error') is None and case.find('skipped') is None
 score=0.0 if set(seen)!=set(groups) else sum(weights[group]*sum(seen[name] for name in groups if groups[name]==group)/sum(1 for name in groups if groups[name]==group) for group in weights)
except Exception: score=0.0
with open(sys.argv[2],'w',encoding='utf-8') as stream: stream.write(f'{score:.6f}\n')
PY2
exit "$status"
