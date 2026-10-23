from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path


def fail(message):
    raise ValueError(message)


def exact_dict(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        fail(f"{label} 的字段不合法")
    return value


def text(value, label):
    if not isinstance(value, str) or not value:
        fail(f"{label} 必须是非空字符串")
    return value


def integer(value, label, *, positive=False, nonnegative=False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        fail(f"{label} 必须是整数")
    if positive and value <= 0: fail(f"{label} 必须为正整数")
    if nonnegative and value < 0: fail(f"{label} 不能为负整数")
    if maximum is not None and value > maximum: fail(f"{label} 超过上限")
    return value


def number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail(f"{label} 必须是有限数值")
    result=float(value)
    if positive and result <= 0: fail(f"{label} 必须为正数")
    if nonnegative and result < 0: fail(f"{label} 不能为负数")
    return result


def parse(payload):
    root=exact_dict(payload, ('max_batch_orders','policy','couriers','orders','zone_ledger'), '输入')
    batch=integer(root['max_batch_orders'],'max_batch_orders',positive=True,maximum=8)
    policy=exact_dict(root['policy'], ('max_zone_share','urgent_slack_minutes','max_rush_overrides_per_zone','debt_dispatch_bonus','max_debt_after_batch'), 'policy')
    share=number(policy['max_zone_share'],'max_zone_share',positive=True)
    if share>1: fail('max_zone_share 不能超过 1')
    urgent=integer(policy['urgent_slack_minutes'],'urgent_slack_minutes',nonnegative=True)
    overrides=integer(policy['max_rush_overrides_per_zone'],'max_rush_overrides_per_zone',nonnegative=True,maximum=2)
    debt_bonus=integer(policy['debt_dispatch_bonus'],'debt_dispatch_bonus',nonnegative=True,maximum=3)
    debt_limit=integer(policy['max_debt_after_batch'],'max_debt_after_batch',nonnegative=True,maximum=12)
    if not isinstance(root['couriers'],list) or not root['couriers'] or len(root['couriers'])>4: fail('couriers 数量不合法')
    if not isinstance(root['orders'],list) or not root['orders'] or len(root['orders'])>8: fail('orders 数量不合法')
    if not isinstance(root['zone_ledger'],list) or len(root['zone_ledger'])>8: fail('zone_ledger 数量不合法')
    courier_ids=set(); couriers=[]
    for raw in root['couriers']:
        row=exact_dict(raw, ('id','zone','available_at_min','capacity_orders','max_route_minutes','battery_minutes','reserve_battery_minutes'), 'courier')
        ident=text(row['id'],'courier.id')
        if ident in courier_ids: fail('courier.id 重复')
        courier_ids.add(ident)
        battery=number(row['battery_minutes'],'courier.battery_minutes',nonnegative=True)
        reserve=number(row['reserve_battery_minutes'],'courier.reserve_battery_minutes',nonnegative=True)
        if reserve>battery: fail('courier.reserve_battery_minutes 不能超过 battery_minutes')
        couriers.append({'id':ident,'zone':text(row['zone'],'courier.zone'),'available':integer(row['available_at_min'],'courier.available_at_min',nonnegative=True),'capacity':integer(row['capacity_orders'],'courier.capacity_orders',positive=True),'route_limit':number(row['max_route_minutes'],'courier.max_route_minutes',positive=True),'battery':battery,'reserve':reserve})
    order_ids=set(); orders=[]
    for raw in root['orders']:
        row=exact_dict(raw, ('id','zone','created_at_min','prep_ready_at_min','promised_by_min','travel_minutes','load','service_level','energy_minutes'), 'order')
        ident=text(row['id'],'order.id')
        if ident in order_ids: fail('order.id 重复')
        order_ids.add(ident)
        level=row['service_level']
        if level not in ('rush','standard'): fail('order.service_level 不合法')
        orders.append({'id':ident,'zone':text(row['zone'],'order.zone'),'created':integer(row['created_at_min'],'order.created_at_min',nonnegative=True),'ready':integer(row['prep_ready_at_min'],'order.prep_ready_at_min',nonnegative=True),'deadline':integer(row['promised_by_min'],'order.promised_by_min',positive=True),'travel':number(row['travel_minutes'],'order.travel_minutes',positive=True),'load':integer(row['load'],'order.load',positive=True),'level':level,'energy':number(row['energy_minutes'],'order.energy_minutes',positive=True)})
    ledger={}
    for raw in root['zone_ledger']:
        row=exact_dict(raw, ('zone','carried_debt'), 'zone_ledger item')
        zone=text(row['zone'],'zone_ledger.zone')
        if zone in ledger: fail('zone_ledger.zone 重复')
        ledger[zone]=integer(row['carried_debt'],'zone_ledger.carried_debt',nonnegative=True,maximum=12)
    order_zones={x['zone'] for x in orders}
    if set(ledger)!=order_zones: fail('zone_ledger 必须恰好覆盖订单区域')
    return batch,share,urgent,overrides,debt_bonus,debt_limit,couriers,orders,ledger


def solve(payload):
    batch,share,urgent,override_limit,debt_bonus,debt_limit,couriers,orders,ledger=parse(payload)
    # 债务高的区域在同服务等级和承诺时刻下优先，防止跨批次长期饥饿。
    orders.sort(key=lambda o:(0 if o['level']=='rush' else 1,-ledger[o['zone']],o['deadline'],o['created'],o['id']))
    couriers.sort(key=lambda c:c['id'])
    zones=sorted(ledger)
    cap=math.ceil(batch*share)
    initial_c={c['id']:{'route':0.0,'energy':0.0,'load':0,'assigned':[]} for c in couriers}
    initial_z={z:{'count':0,'overrides':0} for z in zones}
    best=None
    def key(records,cstate,zstate):
        assigned=[r for r in records if r is not None]
        rush=sum(r['level']=='rush' for r in assigned)
        # 账本轴：先满足有限批次目标，再最小化下一批的最大区域债务。
        next_debt={z:max(0,ledger[z]+sum(o['zone']==z for o,r in zip(orders,records) if r is None)-zstate[z]['count']*debt_bonus) for z in zones}
        max_debt=max(next_debt.values())
        peak=max((s['load'] for s in cstate.values()),default=0)
        completion=sum(r['completion'] for r in assigned)
        vector=tuple((1,'') if r is None else (0,r['courier_id']) for r in records)
        return (-rush,-len(assigned),max_debt,peak,completion,vector),next_debt
    def visit(index,records,cstate,zstate,dispatched):
        nonlocal best
        if index==len(orders):
            score,next_debt=key(records,cstate,zstate)
            if best is None or score<best[0]: best=(score,list(records),cstate,zstate,next_debt)
            return
        order=orders[index]
        # Always permit defer; global objectives decide whether it is appropriate.
        visit(index+1,records+[None],cstate,zstate,dispatched)
        if dispatched>=batch: return
        for courier in couriers:
            if courier['zone']!=order['zone']: continue
            old=cstate[courier['id']]
            if len(old['assigned'])>=courier['capacity'] or old['load']+order['load']>courier['capacity']: continue
            route=old['route']+order['travel']; energy=old['energy']+order['energy']
            if route>courier['route_limit'] or energy>courier['battery']-courier['reserve']: continue
            start=max(courier['available']+old['route'],order['ready'])
            completion=start+order['travel']; slack=order['deadline']-completion
            if completion>order['deadline']: continue
            zone=zstate[order['zone']]
            override=False
            if zone['count']>=cap:
                override=order['level']=='rush' and slack<=urgent and zone['overrides']<override_limit
                if not override: continue
            nc={ident:{'route':v['route'],'energy':v['energy'],'load':v['load'],'assigned':list(v['assigned'])} for ident,v in cstate.items()}
            nz={zone_name:dict(v) for zone_name,v in zstate.items()}
            nc[courier['id']].update(route=route,energy=energy,load=old['load']+order['load'])
            nc[courier['id']]['assigned'].append(order['id'])
            nz[order['zone']]['count']+=1
            if override: nz[order['zone']]['overrides']+=1
            record={'id':order['id'],'courier_id':courier['id'],'zone':order['zone'],'start':start,'completion':completion,'slack':slack,'override':override,'level':order['level'],'energy':order['energy']}
            visit(index+1,records+[record],nc,nz,dispatched+1)
    visit(0,[],initial_c,initial_z,0)
    assert best is not None
    _,records,cstate,zstate,next_debt=best
    dispatched=[]; deferred=[]; known={c['zone'] for c in couriers}
    for order,record in zip(orders,records):
        if record is None: deferred.append({'id':order['id'],'reason':'NO_COURIER' if order['zone'] not in known else 'OPTIMIZER_DEFERRED'})
        else: dispatched.append({'id':record['id'],'courier_id':record['courier_id'],'zone':record['zone'],'start_min':record['start'],'completion_min':record['completion'],'slack_min':record['slack'],'fairness_override':record['override'],'energy_minutes':record['energy']})
    if max(next_debt.values())>debt_limit: status='DEBT_ALERT'
    elif any(o['level']=='rush' and r is None for o,r in zip(orders,records)): status='ALERT'
    elif deferred: status='PARTIAL'
    else: status='READY'
    final_c=[{'id':c['id'],'assigned_order_ids':cstate[c['id']]['assigned'],'assigned_load':cstate[c['id']]['load'],'route_minutes':cstate[c['id']]['route'],'energy_used_minutes':cstate[c['id']]['energy'],'battery_reserve_minutes':c['battery']-cstate[c['id']]['energy']} for c in couriers]
    final_z=[{'zone':z,'dispatched_count':zstate[z]['count'],'fairness_cap':cap,'rush_overrides':zstate[z]['overrides'],'carried_debt':ledger[z],'next_debt':next_debt[z]} for z in zones]
    return {'status':status,'dispatched_orders':dispatched,'deferred_orders':deferred,'couriers':final_c,'zones':final_z,'optimization':{'rush_dispatched':sum(r is not None and o['level']=='rush' for o,r in zip(orders,records)),'total_dispatched':len(dispatched),'max_next_zone_debt':max(next_debt.values()),'max_courier_assigned_load':max((c['assigned_load'] for c in final_c),default=0),'total_completion_min':sum(x['completion_min'] for x in dispatched)}}


def write_atomic(path,report):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    encoded=(json.dumps(report,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode()
    fd,name=tempfile.mkstemp(prefix='.dispatch-audit-',dir=str(path.parent))
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        os.replace(name,path)
    except BaseException:
        try: os.unlink(name)
        except FileNotFoundError: pass
        raise

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument('--input',required=True); parser.add_argument('--output',required=True); args=parser.parse_args(argv)
    try:
        with open(args.input,encoding='utf-8') as stream: payload=json.load(stream,parse_constant=lambda value:(_ for _ in ()).throw(ValueError(value)))
        write_atomic(args.output,solve(payload))
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(f'INPUT_ERROR: {exc}',file=__import__('sys').stderr); return 2
    return 0
if __name__=='__main__': raise SystemExit(main())
