import copy
import json
import math
import os
import subprocess
import sys

PROGRAM_DIR=os.environ["PROGRAM_DIR"]

def courier(ident, *, zone="north", available=0, capacity=4, route=60):
    return {"id":ident,"zone":zone,"available_at_min":available,"capacity_orders":capacity,"max_route_minutes":route}

def order(ident, *, zone="north", deadline=40, travel=10, load=1, level="standard", created=0, ready=0):
    return {"id":ident,"zone":zone,"created_at_min":created,"prep_ready_at_min":ready,"promised_by_min":deadline,"travel_minutes":travel,"load":load,"service_level":level}

def payload(*, batch=4, share=.5, slack=3, overrides=1, couriers=None, orders=None):
    return {"max_batch_orders":batch,"policy":{"max_zone_share":share,"urgent_slack_minutes":slack,"max_rush_overrides_per_zone":overrides},"couriers":couriers or [courier("c")],"orders":orders or [order("o")]}

def decoded(tmp_path, data):
    inp=tmp_path/"input.json"; out=tmp_path/"report.json"
    inp.write_text(json.dumps(data),encoding="utf-8")
    run=subprocess.run([sys.executable,"-m","dispatch_audit","--input",str(inp),"--output",str(out)],cwd=PROGRAM_DIR,capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    return json.loads(out.read_text(encoding="utf-8")),out

def test_global_optimizer_reserves_scarce_courier_for_later_rush(tmp_path):
    """O1：局部最快选择 a 会堵死后续加急；全局解必须把 flex 分给 b。"""
    data=payload(batch=2,share=1,couriers=[courier("a",available=0),courier("b",available=5)],orders=[order("flex",deadline=30,level="rush"),order("scarce",deadline=12,level="rush",ready=0)])
    # b cannot serve scarce by 12, but can serve flex; a must remain for scarce.
    report,_=decoded(tmp_path,data)
    by_id={x["id"]:x["courier_id"] for x in report["dispatched_orders"]}
    assert by_id=={"scarce":"a","flex":"b"} and report["optimization"]["rush_dispatched"]==2

def test_global_optimizer_maximizes_rush_before_more_standard_orders(tmp_path):
    """O2：一个加急的优先级高于多个常规订单，不能只最大化订单数量。"""
    data=payload(batch=2,share=1,couriers=[courier("a",capacity=2)],orders=[order("rush",deadline=10,travel=10,load=2,level="rush"),order("std-a",deadline=30,load=1),order("std-b",deadline=40,load=1)])
    report,_=decoded(tmp_path,data)
    assert [x["id"] for x in report["dispatched_orders"]]==["rush"]
    assert report["optimization"]["rush_dispatched"]==1 and report["optimization"]["total_dispatched"]==1

def test_global_optimizer_minimizes_peak_load_after_dispatch_count(tmp_path):
    """O3：即使集中给快骑手会降低总完成时刻，也必须先最小化最大骑手载荷。"""
    data=payload(batch=2,share=1,couriers=[courier("fast",capacity=4,available=0),courier("slow",capacity=4,available=20)],orders=[order("one",load=2,deadline=40),order("two",deadline=50,load=2)])
    report,_=decoded(tmp_path,data)
    # 集中给 fast 的总完成时间更小（30），但峰值载荷为 4；规范目标要求先选峰值 2 的分配。
    assert report["optimization"]["max_courier_assigned_load"]==2
    assert {x["courier_id"] for x in report["dispatched_orders"]}=={"fast","slow"}
    assert report["optimization"]["total_completion_min"]==40

def test_global_optimizer_minimizes_total_completion_before_canonical_tiebreak(tmp_path):
    """O4：最大载荷同分时，以总完成时刻而非输入顺序决定计划。"""
    data=payload(batch=3,share=1,couriers=[courier("slow",available=10),courier("fast",available=0)],orders=[order("one",deadline=60),order("two",deadline=70),order("three",deadline=80)])
    report,_=decoded(tmp_path,data)
    # 三笔、两位骑手时最小峰值载荷为 2；在该前提下 fast 承担前两笔才使总完成时间最小。
    assert report["optimization"]["max_courier_assigned_load"]==2
    assert report["optimization"]["total_completion_min"]==50
    assert [x["courier_id"] for x in report["dispatched_orders"]]==["fast","fast","slow"]

def test_canonical_assignment_vector_breaks_perfect_tie(tmp_path):
    """O5：所有数值目标同分时使用题设 assignment_vector，选择字典序较小骑手。"""
    data=payload(batch=1,share=1,couriers=[courier("z"),courier("a")],orders=[order("x")])
    report,_=decoded(tmp_path,data)
    assert report["dispatched_orders"]==[{"id":"x","courier_id":"a","zone":"north","start_min":0.0,"completion_min":10.0,"slack_min":30.0,"fairness_override":False}]

def test_fairness_cap_allows_only_imminent_bounded_rush_override(tmp_path):
    """O6：超限加急需同时满足余量阈值和区域豁免额度。"""
    data=payload(batch=4,share=.5,slack=2,overrides=1,orders=[order("r1",deadline=10,level="rush"),order("r2",deadline=20,level="rush"),order("r3",deadline=32,level="rush"),order("r4",deadline=42,level="rush")])
    report,_=decoded(tmp_path,data)
    assert [x["id"] for x in report["dispatched_orders"]]==["r1","r2","r3"]
    assert report["dispatched_orders"][-1]["fairness_override"] is True
    assert report["deferred_orders"]==[{"id":"r4","reason":"OPTIMIZER_DEFERRED"}]

def test_zone_cap_can_force_cross_zone_global_tradeoff(tmp_path):
    """O7：区域普通上限约束参与全局最优，空余批次位不能非法填满单一区域。"""
    data=payload(batch=3,share=.5,couriers=[courier("n",zone="north"),courier("s",zone="south")],orders=[order("n1"),order("n2",deadline=50),order("n3",deadline=60),order("s1",zone="south",deadline=70)])
    report,_=decoded(tmp_path,data)
    assert [x["id"] for x in report["dispatched_orders"]]==["n1","n2","s1"]
    assert report["zones"]==[{"zone":"north","dispatched_count":2,"fairness_cap":2,"rush_overrides":0},{"zone":"south","dispatched_count":1,"fairness_cap":2,"rush_overrides":0}]

def test_route_sequence_can_make_a_plan_infeasible(tmp_path):
    """O8：同一骑手按全局顺序累积路线；不能把各订单独立地判为可行。"""
    data=payload(batch=2,share=1,couriers=[courier("a",route=15)],orders=[order("first",deadline=10,travel=8),order("later",deadline=16,travel=8)])
    report,_=decoded(tmp_path,data)
    assert [x["id"] for x in report["dispatched_orders"]]==["first"]
    assert report["deferred_orders"]==[{"id":"later","reason":"OPTIMIZER_DEFERRED"}]

def test_no_courier_is_distinguished_from_optimizer_deferral(tmp_path):
    """O9：只有无同区骑手使用 NO_COURIER；其余未选用全局延后原因。"""
    data=payload(couriers=[courier("n")],orders=[order("missing",zone="south",level="rush"),order("present")])
    report,_=decoded(tmp_path,data)
    assert report["status"]=="ALERT"
    assert report["deferred_orders"]==[{"id":"missing","reason":"NO_COURIER"}]

def test_reordered_input_produces_identical_bytes(tmp_path):
    """O10：输入数组重排不得改变搜索结果或紧凑 JSON 字节。"""
    base=payload(batch=3,share=1,couriers=[courier("b"),courier("a")],orders=[order("b",deadline=50),order("a",deadline=40),order("r",deadline=20,level="rush")])
    _,first=decoded(tmp_path,base)
    changed=copy.deepcopy(base); changed["couriers"].reverse(); changed["orders"].reverse()
    _,second=decoded(tmp_path,changed)
    assert first.read_bytes()==second.read_bytes()

def test_report_schema_order_and_compact_newline(tmp_path):
    """O11：所有公开字段顺序、紧凑 JSON 和末尾换行均受契约约束。"""
    report,out=decoded(tmp_path,payload())
    raw=out.read_bytes()
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n") and b", " not in raw
    assert list(report)==["status","dispatched_orders","deferred_orders","couriers","zones","optimization"]
    assert list(report["dispatched_orders"][0])==["id","courier_id","zone","start_min","completion_min","slack_min","fairness_override"]
    assert list(report["zones"][0])==["zone","dispatched_count","fairness_cap","rush_overrides"]

def assert_rejected_unchanged(tmp_path,data):
    out=tmp_path/"report.json"; inp=tmp_path/"input.json"
    out.write_bytes(b"SENTINEL")
    inp.write_text(json.dumps(data,allow_nan=True),encoding="utf-8")
    before=sorted(p.name for p in tmp_path.iterdir())
    run=subprocess.run([sys.executable,"-m","dispatch_audit","--input",str(inp),"--output",str(out)],cwd=PROGRAM_DIR,capture_output=True,text=True)
    assert run.returncode!=0 and run.stderr.startswith("INPUT_ERROR:")
    assert out.read_bytes()==b"SENTINEL" and sorted(p.name for p in tmp_path.iterdir())==before

def test_rejects_new_policy_fields_and_planning_limits_without_side_effects(tmp_path):
    """O12：新增策略字段及枚举规模上限均须严格校验且不改写既有报告。"""
    wrong=payload(); del wrong["policy"]["max_rush_overrides_per_zone"]
    assert_rejected_unchanged(tmp_path,wrong)
    too_many=payload(orders=[order(str(i),deadline=100+i) for i in range(9)])
    assert_rejected_unchanged(tmp_path,too_many)

def test_rejects_boolean_nonfinite_and_extra_fields_without_side_effects(tmp_path):
    """O13：布尔伪数值、非有限数和未声明字段都必须被拒收。"""
    boolean=payload(); boolean["policy"]["max_rush_overrides_per_zone"]=True
    assert_rejected_unchanged(tmp_path,boolean)
    nonfinite=payload(); nonfinite["orders"][0]["travel_minutes"]=math.inf
    assert_rejected_unchanged(tmp_path,nonfinite)
    extra=payload(); extra["orders"][0]["unexpected"]=1
    assert_rejected_unchanged(tmp_path,extra)

def test_atomic_success_replaces_sentinel_without_temporary_files(tmp_path):
    """O14：成功写入须原子替换，不能留下命名临时文件。"""
    out=tmp_path/"report.json"; out.write_bytes(b"OLD")
    _,out=decoded(tmp_path,payload())
    assert out.read_bytes()!=b"OLD" and not [p for p in tmp_path.iterdir() if p.name.startswith(".dispatch-audit-")]
