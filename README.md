# task26100330

语言学习平台站内通知的投递调度库（Python + SQLite）。

## 用法

```python
from notification_dispatch import NotificationDispatcher

disp = NotificationDispatcher("notifications.db")

# 设置偏好：IANA 时区、本地墙钟免打扰窗口 [start, end)、各类别每日上限。
# 新偏好只对严格晚于 now 的时刻生效；未设置过偏好的学习者按
# UTC、无免打扰、不限量处理。
disp.set_preferences(
    "learner-1", "America/New_York", "22:00", "07:00",
    {"streak": 3},            # 未列出的类别不限量
    now=1_772_000_000,
)

event_id = disp.emit("learner-1", "streak", now=1_772_000_100)

new_entries = disp.dispatch(now=1_772_001_000)   # 本次新写入收件箱的条目
inbox = disp.inbox("learner-1")                  # 按投递时刻、再按 emit 顺序
disp.close()
```

所有时刻均为 UTC 整数秒；所有调用的 `now` 必须单调不减；调用方单线程。
`dnd_start > dnd_end` 表示跨午夜窗口，两者相等表示没有免打扰。

## 设计要点

- 每条通知的投递时刻只由事件发生时刻、偏好历史、同类别前一条的投递时刻
  和当日已投递条数决定，与 `dispatch` 被调用的时机无关；因此中途任意
  插入 `dispatch`、或关闭重开数据库，最终收件箱逐条相同。
- 待投递通知存于 `pending` 表；`dispatch` 只扫描待投递队列，按
  （学习者, 类别）独立排队，遇到第一条投递时刻晚于 `now` 的通知即停止
  该队列，不重放已投递历史。每日频控通过
  `(learner_id, category, local_date)` 索引上的 `COUNT` 完成。
- 时区一律通过 `zoneinfo` 按时刻换算，从不固化为固定 UTC 偏移，也不把
  本地一天当作 24 小时。免打扰出口、下一个本地日期的起点都通过
  “墙钟到达目标时刻的最早 UTC 秒”直接计算：正常/重复（歧义）时刻用
  fold 偏移直接求解，不存在（春季跳过）的时刻在单转换区间内二分定位，
  绝不按固定步长扫描时间轴。

## 测试

```
python3 -m pytest -q
```

- `tests/test_timeutil.py` / `tests/test_dst.py`：纽约春秋季切换、
  圣地亚哥午夜切换（不存在的本地午夜、25 小时的一天、重复小时第二遍
  仍被免打扰推迟、上限顺延到下一个本地日期的第一个时刻）。
- `tests/test_determinism.py`：随机偏好变更（改时区、改上限）与事件
  序列跨夏令时切换，分别按“每次都 dispatch / 只在最后 dispatch /
  随机时刻 dispatch 且中途关闭重开数据库”三种方式运行，断言收件箱
  逐条相同。
- `tests/test_performance.py`：单学习者上万条积压一次投递。
