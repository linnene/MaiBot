# 虚拟旅行生活

角色按现实时间推进的虚拟旅行：规划、模拟订票与酒店、出发、交通、游玩、返程。旅行经费是独立记账的虚拟人民币，不涉及真实支付、预订、身份申请或下单。

## 组成

- 主程序（本仓，分支 `feat/virtual-travel`）：`src/services/virtual_travel_memory.py` 把已发生的自身经历接到现有 `memory_service.ingest_summary`，用 `virtual-travel:<event_id>` 去重。
- 旅行插件（独立仓库，分支 `feat/virtual-travel-plugin`，ID `local.virtual-travel`）：账本、行程状态机、每月契机、内部规划、只读投影、投递箱、发布边界与只读 WebUI。
- 麦生活（分支 `feat/virtual-travel-life`）：每轮维护取一次 `get_travel_projection`，日程、状态时间线、当前位置和聊天背景共用同一快照。
- 搜索插件（分支 `feat/virtual-travel-search`）：新增只读 `search_travel_facts`，只返回来源与抓取时间，不进入发送路径。

## 数据与状态

旅行插件在自身数据目录使用 `travel.sqlite3`，表包括：月费、预留、账目、行程、行程段、已发生事件、事件确认、每月契机、发布队列。所有时间以 UTC epoch 保存，展示使用 IANA 时区。

- 每月按 `Asia/Shanghai` 月初结算，随机 15,000—25,000 元，余额累积；同月只入账一次。
- 只有已到时间且已推进的事件才是自身经历；计划与检索资料不是经历，也不会写成用户经历。
- 长期记忆导出与主动游记均默认关闭；开启需要显式修改插件配置。

## 只读接口

| API | 用途 |
| --- | --- |
| `get_travel_projection(as_of, end)` | 时间窗口投影：阶段、唯一当前位置、行程段、健康状态 |
| `get_completed_travel_events(limit)` | 已发生且未确认的自身经历 |
| `ack_travel_events(event_ids)` | 确认经历已被接收 |
| `propose_travel_share` / `before_send_travel_share` / `after_send_travel_share` | 游记发布排队与发送前后校验 |
| `status` | 只读余额、行程、阶段与最近决策（不作为跨插件 API） |

## 测试

- 插件：`cd local_virtual_travel && .venv/bin/python -m unittest discover -s tests`
- 搜索插件：`cd google-search && .venv/bin/python -m unittest discover -s tests`
- 麦生活：`PYTHONPATH=<包含 Mai_life 的目录> python -m unittest discover -s Mai_life/tests`
- 主程序桥接：`python -m pytest pytests/services/test_virtual_travel_memory.py -q`

## 上线与回滚

本批只推功能分支与插件锁，不部署。上线时先备份源码、插件锁、私有配置与一致性 SQLite；部署后核对服务、插件锁、`get_travel_projection` 与麦生活日程投影。回滚源码时保留当前聊天、记忆与旅行数据库，不用旧库覆盖。

## 已知限制

- 目的地天气未在本批抓取；旅行期间环境快照标记 `weather_scope=destination_unknown`，不使用家乡天气描述当地实况。
- 真实群游记发送链路未接入；代码只提供白名单与回执校验，需另行授权后接 Host 主动任务。
- 模型是否主动使用旅行工具、故事自然度需要实际观察，离线测试不能证明。
