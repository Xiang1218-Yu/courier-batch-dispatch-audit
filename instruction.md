# 同城配送全局批次派单审计

配送平台每隔若干分钟运行一次批次派单。请修复 `environment/dispatch_audit` 中的 Python 程序。入口：

```bash
python3 -m dispatch_audit --input INPUT.json --output OUTPUT.json
```

程序不得联网或依赖第三方 Python 包，也不得修改 `tests/`。

## 输入与拒收语义

输入为 JSON 对象，且必须**恰好**包含 `max_batch_orders`、`policy`、`couriers`、`orders`。

- `max_batch_orders`：正整数，且不大于 8；本轮最多派出订单数。
- `policy`：对象，且恰好含：
  - `max_zone_share`：有限数，范围 `(0, 1]`；
  - `urgent_slack_minutes`：非负整数；
  - `max_rush_overrides_per_zone`：非负整数，且不大于 2。
- `couriers`：非空数组、至多 4 位。每位骑手含唯一非空字符串 `id`、非空字符串 `zone`、非负整数 `available_at_min`、正整数 `capacity_orders`、正有限数 `max_route_minutes`。
- `orders`：非空数组、至多 8 笔。每笔订单含唯一非空字符串 `id`、非空字符串 `zone`、非负整数 `created_at_min` 和 `prep_ready_at_min`、正整数 `promised_by_min`、正有限数 `travel_minutes`、正整数 `load`，以及 `service_level`（`"rush"` 或 `"standard"`）。

布尔值不能作为数值。缺失键、额外键、错误类型、非有限数、越界值及重复 ID 均须拒收。拒收时以非零状态退出，标准错误以 `INPUT_ERROR:` 开头；若输出文件已存在，其字节内容和所在目录文件清单必须完全不变。

## 固定服务顺序与可行计划

先得到全局稳定规划顺序：`rush` 在 `standard` 前，随后按 `promised_by_min`、`created_at_min`、`id` 升序。一个计划把每笔订单分给同区的一位骑手，或标记为不派；同一骑手按这个全局规划顺序服务其被分到的订单。

对骑手 `c` 的一笔已分订单 `o`，其中 `route_minutes`、`assigned_load` 为该骑手在规划顺序中此前订单累积出的状态：

```text
start_min      = max(c.available_at_min, o.prep_ready_at_min) + route_minutes
completion_min = start_min + o.travel_minutes
slack_min      = o.promised_by_min - completion_min
```

一笔已分订单必须同时满足：

```text
assigned_load + o.load <= c.capacity_orders
route_minutes + o.travel_minutes <= c.max_route_minutes
completion_min <= o.promised_by_min
```

派出总数不能超过 `max_batch_orders`。区域常规派单上限为：

```text
fairness_cap = ceil(max_batch_orders × policy.max_zone_share)
```

某区域前 `fairness_cap` 笔已分订单为常规派单。超过上限的订单仅可为 `rush`，并且它在该计划中的 `slack_min <= urgent_slack_minutes`，同时该区域的超限加急数不超过 `max_rush_overrides_per_zone`；这种订单的 `fairness_override` 为 `true`。所有其他已分订单的该字段为 `false`。

## 必须求全局最优，而非逐笔贪心

在所有可行计划中，按以下目标**依次**比较，取字典序最小的目标元组：

```text
(-rush_dispatched,
 -total_dispatched,
 max_courier_assigned_load,
 total_completion_min,
 assignment_vector)
```

其中：

- `rush_dispatched` 是派出的加急订单数；`total_dispatched` 是派出总数；
- `max_courier_assigned_load` 是所有骑手最终 `assigned_load` 的最大值；
- `total_completion_min` 是所有派出订单 `completion_min` 的和；
- `assignment_vector` 按全局规划顺序列出。已分订单元素为 `(0, courier_id)`，未分订单为 `(1, "")`，按 Python 元组字典序比较。

因此，局部最早完成、局部最低负载或按输入顺序分配，都不足以满足题意。必须在有限输入范围内枚举/搜索出全局最优计划。

未派订单若其区域没有任何骑手，原因是 `NO_COURIER`；否则原因为 `OPTIMIZER_DEFERRED`。`status`：存在未派 `rush` 时为 `ALERT`；否则存在任何未派订单时为 `PARTIAL`；没有未派订单时为 `READY`。

## 成功报告

成功时以 UTF-8 JSON **原子替换**输出文件，紧凑序列化且只以一个换行结束。顶层字段及顺序必须为：

```text
status, dispatched_orders, deferred_orders, couriers, zones, optimization
```

- `dispatched_orders` 按全局规划顺序；每项字段顺序为 `id, courier_id, zone, start_min, completion_min, slack_min, fairness_override`。
- `deferred_orders` 按全局规划顺序；每项字段顺序为 `id, reason`。
- `couriers` 按 `id` 升序；每项字段顺序为 `id, assigned_order_ids, assigned_load, route_minutes`。
- `zones` 按 `zone` 升序，覆盖输入订单出现的每个区域；每项字段顺序为 `zone, dispatched_count, fairness_cap, rush_overrides`。
- `optimization` 字段顺序为 `rush_dispatched, total_dispatched, max_courier_assigned_load, total_completion_min`。

重排输入 `couriers` 或 `orders` 不得改变输出字节。
