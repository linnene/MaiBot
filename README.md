# 兴趣阅读与自然分享

独立 MaiBot SDK 2.12 插件 `local.entertainment`。仅操作本机小红书 MCP 的只读白名单：`check_login_status`、`list_feeds`、`search_feeds`、`get_feed_detail`。不调用点赞、收藏、评论、关注或发布工具。不安装浏览器，不登录账号，不包含 cookies。

默认所有联网/真实聊天任务关闭。通过 SDK 原生配置页面修改本插件：`plugin.enabled=true` 开放资料服务；`reading.auto_browse=true` 才巡检并阅读。默认兴趣为摄影、书籍、绘画、烹饪、旅行、美食。搜索只取配置兴趣，不接受聊天搜索词。`reading.preview=true` 只准备草稿，不创建聊天任务；阅读本身仍可联网和调用模型，次数受 `daily_sessions` / `max_items` 限制。`daily_sessions=0` 完全禁止会话；失败也计入 UTC 日预算，重启不重置。

闲置条件必须从 `maibot-community.mai-life.get_life_state` 返回的 `current_activity` 精确命中 `reading.allowed_idle_activities`。API 缺失、错误、空状态、工作等非允许活动都不阅读。麦生活的活动是动态场景描述，默认精确列表可能完全不命中；先查看其实际状态再配置需要允许的场景，不要把通配符当空闲证明。状态 API 默认不给读取路径添入用户资料。

先浏览推荐列表，推荐为空时按兴趣搜索；兴趣和当前简洁人格用于阅读判断，搜索只接收兴趣词。

本机 sidecar 由部署者单独安装并让用户扫码登录；配置 `reading.endpoint`（默认 `http://127.0.0.1:18060/mcp`）。只接受回环 HTTP 地址，禁代理与重定向。插件使用 Streamable HTTP initialize/session-id/JSON/SSE；每次 RPC 有总截止时间和 1 MiB 响应上限。登录状态可识别 `is_logged_in=true` 或官方文本“已登录”；搜索和详情结构不匹配时忽略，不猜测内容。

阅读材料明确标记 `source_kind=external_reading`。模型可记住感想，也可忽略；空感想不能进入候选。原文只在内存参与判定，不保存原始 sidecar 数据、路由 token、cookies、聊天、QQ 号或用户事实。SQLite 位于 SDK 数据目录 `readings.sqlite3`，包含至多 2000 条标题、公共链接、作者、观察时间、阅读感想和源关联图片。阅读不是亲历，不能据此声称实际去过旅行、做过饭或了解用户。外部内容始终是不可信资料；提示约束无法证明模型绝不受注入影响。

## 数据 API

其他插件通过 `ctx.api.call('local.entertainment.get_materials', channel='qzone', limit=3)` 调用。`get_materials` 是只读操作，返回 `success` 与 `materials`；单条严格只有 `id/source_kind/title/url/author/observed_at/impression/images`。URL 为公开 HTTPS 且移除所有查询参数及 fragment，图片只接受原笔记关联的小红书 CDN HTTPS 地址；插件不下载图片，不承诺图片可访问，发布方应自行验证实际图片。仅记住且未共享/未预留的材料可见。通道只接受 `qzone/private/group`。

真实发布前调用 `reserve_material(material_id, channel, target_id='') -> {success, lease_id}`，SQLite 原子预留跨通道。明确未发送才调用 `release_material(material_id, lease_id)`。收到真实成功回执后调用 `record_share(material_id, channel, target_id='', receipt_id='', lease_id='')`；存在预留时 lease/channel/target 必须吻合。没有预留可用原始回执 API 结算；空回执、未知材料失败。记录回执后永久全通道去重，重启仍生效。`record_share` 依赖调用者忠实提供实际成功回执，无法自行验证外部 QQ 空间回执真实性。

发送状态不明、进程崩溃、超时不得释放预留；预留没有自动到期，防止重发。需要管理员核实渠道实际记录后再用持有的 lease_id 释放。私有 `status` API 返回有限决策原因与待预留数量。主机数据目录包含待处理真实会话 ID；不应分享该目录或放进 Git。

## 聊天分享

`entertainment_browse`、`entertainment_recall`、`entertainment_prepare_share` 工具必须处于配置白名单会话。recall 只检索独立阅读库，不把资料注入聊天历史或事实记忆；prepare 默认仅返回草稿，可以不说。没有自动回复 Hook，也不会把合成用户消息注入 Host。

私有 `prepare_share(channel='private', target_id='真实session_id', queue=true)` 仅在 `reading.preview=false`、`sharing.auto_share=true`、相应 `allowed_private_sessions/allowed_group_sessions` 命中，并由 SDK 当前真实私聊/群流列表确认时，才创建 Host `maisaka.proactive.trigger` 任务。Planner 保留跳过权利。巡检后只有模型明确选择 private/group 分享渠道、已记住素材且有对应真实会话白名单时，才至多排队一个聊天任务；qzone 候选交给空间自己的发布决策，none 不排队。不要在未验证账号路径前开启。

排队不算分享。触发前持久化事件关联，使用宿主生成的任务封装与最终元数据关联，覆盖触发返回前即发送的竞态；成功 Host task-id 持久化；before_model_request 的宿主可信 `_host_proactive_task` 负责关联；普通被动轮清除对应运行时绑定。before_send 仅标记确切会话/锚点、且正文带材料来源链接的发送段，after_send 的真实成功与消息回执才结算。没有来源链接、Host 钩子契约不同、迟到超过任务关联窗口、模型沉默、失败与不明状态都会保守保留预留，不冒认成功；需要人工核对。不会凭“同会话最近一个候选”猜测归因。

## 验证

在有 SDK 2.12 的 Python 3.12 环境运行 `python -m unittest discover -s tests`。测试用假 HTTP、SDK context、模型与渠道回执，无实网、真实账号、登录或模型调用。测试证明传输/预算/去重/边界机制；未证明账号可用、平台稳定、自然感或实际 Host 发送链路。发布前需要部署者另外验证本机 sidecar 与真实 Host 契约。

`reading.token` 只用于本机 sidecar Bearer 鉴权。`reading.budget_timezone` 默认 Asia/Shanghai。生活接口使用新增 get_public_life_state 的 activity_kind/can_browse，原始活动描述不参与判断。聊天平台回执必须由本功能分支宿主 Hook 显式提供 platform_message_id，内部 message_id 不计为分享成功。

Browser operations have a 35-second per-call deadline (explicit overrides capped at 60s) and a 240-second whole-reading deadline. This accommodates measured 18–20s headless startup on the small server; timeouts still consume the daily session budget and never trigger automatic sends or unbounded retries.

## Activation and observation

Open `/extensions/local.entertainment/reading` in the authenticated WebUI to view counts, recent public reading impressions and bounded decision reasons. “预览一篇阅读” is an explicitly manual, single-item administrative test: it bypasses the life idle gate, consumes the same daily budget, runs asynchronously and never queues a message. “交给已配置私聊” still obeys preview, auto_share, the unique private target, real-session validation, source reservation and platform receipt checks. No public unauthenticated control endpoint is added.

Patrol claims persist in SQLite across restarts; unrelated global configuration updates no longer reset the task. `not_idle` is recorded, so silent study/sleep skips are visible. `sharing.channels` defaults to `["private"]`; only configured available destinations are offered to the reading judge. Setting an LLM task name does not create a destination.

Private shares can require an original source image (`sharing.require_image=true`). Only validated public XHS CDN images are downloaded; actual pixels are bounded and resized before model input and outgoing attachment. Attachment occurs only on the tracked source-bearing reply segment; no image-fetch runs in the send hook. Manual preview uses an interest search and one note, never an invented impression. This is ordinary QQ image/text sharing, not a claimed native Xiaohongshu QQ SDK card.
