# 小埃虚拟旅行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in this session. If the user chooses delegation instead, use superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** 完成有账本、有现实时间进度、能影响麦生活并被角色正确记住的虚拟旅行闭环；主动分享另设白名单开关。

**Architecture:** 独立旅行领域模块负责账本、计划、事实与事件，SDK插件负责模型工具、定时驱动和只读接口。麦生活消费版本化投影，不让两个插件互写数据库；主程序仅提供必要的有来源记忆交接。

**Tech Stack:** 现有Python、SQLite、asyncio、maibot-sdk、现有搜索/天气/模型路由；不增加账号、付费服务或生图依赖。

**Spec:** docs/superpowers/specs/2026-10-31-virtual-travel-design.md

## Global Constraints

- 基线为ecc027a18676550cd7e2e1e1260dc96c19fa4186；开始执行时重新核实本地、远端、线上及插件锁，不在main上提交。
- 建议执行分支feat/virtual-travel；独立插件ID local.virtual-travel。下述T为独立仓库plugins/local_virtual-travel；L为麦生活独立仓库Mai_life；H为主程序。开发时使用隔离工作区，对应发布快照保持独立Git历史。
- 经费每月随机15,000—25,000元人民币、可累积；均为虚拟资金；金额存整数分。模型不能增发或透支，禁止真实支付/预订。
- 工程默认：Asia/Shanghai每月1日结算；均匀抽整元；启用当月首笔入账、初始余额零、不补安装前月份。启用后离线跨月补账并去重。
- 每月一个持久化随机契机，不强制旅行；平时允许自主提出意愿。每分钟轻量轮询，未到期不调用模型。内部决策沿用deepseek-flash路由，新增决策上限8次/日，单次至多一次模型请求；失败停留待考虑，不忙循环。
- 时间以UTC保存、IANA时区展示；出境资格可以显式模拟。人设、可信身份、既有短回复、私聊保密不改。
- 普通私聊/获准聊天可自然谈旅行；主动游记只在显式白名单群中开展，默认关闭且白名单空。不顺带启用QQ空间、网页发布或图片发送。
- 私有配置、账号、原始聊天、资料认证信息与数据库不入Git；不借回滚覆盖当前聊天/记忆。

## Review Focus

- 跨月、系统时间校正或重复启动必须不重复发钱；任务“账本”测试。
- 改期时旧模型结果、旧天气与旧发送任务不能推进新行程；任务“行程”与“联动/分享”测试。
- 时差和航班途中必须只有一个当前位置，不因当地日历切换重复过一天；任务“行程”测试。
- 长期记忆写入回执丢失时不能重复导出，且计划不能变成用户经历；任务“记忆”测试。
- 旅行插件停用/掉线不能瞬间回学校，群退白名单后排队消息不能继续发；任务“联动/分享”测试。

---

## 账本：独立可重启的虚拟经济

**Files:** T/domain/models.py、T/domain/ledger.py、T/storage.py、T/config.py、T/_manifest.json、T/tests/test_ledger.py。

**Interfaces:** Ledger.credit_month(month: str, amount_yuan: int, now: float) -> dict；reserve(trip_id: str, key: str, amount_fen: int, expires_at: float) -> dict；settle(key: str, now: float) -> dict；release(key: str, now: float) -> dict；refund(payment_key: str, key: str, amount_fen: int, now: float) -> dict；balance() -> dict。返回available_fen/reserved_fen/spent_fen；数据库操作通过to_thread，不阻塞事件循环。

- [ ] 写失败测试：同月并发credit仅一笔、抽取区间端点、首次当月与离线跨月、预留/消费/释放/退款幂等、可用资金不为负。断言credit后重启读取同一金额，不再次抽取。
- [ ] 在T运行`python -m unittest tests.test_ledger`，确认因缺少接口失败。
- [ ] 实现版本化SQLite表及事务；生产随机数仅在无当月记录时生成并原子提交，测试注入确定值；退款总额不得超过原支付，过期预留不能结算。
- [ ] 重跑该文件，检查全部通过；配置包含Host必需的plugin.config_version，默认不启用。
- [ ] 独立提交`feat: add persistent virtual travel ledger`，不提交配置和数据库。

## 行程：可取消的状态机和时间推进

**Files:** T/domain/trips.py、T/domain/clock.py、T/domain/opportunities.py、T/tests/test_trips.py、T/tests/test_clock.py。

**Interfaces:** TripService.propose(title: str, now: float) -> dict；revise(trip_id: str, expected_revision: int, patch: dict, now: float) -> dict；confirm(trip_id: str, expected_revision: int, now: float) -> dict；cancel(trip_id: str, expected_revision: int, now: float) -> dict；advance(now: float) -> list[dict]；projection(as_of: float, end: float) -> dict。契机接口ensure_month(month: str, now: float) -> dict，同月一个稳定时刻。

- [ ] 写失败测试：尚未到达不生成已发生事件；取消/改期后旧revision拒绝；UTC跨日、东京/纽约夏令时与跨日期线航程只有一个当前位置；重启不重发契机、不重复扣款；离线恢复只推进已确定段，不虚构缺失细节。
- [ ] 运行`python -m unittest tests.test_trips tests.test_clock`确认失败。
- [ ] 实现阶段idea/research/draft/confirmed/preparing/in_transit/staying/returning/completed/cancelled，限制一个活动旅程；时间段不重叠、结束晚于开始。确定硬行程必须有相应资金预留；合法开始时才结算预留。
- [ ] 测试全部通过；虚拟资格存source_kind=virtual_qualification，不伪装真实订单。记录旅行取消不等于替用户取消真实约定。
- [ ] 提交`feat: add time-aware autonomous trip lifecycle`。

## 决策与资料：让她能自己规划

**Files:** T/research.py、T/planner.py、T/plugin.py、T/tests/test_planner.py；搜索插件plugin.py新增只读search_travel_facts API及tests/test_travel_api.py。

**Interfaces:** ResearchClient.search(query: str, now: float) -> list[dict]，每条有url/fetched_at/content/source_kind；Planner.consider(now: float, snapshot: dict) -> dict；插件工具travel_plan支持status/propose/revise/confirm/postpone/cancel。模型只提议，所有改变调用前两项的领域接口验证。

- [ ] 写失败测试：搜索正文包含“忽略规则/转账”不变成指令；无结果不假装查到票；模型超预算、错误时区、过期revision及连续无效请求被拒绝；没有待处理意愿时不调用模型；普通工具调用不直接发送QQ。
- [ ] 分别运行T的`python -m unittest tests.test_planner`及搜索插件对应测试，确认失败。
- [ ] 给现有handle_web_search提供仅返回资料的API包装，拒绝调用发送路径；复用既有provider，不新增搜索账号。天气按目的地坐标/时区查询，不能修改家乡全局配置。模型请求附简洁人设兴趣、预算、既有计划和当前时间，不含原始私聊。
- [ ] 确认请求有截止时间和每日预算；Mock失败必须返回显式状态。测试通过后检查工具和API通过真实SDK注册，而非只测手写字典。
- [ ] 提交T和搜索独立仓库改动，记录两个SHA。

## 联动：麦生活采用同一份旅行投影

**Files:** T/projection.py、L/life/travel_projection.py、L/life/schedule_service.py、L/core/environment.py、L/plugin.py；T/tests/test_projection.py、L/tests/test_travel_projection.py。

**Interfaces:** 插件公开只读get_travel_projection(as_of: float, end: float) -> dict，schema_version=1，字段trip_id/revision/valid_from/valid_until/phase/location/timezone/segments/source_kind/health。L的TravelProjectionClient.snapshot(now: datetime, end: datetime) -> dict，以api.call获取并缓存同一revision。

- [ ] 写失败测试：context/state_timeline/ensure_day/expand_due和环境都读取同一快照；旅行覆盖学生回落模板；飞行中不提前落地；返程后恢复；网络失败保留有效快照，过期标降级而非瞬移；停用冻结活动旅程，不偷偷清除行程。
- [ ] 在T与L分别运行对应测试，确认失败。
- [ ] 维护循环每轮只取一次快照，传给当轮相关服务；旅行硬段覆盖普通模板，剩余时间仍由麦生活安排吃饭/休息和自由活动。目的地天气随快照传递，失败保留来源日期并显示未知项。
- [ ] 跑麦生活现有日程、生活状态、公共投影测试和新增用例。不能只改聊天提示而遗漏状态推进与天气消费者。
- [ ] 提交L与T改动，不跨仓写对方数据库。

## 记忆：保留自己的虚拟经历及其归属

**Files:** T/memory_outbox.py、T/tests/test_memory_outbox.py；H/src/services/virtual_travel_memory.py、H/pytests/services/test_virtual_travel_memory.py；必要的Host上下文调用点在实现前通过现有memory_service与编译器测试锁定。

**Interfaces:** T只读get_completed_travel_events(cursor: str, limit: int=20) -> dict；确认接口ack_travel_events(event_ids: list[str]) -> dict。H的VirtualTravelMemoryBridge.flush(now: float) -> dict，从锁定插件获取并验证已发生事件，调用现有memory_service.ingest_summary；external_id固定virtual-travel:<event_id>。明确记录subject=bot/source_kind=virtual_self_event/event_at/timezone/trip_id，不产生用户人物画像。

- [ ] 写失败测试：未来计划/资料不得导出；重复拉取与写入回执未知不重复记忆；哥哥问“你去了哪里”能召回她的旅行，询问用户行程不把旅行移给用户；来源、日期与私聊保护在Planner到Replyer交接中保留。
- [ ] 运行T和H对应测试确认失败；阅读A_memorix/MODIFICATION_POLICY.md，不能直接修改实现层绕过Host服务。
- [ ] 首先用合成实例证明现有ingest_summary external_id的去重与来源保留；若不足，先写出明确的最小Host修复，不通过多次批量写线上来试。长记忆导出独立开关，默认关闭；旅行档案和按需查询始终可用。
- [ ] 原有人设/可见回复/归属/互动记忆回归通过；模拟成功但ack丢失仍仅一条有效事件。只有用户批准启用导出时才向生产记忆写入。
- [ ] 提交T/H改动，保留关闭适配而不删除现有记忆的回滚路径。

## 分享和观测：只读状态、明确发布边界

**Files:** T/webui.json、T/publishing.py、T/tests/test_webui.py、T/tests/test_publishing.py；H已有发送来源交接仅在不兼容时最小扩展。

**Interfaces:** status() -> dict只读返回余额/行程/下个事件/资料状态/失败；Publishing.propose(event_id: str, group_session: str, now: float) -> dict；before_send逐段验证真实群会话、当前白名单、revision与过期时间，after_send只有平台回执才结算。

- [ ] 写失败测试：普通私聊自然问答可读取摘要；非白名单群不产生主动游记；退白名单、取消行程、改期、重载和引用上一分段都撤销旧发送；未知回执不重试；公开投影无私人聊天字段。
- [ ] 运行`python -m unittest tests.test_webui tests.test_publishing`确认失败。
- [ ] 沿用Host主动任务与逐段来源机制，不直接调用适配器发消息；默认auto_share=false与空白名单。前端只读、无手动替她发话/改账按钮；由Host schema做实际校验。
- [ ] 配图与网页帖子暂不包含在本批实现；使用合成文字和回执验证，不以排队等同到达。未获得具体群配置与真实发送授权不做QQ测试。
- [ ] 提交独立插件改动并记录未启用能力，避免把“代码存在”说成“已自动分享”。

## 发布：先可回滚试用，再验收自然性

**Files:** H/deployment/plugin-lock.json、H/docs/virtual_travel.md；各仓README与部署记录。

- [ ] 检查所有执行分支、未提交变更与源锁，备份当前配置、部署记录、源码锚点和一致性数据库；运行全套目标测试及跨模块集成测试，不消除存量失败证据。
- [ ] 独立复审领域约束和联动边界，修复确定问题；将H/T/L/搜索源码先推用户自己的功能分支，再更新并推精确插件锁。
- [ ] 先在合成实例跑完一次准备—出发—停留—返程及一次取消。检查源码回滚时数据库继续前进，不重新抽月费或清空旅程。
- [ ] 配置与上线时只启用已确认范围，默认不启用自动游记与长期记忆批量导出；不引入新费用/服务依赖，不顺带修改人设或PushPlus定时器。按需重启，核对服务、SDK工具、API、插件锁和实际日程投影。
- [ ] 报告实际SHA、测试结果、资料能力的限制与待观察行为。模型是否主动采纳工具、旅程是否有趣须实际观察，不能由离线单测宣称。

## 自审与交接

经费、时间、资料、日程、记忆、分享边界和回滚均有对应任务；五项Review Focus均落入测试。保留网页/图片与真实订单作为显式非目标。上述工程默认是实现提案，执行时不能暗中扩张成已授权的QQ群发送或生产记忆批量写入。

建议本会话逐项实现，接口串行稳定后做一次独立全分支复审；用户也可选择逐任务子代理实施与复审。等待用户审阅计划并确定执行方式后开始编码。
