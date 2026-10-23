import json
import os
import subprocess
from pathlib import Path

PROGRAM_DIR = Path(os.environ.get('PROGRAM_DIR', Path(__file__).resolve().parents[1] / 'environment'))
CANDIDATE_PYTHON = os.environ.get('CANDIDATE_PYTHON', '/usr/local/bin/python3')
RUNNER = (
    "import runpy, sys\n"
    "program = sys.argv[1]\n"
    "sys.path.insert(0, program)\n"
    "sys.argv = ['dispatch_audit', *sys.argv[2:]]\n"
    "runpy.run_module('dispatch_audit', run_name='__main__')\n"
)


def courier(i, zone='north', available=0, capacity=4, route=60, battery=60, reserve=5):
    return {'id': i, 'zone': zone, 'available_at_min': available, 'capacity_orders': capacity,
            'max_route_minutes': route, 'battery_minutes': battery,
            'reserve_battery_minutes': reserve}


def order(i, zone='north', deadline=50, travel=10, load=1, level='standard', created=0, ready=0, energy=10):
    return {'id': i, 'zone': zone, 'created_at_min': created, 'prep_ready_at_min': ready,
            'promised_by_min': deadline, 'travel_minutes': travel, 'load': load,
            'service_level': level, 'energy_minutes': energy}


def payload(*, batch=4, share=1, slack=3, overrides=1, bonus=1, limit=12, couriers=None, orders=None, ledger=None):
    orders = orders if orders is not None else [order('o')]
    zones = sorted({o['zone'] for o in orders})
    return {'max_batch_orders': batch,
            'policy': {'max_zone_share': share, 'urgent_slack_minutes': slack,
                       'max_rush_overrides_per_zone': overrides, 'debt_dispatch_bonus': bonus,
                       'max_debt_after_batch': limit},
            'couriers': couriers if couriers is not None else [courier('c')],
            'orders': orders,
            'zone_ledger': ledger if ledger is not None else [{'zone': z, 'carried_debt': 0} for z in zones]}


def invoke(tmp, data):
    inp = tmp / 'input.json'
    out = tmp / 'report.json'
    inp.write_text(json.dumps(data, allow_nan=True))
    environment = {
        'PATH': '/usr/local/bin:/usr/bin:/bin',
        'HOME': str(tmp),
        'TMPDIR': str(tmp),
        'PYTHONDONTWRITEBYTECODE': '1',
        'LANG': 'C.UTF-8',
    }
    process = subprocess.run(
        [CANDIDATE_PYTHON, '-I', '-c', RUNNER, str(PROGRAM_DIR),
         '--input', str(inp), '--output', str(out)],
        cwd=tmp, env=environment, capture_output=True, text=True,
    )
    return process, out


def test_global_reserves_scarce_courier_for_rush(tmp_path):
    """R1：全局目标先最大化加急完成数，并保留同区骑手完成加急单。"""
    data = payload(batch=2,
                   couriers=[courier('shared'), courier('north', zone='north')],
                   orders=[order('standard'), order('rush', zone='north', deadline=15, level='rush')])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert {item['id'] for item in report['dispatched_orders']} == {'standard', 'rush'}
    assert next(item for item in report['dispatched_orders'] if item['id'] == 'rush')['courier_id'] == 'north'


def test_route_sequence_blocks_individually_feasible_order(tmp_path):
    """R2：路线按全局规划顺序累积，单笔可行不代表组合可行。"""
    data = payload(batch=2, couriers=[courier('a', route=15)],
                   orders=[order('a', deadline=10, travel=8), order('b', deadline=16, travel=8)])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert [item['id'] for item in report['dispatched_orders']] == ['a']


def test_rush_fairness_override_is_bounded(tmp_path):
    """R3：仅紧迫加急单可突破区域上限，且豁免次数有界。"""
    data = payload(batch=3, share=.33, slack=5, overrides=1, couriers=[courier('c')],
                   orders=[order('r1', deadline=20, level='rush'),
                           order('r2', deadline=25, level='rush'),
                           order('r3', deadline=40, level='rush')])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert len(report['dispatched_orders']) == 2
    assert report['zones'][0]['rush_overrides'] == 1


def test_energy_reserve_defers_route_feasible_order(tmp_path):
    """R4：路线可行但透支电量储备时必须延后。"""
    data = payload(couriers=[courier('c', battery=15, reserve=5)],
                   orders=[order('a', energy=10), order('b', deadline=70, energy=1)])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert [item['id'] for item in report['dispatched_orders']] == ['a']


def test_energy_accumulates_across_orders(tmp_path):
    """R5：电量按同一骑手接单序列累计，而不是逐单独立检查。"""
    data = payload(batch=2, couriers=[courier('c', battery=24, reserve=4)],
                   orders=[order('a', energy=10), order('b', deadline=70, energy=11)])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert [item['id'] for item in report['dispatched_orders']] == ['a']


def test_rush_cannot_borrow_safety_reserve(tmp_path):
    """R6：加急订单同样不能突破安全储备，未派时状态为 ALERT。"""
    data = payload(couriers=[courier('c', battery=10, reserve=5)],
                   orders=[order('r', level='rush', energy=6)])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert report['status'] == 'ALERT'
    assert not report['dispatched_orders']


def test_debt_orders_same_level_priority(tmp_path):
    """R7：同服务等级订单按区域历史债务排序。"""
    data = payload(batch=1, couriers=[courier('n'), courier('s', 'south')],
                   orders=[order('north'), order('south', 'south')],
                   ledger=[{'zone': 'north', 'carried_debt': 0}, {'zone': 'south', 'carried_debt': 4}])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert [item['id'] for item in report['dispatched_orders']] == ['south']


def test_next_debt_is_auditable(tmp_path):
    """R8：下一批债务结合延后订单与派单抵扣计算。"""
    data = payload(batch=1, bonus=2, couriers=[courier('n')],
                   orders=[order('a'), order('b', deadline=60)],
                   ledger=[{'zone': 'north', 'carried_debt': 3}])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert report['zones'][0]['next_debt'] == 2


def test_debt_alert_precedes_partial(tmp_path):
    """R9：超过债务警戒线时，状态优先报告 DEBT_ALERT。"""
    data = payload(batch=1, limit=1, couriers=[courier('n')],
                   orders=[order('a'), order('b', deadline=60)],
                   ledger=[{'zone': 'north', 'carried_debt': 3}])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert report['status'] == 'DEBT_ALERT'


def test_debt_goal_precedes_peak_load(tmp_path):
    """R10：完成数相同时优先降低最大下一批区域债务。"""
    data = payload(batch=2, bonus=1,
                   couriers=[courier('n', capacity=2), courier('s', 'south', capacity=2)],
                   orders=[order('n1'), order('n2', deadline=60), order('s1', 'south', deadline=70)],
                   ledger=[{'zone': 'north', 'carried_debt': 4}, {'zone': 'south', 'carried_debt': 0}])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert {item['id'] for item in report['dispatched_orders']} == {'n1', 'n2'}


def test_canonical_assignment_tiebreak(tmp_path):
    """R11：等价方案按骑手 ID 给出稳定指派。"""
    data = payload(couriers=[courier('z'), courier('a')], orders=[order('o')])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert report['dispatched_orders'][0]['courier_id'] == 'a'


def test_reordered_input_bytes_stable(tmp_path):
    """R12：输入数组重排不得改变输出字节。"""
    data = payload(batch=2, couriers=[courier('b'), courier('a')],
                   orders=[order('b'), order('a', deadline=40)])
    first_process, first_out = invoke(tmp_path, data)
    assert first_process.returncode == 0, first_process.stderr
    first_bytes = first_out.read_bytes()
    data['couriers'].reverse()
    data['orders'].reverse()
    second_process, second_out = invoke(tmp_path, data)
    assert second_process.returncode == 0, second_process.stderr
    assert first_bytes == second_out.read_bytes()


def test_report_contract_and_atomic_replace(tmp_path):
    """R13：成功报告字段顺序、紧凑格式、换行和原子替换均固定。"""
    out = tmp_path / 'report.json'
    out.write_bytes(b'OLD')
    process, out = invoke(tmp_path, payload())
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    raw = out.read_bytes()
    assert list(report) == ['status', 'dispatched_orders', 'deferred_orders', 'couriers', 'zones', 'optimization']
    assert list(report['optimization']) == ['rush_dispatched', 'total_dispatched', 'max_next_zone_debt',
                                            'max_courier_assigned_load', 'total_completion_min']
    assert b', ' not in raw
    assert raw.endswith(b'\n')
    assert not list(tmp_path.glob('.dispatch-audit-*'))


def test_energy_schema_missing_field_preserves_report(tmp_path):
    """R14：缺少电量字段必须拒收，且旧报告与目录清单不变。"""
    initial_process, initial_out = invoke(tmp_path, payload())
    assert initial_process.returncode == 0, initial_process.stderr
    assert initial_out.exists()
    data = payload()
    del data['couriers'][0]['battery_minutes']
    initial_out.write_bytes(b'SENTINEL')
    before_names = sorted(path.name for path in tmp_path.iterdir())
    process, out = invoke(tmp_path, data)
    assert process.returncode != 0
    assert process.stderr.startswith('INPUT_ERROR:')
    assert out.read_bytes() == b'SENTINEL'
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names


def test_energy_schema_extra_field_preserves_report(tmp_path):
    """R15：订单出现额外字段必须拒收，且旧报告与目录清单不变。"""
    initial_process, initial_out = invoke(tmp_path, payload())
    assert initial_process.returncode == 0, initial_process.stderr
    assert initial_out.exists()
    data = payload()
    data['orders'][0]['x'] = 1
    initial_out.write_bytes(b'SENTINEL')
    before_names = sorted(path.name for path in tmp_path.iterdir())
    process, out = invoke(tmp_path, data)
    assert process.returncode != 0
    assert process.stderr.startswith('INPUT_ERROR:')
    assert out.read_bytes() == b'SENTINEL'
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names


def test_energy_reserve_exceeds_battery_preserves_report(tmp_path):
    """R16：安全储备大于总电量必须拒收，且旧报告与目录清单不变。"""
    initial_process, initial_out = invoke(tmp_path, payload())
    assert initial_process.returncode == 0, initial_process.stderr
    assert initial_out.exists()
    data = payload()
    data['couriers'][0]['reserve_battery_minutes'] = 99
    initial_out.write_bytes(b'SENTINEL')
    before_names = sorted(path.name for path in tmp_path.iterdir())
    process, out = invoke(tmp_path, data)
    assert process.returncode != 0
    assert process.stderr.startswith('INPUT_ERROR:')
    assert out.read_bytes() == b'SENTINEL'
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names


def test_ledger_must_cover_order_zones_preserves_report(tmp_path):
    """R17：区域账本未覆盖订单区域必须拒收，且旧报告与目录清单不变。"""
    initial_process, initial_out = invoke(tmp_path, payload())
    assert initial_process.returncode == 0, initial_process.stderr
    assert initial_out.exists()
    data = payload()
    data['zone_ledger'] = []
    initial_out.write_bytes(b'SENTINEL')
    before_names = sorted(path.name for path in tmp_path.iterdir())
    process, out = invoke(tmp_path, data)
    assert process.returncode != 0
    assert process.stderr.startswith('INPUT_ERROR:')
    assert out.read_bytes() == b'SENTINEL'
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names


def test_nonfinite_energy_value_preserves_report(tmp_path):
    """R18：非有限电量数值必须拒收，且旧报告与目录清单不变。"""
    initial_process, initial_out = invoke(tmp_path, payload())
    assert initial_process.returncode == 0, initial_process.stderr
    assert initial_out.exists()
    data = payload()
    data['orders'][0]['energy_minutes'] = float('inf')
    initial_out.write_bytes(b'SENTINEL')
    before_names = sorted(path.name for path in tmp_path.iterdir())
    process, out = invoke(tmp_path, data)
    assert process.returncode != 0
    assert process.stderr.startswith('INPUT_ERROR:')
    assert out.read_bytes() == b'SENTINEL'
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names


def test_boolean_policy_value_preserves_report(tmp_path):
    """R19：布尔值伪装整数策略参数必须拒收，且旧报告与目录清单不变。"""
    initial_process, initial_out = invoke(tmp_path, payload())
    assert initial_process.returncode == 0, initial_process.stderr
    assert initial_out.exists()
    data = payload()
    data['policy']['debt_dispatch_bonus'] = True
    initial_out.write_bytes(b'SENTINEL')
    before_names = sorted(path.name for path in tmp_path.iterdir())
    process, out = invoke(tmp_path, data)
    assert process.returncode != 0
    assert process.stderr.startswith('INPUT_ERROR:')
    assert out.read_bytes() == b'SENTINEL'
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names


def test_no_courier_reason(tmp_path):
    """R20：无同区骑手与优化延后原因可区分，且 deferred 字段顺序固定。"""
    data = payload(couriers=[courier('n')],
                   orders=[order('s', 'south', level='rush'), order('n')])
    process, out = invoke(tmp_path, data)
    assert process.returncode == 0, process.stderr
    report = json.loads(out.read_text())
    assert list(report['deferred_orders'][0]) == ['id', 'reason']
    assert report['deferred_orders'][0] == {'id': 's', 'reason': 'NO_COURIER'}
