# 兴趣阅读与自然分享

本功能在 feat/entertainment 开发，独立插件源码由 deployment/plugin-lock.json 的精确提交锁定。主程序、麦生活、空间、搜索、阅读插件是一组兼容改动。新增插件默认关闭；空间 natural_mode 默认关闭，已有线上人设/权限/发送策略不被本分支自动修改。

## 使用效果与边界

她有空时可以读小红书推荐，推荐为空时按兴趣搜索；依据简洁人格和兴趣判断是否值得记住、是否有感想以及是否想分享。记住不等于分享，share_channel=none 可以完全沉默。兴趣包括摄影、书籍、绘画、料理/烹饪、旅行、美食，可在插件配置调整；具体食物偏好继续放人设中，不在通用代码绑定私人身份。

记忆以 external_reading 分类保存标题、作者、公共链接、浏览时间、关联图片和感想；原帖正文仅参与当次阅读判断，不当作用户事实/亲历，不持久化认证 token。阅读档案持久化，提供 entertainment_recall 工具作本地检索；目前是字面主题检索，不宣称已完成向量/长期事实库整合或自动每轮注入。

私聊和群聊只有真实会话白名单、preview=false、auto_share=true 且模型明确想分享时，才排队宿主主动任务；宿主仍可选择沉默。排队不算成功，只有带来源链接的实际发送段和平台回执才结算。空间独立按自己的时机选择素材，阅读不会直接强制发说说。同一素材默认只主动分享一次，跨渠道持久去重；确实需要再次分享时应另行设计明确策略，不通过删记录绕过。

## 依赖和安装清单

先使用主程序本分支及锁文件中的四个定制插件版本：Mai_life、Maizone、google-search、local.entertainment。插件仍是独立 Git 仓库；不能只拉主分支后假定功能源码已经安装。其余锁定插件保留不变。部署遵循 docs/git_deployment.md，备份主/插件提交、前端产物、配置和数据，先在合成实例验证。不要让第二个实例连接生产 QQ 同账号。

SDK 至少2.12。图片验证依赖 Pillow，沿用现有模型任务路由，不添加付费生图服务。Maizone natural_vision_model 应配置到支持图片的任务（本实例继续用 deepseek-flash）；没配视觉、图不匹配或下载失败时降为纯文字，不补无关表情包。

## 小红书侧服务

接入候选是 [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)。本功能只调用 check_login_status/list_feeds/search_feeds/get_feed_detail，拒绝发布、评论、点赞、收藏等调用。它不是小红书官方接口；必须由用户完成账号注册/扫码，浏览器/API和账号运行效果仍需实测。

可以采用 deployment/entertainment/xhs-compose.yml 的本机绑定部署模板，先指定经核验的镜像 digest、私有状态目录与鉴权密钥，再由操作者启动。模板没有自动选择 latest 镜像或自动启动浏览器。也可以运行已核验二进制，使用 -port 127.0.0.1:18060 并配置 AUTH_TOKEN；须核对所选上游版本支持这些参数。

上游当前提供 GET /api/v1/login/qrcode 和 GET /api/v1/login/status，使用 Authorization: Bearer 鉴权。通过本机/SSH 隧道获取二维码让用户扫码；不得把 cookies、完整登录响应或令牌写进 Git/聊天日志。阅读插件 reading.endpoint 指向 http://127.0.0.1:18060/mcp，reading.token 与 sidecar 匹配。全局 MCP 配置不需要开启。

## 分阶段配置

先在 WebUI 插件市场/插件管理中加载精确版本；阅读插件的原生配置页可修改以下分组。真实会话 ID 应从现有会话列表读取，不从 QQ 号自行构造。

```toml
[plugin]
enabled = true

[reading]
endpoint = "http://127.0.0.1:18060/mcp"
# token 在本机配置，示例不放实际值
interests = ["胶片摄影", "看书", "绘画", "料理", "旅行", "美食", "抹茶甜品"]
auto_browse = true
preview = true
budget_timezone = "Asia/Shanghai"
daily_sessions = 3
max_items = 2

[sharing]
auto_share = false
allowed_private_sessions = []
allowed_group_sessions = []
```

空闲判断来自麦生活新增的 get_public_life_state：activity_kind、can_browse、睡眠标记和有限数值；没有原始日程、地点、书柜或关系正文。仅 leisure/rest 且明确允许浏览时运行；API不可用、结构不明、睡眠/学习/工作都不读。预算和 RPC/模型总截止时间限制故障开销。

首轮先保持 preview=true 和空分享白名单，验证读取、批注、status 与检索；然后填写允许会话，验证生成草稿。确认后才把 preview=false、auto_share=true 放开真实主动任务。状态 API 是插件私有管理入口，提供数量与有限决策原因，不展示 cookies 或账号。

## 空间新流程

Maizone 配置 send.natural_mode=true 才使用新路径；send.enable_image 控制可选配图。先选择独立碎念、某条阅读素材或跳过，再生成正文：独立模式不接收任何阅读素材，阅读模式不能丢弃选定来源。自己的设定内小生活可以继续想象；来源作者经历和用户事实不能借此变成她的亲历。

调用公开 API internetsb.maizone.preview_natural_feed_api 可只预览文字/来源与是否配图，不获取空间 cookies、不发布、不登记分享。实际接口由 SDK 插件管理/调试入口调用，不是未经认证的公网 HTTP 接口。

图来自原帖或搜索数据接口 xxxxx7258.google-search-plugin.search_image_material；该接口不发送聊天消息。搜索插件 actions.image_search_enabled 也必须允许；否则只发文字。下载限制公开 HTTPS、无跳转、大小和真实像素验证；再用视觉模型检查文图相关性。重复图片和完全重复的文字抑制，不保留全部私聊作为参考。搜索图及原帖图保留链接，不能当她实际拍摄的照片。

素材发布前原子预留，得到 Qzone fid 后才写全局分享记录；发送结果不明时保留 pending 待人工核对，避免盲目重试。文字/图片指纹历史仅保存最近50条；目前不宣称具备完整语义重复检测。已有老模式继续可用，切回 natural_mode=false 即恢复旧路径，但不会删除任何新资料。

## 测试和未验证项

测试使用合成资料、假模型/HTTP/SDK/Qzone 与临时 SQLite；不向真实 QQ/空间发送，不回放私人聊天，不连接小红书账号。测试覆盖真实生成入口记忆交接、MCP白名单/会话/超时、资料来源、空闲与预算、跨渠道预留/回执、触发竞态、独立文案隔离与配图验证。

单元通过不等于文案自然度、账号登录/稳定性、推荐质量或真实渠道送达已通过。真实验收应从预览和少量人工选择的分享开始，分别记录接入、模型质量、平台回执与用户主观效果。主程序全仓仍有已核对的基线失败，详见本轮验证记录，不能称全仓全绿。

## 回退

本次不更改现有生产配置。部署后遇到问题，先关闭 reading.auto_browse、sharing.auto_share 与 send.natural_mode；需要代码回退时主程序与锁定插件一起回到部署前锚点。新阅读库、收据和记录保留备份；没有原长期记忆库迁移，不需要删除旧记忆或重建 embedding。

实机部署验证补充：小型服务器上一次浏览器冷启动约18–20秒，因此每次 MCP 操作截止时间为35秒，整次阅读截止为240秒。仍限制日预算，不自动重试，不改变聊天回复等待时间。

## 阅读观测与手动验收

登录 WebUI 后打开 /extensions/local.entertainment/reading。页面显示阅读、记住、实际分享数量，以及有限检查原因和最近阅读感想。手动预览只读一篇，明确作为管理员测试绕过生活空闲门禁，仍消耗日预算，始终不创建发送任务。长任务后台执行，页面轮询状态。选择已记住的阅读后，可交给唯一已配置私聊；预览、自动分享开关、真实会话白名单和回执预留仍必须通过。

自动巡检时间持久化，重启继续等待剩余时间；全局配置更新不重置巡检。学习/睡眠跳过会记录 not_idle。sharing.channels 默认仅 private，不提供没有实际目标的群聊选项。此页面不暴露 cookies 或小红书令牌。
