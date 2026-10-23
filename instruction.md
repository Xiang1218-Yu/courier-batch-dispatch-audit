# 同城配送多批次全局派单审计

修复当前工作目录中的 `dispatch_audit` Python 程序：

```bash
python3 -m dispatch_audit --input INPUT.json --output OUTPUT.json
```

程序不得联网或依赖第三方 Python 包，也不得修改 `tests/`。运行环境只提供 Python 标准库。

## 输入、账本和拒收

输入 JSON 对象必须恰好包含 `max_batch_orders`、`policy`、`couriers`、`orders`、`zone_ledger`。`max_batch_orders` 表示单批最多派出的订单数量（取值 1–8），不是输入订单数上限；输入订单最多 8 笔、骑手最多 4 位、区域账本最多 8 项。布尔值不能作为数值；缺失或额外字段、重复 ID、非有限数、超出公开范围的值均须拒收。拒收必须非零退出、标准错误以 `INPUT_ERROR:` 开头，并且已有输出文件内容和其目录清单完全不变。

`policy` 恰好包括：`max_zone_share`（(0,1]）、`urgent_slack_minutes`（非负整数）、`max_rush_overrides_per_zone`（0–2）、`debt_dispatch_bonus`（0–3）和 `max_debt_after_batch`（0–12）。每项 `zone_ledger` 恰好有 `zone`、`carried_debt`，并且必须恰好覆盖本批订单出现的区域。

每位 `courier` 恰好有 `id`、`zone`、`available_at_min`、`capacity_orders`、`max_route_minutes`、`battery_minutes`、`reserve_battery_minutes`。储备不能大于电量。每笔 `order` 恰好有 `id`、`zone`、`created_at_min`、`prep_ready_at_min`、`promised_by_min`、`travel_minutes`、`load`、`service_level`（`rush` 或 `standard`）、`energy_minutes`。

## 三条必须组合的业务规则

1. **全局时效与容量可行性。** 仅同区骑手可以服务订单；骑手按全局规划顺序累积路线，开始时刻为其可用时刻加已累积路线与订单备餐时刻的较大值，完成时刻不得晚于承诺时刻。累计路线不得超过 `max_route_minutes`，已接单数和累计载重均不得超过 `capacity_orders`。区域常规派单上限为 `ceil(max_batch_orders * max_zone_share)`；只有 `rush` 订单、剩余 slack 不超过阈值且区域豁免次数未用尽时可突破。
2. **续航安全储备。** 同一骑手的 `energy_minutes` 也按派单顺序累积，且累计能耗不得超过 `battery_minutes - reserve_battery_minutes`。路线可行不代表续航可行；不得透支安全储备来完成加急订单。
3. **跨批次服务债务。** `carried_debt` 是上一批未保障服务形成的区域账本。它只用于同服务等级下的固定排序：先债务高、再承诺时刻、创建时刻、ID。给定完整方案，下一批债务为 `max(0, carried_debt + 本批该区未派数 - 本批该区已派数 * debt_dispatch_bonus)`。该账本使当前派单对下一批公平性产生可审计后果。

全局规划顺序先按 `rush`、再按上述债务排序。枚举有限候选方案后，以以下字典序目标选最优：最大化派出加急数；最大化总派出数；最小化最大下一批区域债务；最小化最大骑手累计载重；最小化完成时刻总和；最小化 `assignment_vector`。其中已派元素为 `(0,courier_id)`，未派为 `(1,"")`，按照全局规划顺序比较。

未派订单若区域没有骑手，原因是 `NO_COURIER`，否则是 `OPTIMIZER_DEFERRED`。`deferred_orders` 每项恰好包含 `id`、`reason` 两个字段，顺序固定为 `id, reason`。若最大下一批债务超过 `max_debt_after_batch`，状态为 `DEBT_ALERT`；否则若有未派加急订单为 `ALERT`；否则有未派订单为 `PARTIAL`；全派为 `READY`。

## 成功报告

成功时用 UTF-8 紧凑 JSON 原子替换输出，且只以一个换行结束。顶层字段顺序必须为：`status, dispatched_orders, deferred_orders, couriers, zones, optimization`。`dispatched_orders` 的字段顺序：`id, courier_id, zone, start_min, completion_min, slack_min, fairness_override, energy_minutes`；`couriers`：`id, assigned_order_ids, assigned_load, route_minutes, energy_used_minutes, battery_reserve_minutes`；`zones`：`zone, dispatched_count, fairness_cap, rush_overrides, carried_debt, next_debt`；`optimization`：`rush_dispatched, total_dispatched, max_next_zone_debt, max_courier_assigned_load, total_completion_min`。骑手、区域按 ID/区域升序；重排输入数组不得改变输出字节。
