# notify-scheduler

语言学习平台站内通知的投递调度库（Python + SQLite，仅标准库）。

## API

所有时刻为 UTC 整数秒；调用方单线程、`now` 单调不减。

```python
from notify_scheduler import Scheduler

s = Scheduler("notifications.db")          # 文件路径或 ":memory:"
s.set_preferences("learner", "America/New_York", "22:00", "07:00",
                  {"streak": 2}, now)      # 仅对严格晚于 now 的时刻生效
event_id = s.emit("learner", "streak", now)
new_items = s.dispatch(now)                # 写入收件箱，返回本次新条目
items = s.inbox("learner")                 # 按投递时刻、emit 顺序排序
s.close()                                  # 可随时关闭重开，结果不变
```

## 设计

- **投递时刻在 emit 时确定**：`deliver_at` 只依赖偏好历史与同类别队列，
  因此 dispatch 的调用时机、次数、数据库中途关闭重开都不影响结果。
- **同类别队列**：每条通知的投递时刻不早于同类别前一条（按 emit 顺序）。
- **时区/DST**：用 `zoneinfo` 直接求解墙钟时刻对应的 UTC 瞬间
  （`tzutil.py`），空隙（春季跳过）用二分定位过渡瞬间，重复时段
  （秋季折叠）枚举 fold=0/1 两个候选；不固化 UTC 偏移，不固定步长扫描。
- **每日频控**：按投递时刻生效的时区记本地日期，超上限则顺延到下一个
  本地日期的第一个时刻（正确处理 Santiago 午夜切换）。
- **性能**：dispatch 走 `(delivered, deliver_at)` 索引，只触及到期未投递
  行，不重放历史；频控计数与队首查询均走索引。

## 测试

```bash
python3 -m pytest test_notify_scheduler.py -q
```

覆盖：NY 春季空隙中的免打扰结束、NY 秋季重复小时的二次推迟、Santiago
午夜切换两天的顺延、以及随机偏好变更/事件序列在三种 dispatch 时机
（含中途关闭重开数据库）下收件箱逐条一致。
