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
    if positive and value <= 0:
        fail(f"{label} 必须为正整数")
    if nonnegative and value < 0:
        fail(f"{label} 不能为负整数")
    if maximum is not None and value > maximum:
        fail(f"{label} 超过上限")
    return value


def number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail(f"{label} 必须是有限数值")
    answer = float(value)
    if positive and answer <= 0:
        fail(f"{label} 必须为正数")
    if nonnegative and answer < 0:
        fail(f"{label} 不能为负数")
    return answer


def parse(payload):
    root = exact_dict(payload, ("max_batch_orders", "policy", "couriers", "orders"), "输入")
    batch = integer(root["max_batch_orders"], "max_batch_orders", positive=True, maximum=8)
    policy = exact_dict(root["policy"], ("max_zone_share", "urgent_slack_minutes", "max_rush_overrides_per_zone"), "policy")
    share = number(policy["max_zone_share"], "max_zone_share", positive=True)
    if share > 1:
        fail("max_zone_share 不能超过 1")
    urgent = integer(policy["urgent_slack_minutes"], "urgent_slack_minutes", nonnegative=True)
    overrides = integer(policy["max_rush_overrides_per_zone"], "max_rush_overrides_per_zone", nonnegative=True, maximum=2)
    if not isinstance(root["couriers"], list) or not root["couriers"] or len(root["couriers"]) > 4:
        fail("couriers 数量不合法")
    if not isinstance(root["orders"], list) or not root["orders"] or len(root["orders"]) > 8:
        fail("orders 数量不合法")
    courier_ids, couriers = set(), []
    for raw in root["couriers"]:
        row = exact_dict(raw, ("id", "zone", "available_at_min", "capacity_orders", "max_route_minutes"), "courier")
        ident = text(row["id"], "courier.id")
        if ident in courier_ids:
            fail("courier.id 重复")
        courier_ids.add(ident)
        couriers.append({"id": ident, "zone": text(row["zone"], "courier.zone"), "available": integer(row["available_at_min"], "courier.available_at_min", nonnegative=True), "capacity": integer(row["capacity_orders"], "courier.capacity_orders", positive=True), "route_limit": number(row["max_route_minutes"], "courier.max_route_minutes", positive=True)})
    order_ids, orders = set(), []
    for raw in root["orders"]:
        row = exact_dict(raw, ("id", "zone", "created_at_min", "prep_ready_at_min", "promised_by_min", "travel_minutes", "load", "service_level"), "order")
        ident = text(row["id"], "order.id")
        if ident in order_ids:
            fail("order.id 重复")
        order_ids.add(ident)
        level = row["service_level"]
        if level not in ("rush", "standard"):
            fail("order.service_level 不合法")
        orders.append({"id": ident, "zone": text(row["zone"], "order.zone"), "created": integer(row["created_at_min"], "order.created_at_min", nonnegative=True), "ready": integer(row["prep_ready_at_min"], "order.prep_ready_at_min", nonnegative=True), "deadline": integer(row["promised_by_min"], "order.promised_by_min", positive=True), "travel": number(row["travel_minutes"], "order.travel_minutes", positive=True), "load": integer(row["load"], "order.load", positive=True), "level": level})
    return batch, share, urgent, overrides, couriers, orders


def solve(payload):
    batch, share, urgent, override_limit, couriers, orders = parse(payload)
    orders.sort(key=lambda o: (0 if o["level"] == "rush" else 1, o["deadline"], o["created"], o["id"]))
    couriers.sort(key=lambda c: c["id"])
    cap = math.ceil(batch * share)
    zones = sorted({order["zone"] for order in orders})
    initial_couriers = {c["id"]: {"route": 0.0, "load": 0, "assigned": []} for c in couriers}
    initial_zones = {zone: {"count": 0, "overrides": 0} for zone in zones}
    best_key, best_records, best_courier_state, best_zone_state = None, None, None, None

    def consider(records, cstate, zstate):
        nonlocal best_key, best_records, best_courier_state, best_zone_state
        sent = [record for record in records if record is not None]
        rush = sum(record["level"] == "rush" for record in sent)
        maximum = max((state["load"] for state in cstate.values()), default=0)
        total_completion = sum(record["completion"] for record in sent)
        vector = tuple((1, "") if record is None else (0, record["courier_id"]) for record in records)
        key = (-rush, -len(sent), maximum, total_completion, vector)
        if best_key is None or key < best_key:
            best_key = key
            best_records = [None if r is None else dict(r) for r in records]
            best_courier_state = {ident: {"route": state["route"], "load": state["load"], "assigned": list(state["assigned"])} for ident, state in cstate.items()}
            best_zone_state = {zone: dict(state) for zone, state in zstate.items()}

    def visit(index, records, cstate, zstate, dispatched):
        if index == len(orders):
            consider(records, cstate, zstate)
            return
        order = orders[index]
        # Deferral is always possible and must be considered by the exact optimizer.
        visit(index + 1, records + [None], cstate, zstate, dispatched)
        if dispatched >= batch:
            return
        for courier in couriers:
            if courier["zone"] != order["zone"]:
                continue
            state = cstate[courier["id"]]
            start = max(courier["available"], order["ready"]) + state["route"]
            completion = start + order["travel"]
            slack = order["deadline"] - completion
            if state["load"] + order["load"] > courier["capacity"] or state["route"] + order["travel"] > courier["route_limit"] or completion > order["deadline"]:
                continue
            zone_state = zstate[order["zone"]]
            override = zone_state["count"] >= cap
            if override and (order["level"] != "rush" or slack > urgent or zone_state["overrides"] >= override_limit):
                continue
            next_couriers = {ident: {"route": current["route"], "load": current["load"], "assigned": list(current["assigned"])} for ident, current in cstate.items()}
            next_zones = {zone: dict(current) for zone, current in zstate.items()}
            next_couriers[courier["id"]]["route"] += order["travel"]
            next_couriers[courier["id"]]["load"] += order["load"]
            next_couriers[courier["id"]]["assigned"].append(order["id"])
            next_zones[order["zone"]]["count"] += 1
            if override:
                next_zones[order["zone"]]["overrides"] += 1
            record = {"id": order["id"], "courier_id": courier["id"], "zone": order["zone"], "start": start, "completion": completion, "slack": slack, "override": override, "level": order["level"]}
            visit(index + 1, records + [record], next_couriers, next_zones, dispatched + 1)

    visit(0, [], initial_couriers, initial_zones, 0)
    assert best_records is not None
    courier_zones = {c["id"]: c["zone"] for c in couriers}
    dispatched = [{"id": r["id"], "courier_id": r["courier_id"], "zone": r["zone"], "start_min": r["start"], "completion_min": r["completion"], "slack_min": r["slack"], "fairness_override": r["override"]} for r in best_records if r is not None]
    deferred = []
    known_zones = {c["zone"] for c in couriers}
    for order, record in zip(orders, best_records):
        if record is None:
            deferred.append({"id": order["id"], "reason": "NO_COURIER" if order["zone"] not in known_zones else "OPTIMIZER_DEFERRED"})
    status = "ALERT" if any(order["level"] == "rush" and record is None for order, record in zip(orders, best_records)) else ("PARTIAL" if deferred else "READY")
    final_couriers = []
    for courier in couriers:
        state = best_courier_state[courier["id"]]
        final_couriers.append({"id": courier["id"], "assigned_order_ids": state["assigned"], "assigned_load": state["load"], "route_minutes": state["route"]})
    final_zones = [{"zone": zone, "dispatched_count": best_zone_state[zone]["count"], "fairness_cap": cap, "rush_overrides": best_zone_state[zone]["overrides"]} for zone in zones]
    rush_count = sum(record is not None and order["level"] == "rush" for order, record in zip(orders, best_records))
    return {"status": status, "dispatched_orders": dispatched, "deferred_orders": deferred, "couriers": final_couriers, "zones": final_zones, "optimization": {"rush_dispatched": rush_count, "total_dispatched": len(dispatched), "max_courier_assigned_load": max((c["assigned_load"] for c in final_couriers), default=0), "total_completion_min": sum(item["completion_min"] for item in dispatched)}}


def write_atomic(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(report, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=".dispatch-audit-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.input, encoding="utf-8") as stream:
            payload = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        write_atomic(args.output, solve(payload))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
