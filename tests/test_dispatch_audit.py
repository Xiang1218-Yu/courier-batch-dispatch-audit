import copy,json,math,os,subprocess,sys
from pathlib import Path
PROGRAM_DIR=Path(os.environ.get('PROGRAM_DIR',Path(__file__).resolve().parents[1]/'environment'))

def courier(i,zone='north',available=0,capacity=4,route=60,battery=60,reserve=5): return {'id':i,'zone':zone,'available_at_min':available,'capacity_orders':capacity,'max_route_minutes':route,'battery_minutes':battery,'reserve_battery_minutes':reserve}
def order(i,zone='north',deadline=50,travel=10,load=1,level='standard',created=0,ready=0,energy=10): return {'id':i,'zone':zone,'created_at_min':created,'prep_ready_at_min':ready,'promised_by_min':deadline,'travel_minutes':travel,'load':load,'service_level':level,'energy_minutes':energy}
def payload(*,batch=4,share=1,slack=3,overrides=1,bonus=1,limit=12,couriers=None,orders=None,ledger=None):
 orders=orders or [order('o')]; zones=sorted({o['zone'] for o in orders})
 return {'max_batch_orders':batch,'policy':{'max_zone_share':share,'urgent_slack_minutes':slack,'max_rush_overrides_per_zone':overrides,'debt_dispatch_bonus':bonus,'max_debt_after_batch':limit},'couriers':couriers or [courier('c')],'orders':orders,'zone_ledger':ledger if ledger is not None else [{'zone':z,'carried_debt':0} for z in zones]}
def run(tmp,data):
 inp=tmp/'input.json'; out=tmp/'report.json'; inp.write_text(json.dumps(data,allow_nan=True)); p=subprocess.run([sys.executable,'-m','dispatch_audit','--input',str(inp),'--output',str(out)],cwd=PROGRAM_DIR,capture_output=True,text=True); return p,out
def decoded(tmp,data):
 p,out=run(tmp,data); assert p.returncode==0,p.stderr; return json.loads(out.read_text()),out

def test_global_reserves_scarce_courier_for_rush(tmp_path):
 """F1：全局目标先最大化加急完成数。"""
 d=payload(batch=2,couriers=[courier('shared'),courier('north',zone='north')],orders=[order('standard'),order('rush',zone='north',deadline=15,level='rush')])
 r,_=decoded(tmp_path,d); assert {x['id'] for x in r['dispatched_orders']}=={'standard','rush'} and next(x for x in r['dispatched_orders'] if x['id']=='rush')['courier_id']=='north'
def test_route_sequence_blocks_individually_feasible_order(tmp_path):
 """F2：路线按规划顺序累积。"""
 r,_=decoded(tmp_path,payload(batch=2,couriers=[courier('a',route=15)],orders=[order('a',deadline=10,travel=8),order('b',deadline=16,travel=8)])); assert [x['id'] for x in r['dispatched_orders']]==['a']
def test_rush_fairness_override_is_bounded(tmp_path):
 """F3：仅紧迫加急可有限突破区域上限。"""
 r,_=decoded(tmp_path,payload(batch=3,share=.33,slack=5,overrides=1,couriers=[courier('c')],orders=[order('r1',deadline=20,level='rush'),order('r2',deadline=25,level='rush'),order('r3',deadline=40,level='rush')]))
 assert len(r['dispatched_orders'])==2 and r['zones'][0]['rush_overrides']==1
def test_energy_reserve_rejects_route_feasible_order(tmp_path):
 """E1：路线可行但透支电量储备时必须延后。"""
 r,_=decoded(tmp_path,payload(couriers=[courier('c',battery=15,reserve=5)],orders=[order('a',energy=10),order('b',deadline=70,energy=1)])); assert [x['id'] for x in r['dispatched_orders']]==['a']
def test_energy_accumulates_across_orders(tmp_path):
 """E2：电量按骑手接单序列累计。"""
 r,_=decoded(tmp_path,payload(batch=2,couriers=[courier('c',battery=24,reserve=4)],orders=[order('a',energy=10),order('b',deadline=70,energy=11)])); assert [x['id'] for x in r['dispatched_orders']]==['a']
def test_rush_cannot_borrow_safety_reserve(tmp_path):
 """E3：加急订单同样不能突破安全储备。"""
 r,_=decoded(tmp_path,payload(couriers=[courier('c',battery=10,reserve=5)],orders=[order('r',level='rush',energy=6)])); assert r['status']=='ALERT' and not r['dispatched_orders']
def test_debt_orders_same_level_priority(tmp_path):
 """D1：同等级订单按区域历史债务排序。"""
 d=payload(batch=1,couriers=[courier('n'),courier('s','south')],orders=[order('north'),order('south','south')],ledger=[{'zone':'north','carried_debt':0},{'zone':'south','carried_debt':4}])
 r,_=decoded(tmp_path,d); assert [x['id'] for x in r['dispatched_orders']]==['south']
def test_next_debt_is_auditable(tmp_path):
 """D2：下批债务结合延后订单与派单抵扣计算。"""
 d=payload(batch=1,bonus=2,couriers=[courier('n')],orders=[order('a'),order('b',deadline=60)],ledger=[{'zone':'north','carried_debt':3}]); r,_=decoded(tmp_path,d); assert r['zones'][0]['next_debt']==2
def test_debt_alert_precedes_partial(tmp_path):
 """D3：超出债务警戒线产生独立运营状态。"""
 d=payload(batch=1,limit=1,couriers=[courier('n')],orders=[order('a'),order('b',deadline=60)],ledger=[{'zone':'north','carried_debt':3}]); r,_=decoded(tmp_path,d); assert r['status']=='DEBT_ALERT'
def test_debt_goal_precedes_peak_load(tmp_path):
 """D4：在完成数相同时优先降低最大下一批债务。"""
 d=payload(batch=2,bonus=1,couriers=[courier('n',capacity=2),courier('s','south',capacity=2)],orders=[order('n1'),order('n2',deadline=60),order('s1','south',deadline=70)],ledger=[{'zone':'north','carried_debt':4},{'zone':'south','carried_debt':0}]); r,_=decoded(tmp_path,d); assert {x['id'] for x in r['dispatched_orders']}=={'n1','n2'}
def test_canonical_assignment_tiebreak(tmp_path):
 """F4：等价方案按骑手 ID 给出稳定指派。"""
 r,_=decoded(tmp_path,payload(couriers=[courier('z'),courier('a')],orders=[order('o')])); assert r['dispatched_orders'][0]['courier_id']=='a'
def test_reordered_input_bytes_stable(tmp_path):
 """F5：输入数组重排不得影响报告字节。"""
 d=payload(batch=2,couriers=[courier('b'),courier('a')],orders=[order('b'),order('a',deadline=40)]); _,x=decoded(tmp_path,d); d['couriers'].reverse(); d['orders'].reverse(); _,y=decoded(tmp_path,d); assert x.read_bytes()==y.read_bytes()
def test_schema_compact_and_atomic_success(tmp_path):
 """F6：成功报告字段、紧凑格式和原子替换均固定。"""
 out=tmp_path/'report.json';out.write_bytes(b'OLD');r,out=decoded(tmp_path,payload()); raw=out.read_bytes(); assert list(r)==['status','dispatched_orders','deferred_orders','couriers','zones','optimization'] and b', ' not in raw and raw.endswith(b'\n') and not list(tmp_path.glob('.dispatch-audit-*'))
def rejected(tmp,d):
 out=tmp/'report.json';out.write_bytes(b'SENTINEL'); p,_=run(tmp,d); assert p.returncode and p.stderr.startswith('INPUT_ERROR:') and out.read_bytes()==b'SENTINEL' and sorted(x.name for x in tmp.iterdir())==['input.json','report.json']
def test_rejects_energy_schema_and_reserve_bounds(tmp_path):
 """V1：电量字段缺失、额外字段及储备越界均安全拒绝。"""
 decoded(tmp_path,payload())
 d=payload();del d['couriers'][0]['battery_minutes'];rejected(tmp_path,d); d=payload();d['couriers'][0]['reserve_battery_minutes']=99;rejected(tmp_path,d);d=payload();d['orders'][0]['x']=1;rejected(tmp_path,d)
def test_rejects_ledger_coverage_and_nonfinite(tmp_path):
 """V2：账本必须覆盖订单区域且拒绝非有限数与布尔伪数值。"""
 decoded(tmp_path,payload())
 d=payload();d['zone_ledger']=[];rejected(tmp_path,d);d=payload();d['orders'][0]['energy_minutes']=math.inf;rejected(tmp_path,d);d=payload();d['policy']['debt_dispatch_bonus']=True;rejected(tmp_path,d)
def test_no_courier_reason(tmp_path):
 """V3：无同区骑手与优化延后原因可区分。"""
 r,_=decoded(tmp_path,payload(couriers=[courier('n')],orders=[order('s','south',level='rush'),order('n')])); assert r['deferred_orders'][0]=={'id':'s','reason':'NO_COURIER'}
