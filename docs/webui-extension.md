# Mai_life WebUI 扩展设计与交接文档

> 用途：让前端/设计同学在不读 MaiBot 源码的前提下，为 Mai_life 插件设计声明式 WebUI 页面；设计稿交回后由插件开发落成 `webui.json`。
> 协议权威来源：MaiBot 仓库 `docs/plugin-webui.md` 与 `src/plugin_runtime/webui_schema.py`；本文档与其冲突时以源码为准。
> **版本要求：WebUI 协议版本 2（`schema_version: 2`，2026-10 起的宿主；协议 1 的声明会被宿主**整插件注册拒绝**），插件开发侧 SDK ≥ 2.12.0。协议 2 相对 1 移除了 repeat/gallery/upload 组件与 `scope:"item"`，新增 progress/image 组件、custom HTML 页面与 `required_capabilities`。**

---

## 1. 这套 WebUI 是什么

- 插件在自身目录放一个 **`webui.json`**，重载插件即生效。**不需要 Node、不需要打包前端、不能写 HTML/CSS/JS**。
- 页面由宿主 dashboard 渲染，视觉（颜色、间距、字体、暗色模式）全部由宿主控制，设计空间 = **信息架构与组件选型**，不是像素级样式。
- 两类入口：
  - `placement: "sidebar"`：麦麦工作区 →「插件扩展」分组里的侧边栏页；
  - `placement: "workspace"`：插件自己的顶部工作区，名称由 `workspace_title` 指定，侧边栏并列展示该插件全部 workspace 页，**第一个是默认页**。
- 路由由宿主生成为 `/extensions/{plugin_id}/{page_id}`，插件不能自定义路由。
- 用户可在「插件管理 → 管理插件页面」调整入口顺序/隐藏入口（浏览器本地偏好，非授权）。
- **权限模型**：WebUI 后台只有 MaiBot 主人能进入。Mai_life 的页面数据对主人全开（日记、用户关系、Token 统计等），v2 起内容型数据（日记/梦境/作品/新闻/探索）**正文与来源 URL 全量展示**；**唯一例外是各类 API Key 只出掩码/指纹**（凭证卫生，与隐私无关）。

## 2. 文件格式

```json
{
  "schema_version": 2,
  "required_capabilities": ["declarative_basic"],
  "workspace_title": "麦生活",
  "pages": [
    {
      "id": "overview",                 // 全局唯一，进路由
      "title": "生活状态大屏",           // 页面标题
      "placement": "workspace",         // workspace | sidebar
      "icon": "chart",                  // 仅 puzzle/chart/settings/database/list
      "queries": {                      // 只读查询：别名 → {api, version}
        "status": { "api": "webui_status_overview", "version": "1" }
      },
      "actions": { },                   // 写操作（Mai_life 当前全为只读页，不使用）
      "content": [ /* 组件树，见第 3 节 */ ]
    }
  ]
}
```

- `queries`/`actions` 里的 `api` 只能写**本插件**已用 SDK `@API` 注册的方法短名，`version` 缺省 `"1"`，必须精确匹配。
- 页面打开/刷新时**依次执行全部 queries**；操作成功后再次刷新 queries。
- 输入字段（input/date/select/switch）的 `name` 页面内唯一；`actions` 的 `parameters` 从表单字段取值，类型 `string/integer/number/boolean`，支持 `required`、`max_length`(≤4000)、`minimum`、`maximum`、`choices`；`danger` 操作必须声明 `confirmation`。

## 3. 组件规格（协议 2 共 17 种，全部白名单）

| 类型 | 属性 | 说明 |
|---|---|---|
| `stack` | `label`、`children` | 纵向排列 |
| `card` | `label`、`children` | 宿主卡片（与 stack 同模型，仅默认样式不同） |
| `grid` | `label`、`columns`(1-4)、`children` | 网格，移动端自动单列 |
| `tabs` | `label`、`children` | 每个子节点必须有 `label` |
| `collapsible` | `label`（必填）、`children`、`default_open` | 可展开区块，默认收起 |
| `dialog` | `name`、`label`（必填）、`children` | 详情弹窗；**必须在 content 顶层声明**（防止被折叠/未激活 tab 隐藏），name 页内唯一，Escape/关闭按钮可关 |
| `text` | `label`、`value`、`format`(plain/log) | 纯文本；HTML 不执行；换行保留（whitespace-pre-wrap）；`value` 可省略 |
| `stat` | `label`、`value` | 统计大数字卡片；`value` 可省略 |
| `progress` | `label`、`value` | 进度条；**value 必须是 0–100 的数字或数据引用**（适合精力/饥饿这类百分比） |
| `image` | `label`、`value` | 图片；**value 必须是数据引用**，返回受限 data URL（JPEG/PNG/WebP，≤24000 字符） |
| `table` | `label`、`value`、`columns`、`selection`、`detail` | `columns=[{"field","label"}]`；绑定**对象数组**，每页 50 行；声明 `selection` 后点击/Enter/空格选中行，`detail` 同时打开具名弹窗 |
| `chart` | `label`、`value`、`chart_type`(line/bar，默认 line)、`x`、`y` | 绑定**对象数组**，x 为字符串/数字，y 为数字，≤2000 行 |
| `pagination` | `label`、`name`、`value` | 绑定 `{page,pages,total}`，`name` 参与页面输入名唯一性；搭配 `auto_refresh: true` 服务端分页 |
| `input` | `name`、`label`、`value` | 数字默认值→数字框，其余文本 |
| `date` | `name`、`label`、`value` | 字符串日期 |
| `select` | `name`、`label`、`value`、`options` | `options=[{"label","value"}]`，value 必须属于 options |
| `switch` | `name`、`label`、`value` | value 必须布尔（默认 false） |
| `button` | `label`（必填）、`action`、`variant` | variant：primary/danger/muted；danger 必须声明 confirmation |

**协议 2 已移除**：`repeat`（数组循环）、`gallery`（图片墙）、`upload`（声明式上传）、`scope:"item"`——混用任一都会让宿主**整插件注册失败**。数组循环的需求改由 custom 页面或后端拼装文本实现；图片用 `image` 组件（数据引用）；上传只存在于 custom 页面（`uploads` + `required_capabilities: ["image_upload"]`）。

**交互组件禁用态（协议 2 新增）**：input/date/select/switch/button 支持 `disabled_when`（VisibilityCondition）与 `disabled_reason`（Label），条件满足时禁用并展示原因。

**数据绑定**：`value` 可以是标量（字面量），或 `{"source": "查询别名", "field": "a.b.c"}`。`field` 为空串 = 整个查询返回值。**table/chart/pagination 必须用数据引用；progress 接受数字或数据引用；image 只接受数据引用**。不支持计算表达式、脚本、链接。

**数据上下文 scope**：数据引用支持可选 `scope`——默认 `"query"` 读查询结果；`"selection"` 读表格选中行（`source` 填该表的 `selection` 名）。无选中行时 selection 引用为 null（页面显示"—"）。协议 2 已移除 `"item"`。

**条件显示 when（全组件通用）**：`"when": {"reference": {...}, "operator": "truthy|empty|not_empty|equals", "expected": 标量}`——null/空串/空数组/空对象视为空，数字 0 不为空；首次查询完成前有条件区块不显示。**注意：tabs 的直接子节点加 when 时页签标签仍渲染（内容空），when 应加在 tab 内部的组件上。**

**动态参数 arguments（仅 actions）**：action 可用 `arguments` 把参数绑到数据上下文（如 `{"id": {"scope":"selection","source":"row","field":"id"}}`），逐字段覆盖同名表单参数；**queries 不得声明 arguments 或 confirmation**。查询刷新后选中行清除、详情弹窗关闭。

**页面级轮询**：`poll_interval_seconds` 3–60 秒定时重跑 queries（0 关闭；操作忙碌时跳过；离开页面停止）；`auto_refresh` 供分页等表单参数变化时自动重查。

**custom 页面（协议 2 新增，Mai_life 当前未使用）**：`mode: "custom"` 的页面用 `entry` 指向插件内相对 HTML 文件（≤2 MiB，总量 ≤8 MiB），可配 `layout: document|viewport`、`uploads` 图片上传；需要 `required_capabilities: ["custom_pages"]`（上传另加 `image_upload`）。结构化参数（array/object）另需 `structured_arguments`。

## 4. 渲染器硬规则（踩坑篇，违反即整页报错）

宿主渲染器在下列情况直接抛异常，整页显示「插件返回的数据与组件声明不一致」。**设计时必须遵守**：

1. **绑定路径必须全程存在**：`field` 途经的每一层都必须是字典且键存在。后端把"无数据"返回成 `null` 嵌套对象（如 `latest_diary: null`）时，绑定 `latest_diary.title` 直接炸页。→ 后端契约：可空嵌套对象一律返回**键齐全的空字典**（值 null/空串），页面显示"—"。
2. **text/stat 只能绑标量**：绑到对象或数组会炸。列表类数据（如可用模型任务）后端必须拼成字符串。
3. **table**：绑定值必须是数组、每行必须是对象（null 行也炸）；单元格显示前会把对象/数组判为非法。
4. **chart**：数组 ≤2000 行；每行 `y` 必须是有限数字、`x` 必须是字符串或数字；**空数组合法**（显示空图）。
5. 查询失败/API 下线会显示错误，**不使用伪造数据**——所以不要为了"好看"绑定不存在或未必存在的字段。
6. **dialog 必须在 content 顶层**（depth=1），name 页内唯一；`table.detail` 必须与 `selection` 同时声明，selection 名页内唯一。
7. **协议 2 没有 repeat**：需要"数组每项一组组件"时要么后端拼成文本用 text 展示，要么用 custom 页面自己渲染；不要写 repeat/gallery/upload/scope:"item"，写了宿主直接拒绝注册。
8. 查询刷新成功后**选中行清除、详情弹窗关闭**——详情内操作（如删除）完成后数据会刷新，属预期行为。

## 5. 限制与上限

| 项 | 上限 |
|---|---|
| webui.json 文件 | 128 KiB（Host 注册载荷 512 KiB） |
| 每插件页面数 | 20 |
| 每页组件数 | 200（content 顶层 ≤50） |
| 每页嵌套层数 | 8 |
| 每页 queries / actions | 10 / 20 |
| 每绑定参数 | 30 个标量 |
| custom 页面 HTML / 全部 HTML 总量 | 2 MiB / 8 MiB |
| 轮询间隔 | 3–60 秒（0 关闭） |
| 每插件并发 WebUI 请求 | 2 |
| API 超时 | 10 秒 |
| API 响应 | 512 KiB、12 层嵌套、20000 元素 |
| table 分页 / chart 行数 / image data URL | 50 行/页、2000 行、24000 字符 |

无效声明或引用未注册 API → **插件本次注册整体失败**（日志有原因），所以字段名必须与后端契约逐字一致。

## 6. Mai_life 现状：11 个只读 API 数据契约

全部 `public=False`、`version="1"`、方法名=API 名、异常兜底返回 `{"success":False,"error":...}`。数值一律 round 一位小数。
**全量原则（v2 起）**：WebUI 后台仅 MaiBot 主人可进入，日记/梦境/作品/新闻/探索的正文与来源 URL 一律全量输出（不再截断）；列表另附 `*_full_text` 大文本字段（多条全文按分隔线拼接），供"全文 tab"整段阅读；条数上限见下表。搜索历史的 `results_digest` 为结果摘要的多行文本（标题/摘要/URL），查询词入库前已做身份清洗。

| API（查询别名惯用同名） | 返回结构（节选） |
|---|---|
| `webui_status_overview` | `bot_name, version, generated_at`；`state{energy,hunger,mood_valence,mood_arousal,mood_label,sleep_phase_zh,current_activity,current_location,health_note}`；`schedule{current{kind,kind_zh,summary,location,start,end,window},next{kind_zh,summary,start},next_summary,scene{scene,applied}}`；`weather,day_type,upcoming_festival`；`sleep{phase_zh,kind_zh,grace_until,woken_count,last_event,defer_until,started_at}`；`linkage{drawpic_enabled,drawpic_detected}`；`services{llm_health,available_tasks,background_tasks,active_bursts,active_groups,users_summary,diary_last_day,narrative_note,news_count,exploration_count,search_history_count,last_failure,group_observations,archived_today,pending_inspirations}`；`recall{enabled,cache_enabled,latest{user_id,summary,media_types,age}}` |
| `webui_status_history` | `points[{ts,time,energy,hunger,mood_valence,mood_arousal,sleep_phase}]`（近 72 小时，升序，可空）；**已按 2 小时网格重采样**（渲染器是类别轴、等宽分格，稀疏快照会让 1 小时与 9 小时间隔一样宽；网格点取该时刻最近一次快照，起点=最早一条快照，不虚构更早历史）；`time` 为 `MM-DD HH:MM`（按环境时区），**图表 x 轴必须绑 `time` 而不是 `ts`** |
| `webui_information_digest` | `status{information_enabled,news_enabled,search_enabled,sources,recent_news,recent_explorations,recent_search_history}`；`news[{title,summary,url,content,age,score}]`≤10 + `news_full_text`（含来源链接的全文拼接）；`explorations[{topic,query,summary,sources_text,age,score}]`≤10 + `explorations_full_text`；`search_history[{time,operation,query,provider_type,success,result_count,error_class,results_digest,duration_ms}]`≤20 |
| `webui_bookshelf_overview` | `counts{documents,private_documents,creation_runs}`；`creation{enabled,plaintext_acknowledged,archived_today,pending_inspirations,daily_max}`；`books[{doc_type,title,privacy,status,updated_age,summary,content}]`≤20 + `books_full_text`；`runs[{document_id,title,status,started_at}]`；`latest_work{title,privacy,content}`（正文全量） |
| `webui_schedule` | `day,generated_at`；`current/next/scene`（同 overview.schedule）；`nodes[{start,end,kind_zh,summary,location,scene,energy_load,shareability}]`；`tomorrow{day,nodes}` |
| `webui_users` | `counts{total,enabled,disabled,owners,proactive_enabled}`；`users[{user_id,display_name,role,enabled,temperature,stage,proactive_enabled,proactive_count,daily_proactive_max,last_message_age,last_proactive_age,last_relation_day,recent}]` |
| `webui_memories` | `last_diary_day,diary_count,dream_count`；`latest_diary{day,title,content,mood_summary}`（正文全量）；`diaries[{day,title,mood_summary,content}]`≤20 + `diaries_full_text`；`latest_dream{created_at,content,mood_delta,energy_delta,fragments}`；`dreams[{created_at,content,mood_delta,energy_delta}]`≤20（正文全量） |
| `webui_dates` | `counts{dates,candidates,within_30d}`；`dates[{event_name,user_name,event_date,recurrence,occurrence,countdown,days_left}]`；`candidates[{event_name,user_name,date_text,suggested_date,confidence,created_at}]` |
| `webui_usage` | `day,generated_at`；`totals{tokens_today,calls_today,search_calls_today,tokens_7d,calls_7d}`；`daily[{day,tokens,calls}]`（近 7 日）；`tokens[{label,calls,successes,prompt_tokens,completion_tokens,total_tokens}]`；`searches[...]` |
| `webui_config_summary` | `plugin{enabled,version}`；`users{configured,enabled}`；`models{fast_task,reasoning_task}`；`environment{timezone,city,weather_refresh_minutes}`；`linkage{...}`；`debounce{private,group}`；`recall{...}`；`context{...}`；`rest_gate{enabled,mode,wake_probability,llm_threshold,awake_grace_minutes,silence_minutes,night_window,nap_window}`；`memory{...}`；`information{...}`；`news/search/search_api{...}` |
| `webui_admin_summary` | `overview{version,users,owners,pending_dates,pending_proactive,bookshelf_documents,private_documents,token_calls_today}`；`groups[...]`；`proactive{candidates,skips}`；`sources{information_enabled,news_enabled,search_enabled,external_reading,providers,keys}` |

## 7. 当前页面结构（v2，7 页 / 120656 字节，已通过 Host 1.3.6 schema 校验）

| 页面 | placement | 查询 | 内容概要 |
|---|---|---|---|
| 麦麦时刻 | workspace | status, history | **30 秒轮询**；「此刻」双卡（正在做的事 6 字段 / 日程流转 8 字段）；身体与情绪 4 stat；72 小时轨迹 tabs×4 chart（含曲线范围说明）；睡眠与休息闸门 7 格；后台脉搏 8 stat；撤回增强运行时；关于本页 6 格（含可用模型任务/私聊用户） |
| 日程时间线 | sidebar | schedule | 当前节点卡 5 字段 + 下一节点卡 4 字段；今日时间线表；明日预览表；数据说明 |
| 社交与牵挂 | sidebar | users, dates | tabs：用户关系（计数 4 stat + 关系表 ≤50 人）；重要日期（计数 3 stat + 日期表 + 待确认候选表）；数据说明 |
| 内心世界 | sidebar | memories, shelf | 计数 4 stat；tabs：日记（最新全文卡 + 近期日记表，**点击行弹窗看全文**）/日记全文连读/梦境（最新卡 + 列表，**行弹窗**）/书柜（创作状态 + 文档表 **行弹窗** + 运行表 + 最新作品卡）/作品全文连读；**3 个 dialog**（日记/梦境/作品详情） |
| 见闻与搜索 | sidebar | digest | 联网功能与计数合并 grid 4×2；tabs：新闻表/**新闻全文**/探索表/**探索全文**/搜索历史表——三表均支持**点击行弹窗**看 URL、正文与结果摘要；**3 个 dialog**；展示边界说明 |
| 用量统计 | sidebar | usage | 今日/7 日合计合并 grid；趋势 tabs×3 chart；来源聚合表与搜索 API 表；数据说明 |
| 系统与配置 | sidebar | config, admin | 运行概览 grid 12；tabs：群聊与主动（3 表）/来源与 Key（2 表，Key 仅指纹）/总开关与模型/闸门与上下文/记忆与日程/联网与社交/创作与统计；空表自动隐藏 |

v2 排版惯例：叙事优先——workspace 大屏 C 位给"麦麦时刻"；相关指标合并 grid 打满 4 列；布尔开关不再独占大格（与计数混排）；次要内容收 tabs 折叠；长文正文一律走"列表 tab 扫读 + 全文 tab 连读"双层结构；表格详情一律走 selection+dialog 行弹窗（不新增页面）；说明性文字用裸 text 收尾。

**回归保障**（改动 webui.json 后必跑）：`Mai_life/tests/test_fixes.py` 的 `V11412WebuiJsonStructureTests`（声明结构/白名单/引用合法性）+ `V11412WebuiRendererBindingTests`（空库+种子库按渲染器同规则解析全部绑定，含 dialog 详情树模拟选中行）。Host schema 校验：

```python
import sys
sys.path.insert(0, r'<MaiBot 主程序目录>')
from src.plugin_runtime.webui_schema import WebUIExtension
WebUIExtension.model_validate_json(open('webui.json', encoding='utf-8-sig').read())
```

## 8. 设计交付物（交回给插件开发的格式）

1. **完整 `webui.json` 草案**（必须通过 `python -c "import json;json.load(open('webui.json'))"`）。
2. **每页设计说明**：信息架构选择、取舍理由、与现状的差异点。
3. **如需新数据**：明确列出需要的字段与结构（后端按第 4/6 节契约补齐；可空对象一律键齐全）。
4. **自检清单**（交回前逐项过）：组件全在白名单 17 种；schema_version=2 且声明了 required_capabilities；每个绑定字段在第 6 节契约中存在且类型匹配；scope=selection 的 source 是同页 table.selection 名；table/chart 只绑数组；text/stat 不绑对象/数组；progress 的 value 是 0–100；空数据场景（空库）不炸页；页数/组件数/查询数不超上限；dialog 在 content 顶层且 detail 指向已声明 dialog。

---

## 9. 交给前端设计师的提示词（可直接复制）

```
你在为 MaiBot 插件「麦麦生活（Mai_life）」设计声明式 WebUI 页面。这是一个生活模拟插件：麦麦有持续运转的精力/饥饿/心情/睡眠状态、每日日程、日记与梦境、重要日期、书柜创作、联网见闻、Token 统计等数据。WebUI 后台只有 MaiBot 主人能进入，数据对主人全开（API Key 除外，只出掩码）。

【技术约束（必须遵守，违反会整页报错）】
- 页面是声明式 JSON（webui.json），不能写 HTML/CSS/JS；宿主控制全部视觉，你只做信息架构与组件选型。
- 组件仅 17 种：stack/card/grid/tabs/collapsible/dialog/text/stat/progress/image/table/chart/pagination/input/date/select/switch/button。协议 2 已移除 repeat/gallery/upload 与 scope:"item"，写了宿主会整插件注册失败。
- 数据绑定 value 只能是标量或 {"source":"查询别名","field":"a.b"}；table/chart/pagination 必须绑对象数组；image 只绑数据引用；progress 的 value 是 0–100；text/stat 只能绑标量（绑对象/数组会炸页）。
- 绑定路径途经的每一层都必须存在：后端对"无数据"返回键齐全的空字典，你绑定空字典的键会得到 null（页面显示"—"），但绑定不存在的键名会整页报错。
- chart：x 为字符串/数字、y 为有限数字、≤2000 行，空数组合法；table 每页 50 行。
- 高级能力（按需使用）：when 条件显示（reference+operator，全组件通用）；交互组件的 disabled_when+disabled_reason；table.selection+detail 行点击弹窗（dialog 必须声明在 content 顶层）；数据引用可选 scope（query 默认 / selection 选中行）；poll_interval_seconds 3–60 秒轮询。
- 每插件 ≤20 页、每页 ≤200 组件（content 顶层 ≤50）、≤8 层嵌套、≤10 查询、≤20 操作；workspace 页必须写 workspace_title；icon 仅 puzzle/chart/settings/database/list。
- 页面分 placement=workspace（插件顶部工作区，第一个是默认页）与 sidebar（插件扩展分组）。

【现有数据契约（节选，完整版见随附文档第 6 节）】
- webui_status_overview：生活状态/日程当前与下一节点/天气节日/睡眠相位与休息闸门运行时/服务运行态/撤回摘要
- webui_status_history：近 72 小时状态快照 points[{ts,time,energy,hunger,mood_valence,mood_arousal,sleep_phase}]（图表 x 绑 time，MM-DD HH:MM）
- webui_schedule：今日/明日节点表 nodes[{start,end,kind_zh,summary,location,scene,energy_load,shareability}]
- webui_users：用户关系 users[{user_id,display_name,role,temperature,stage,proactive_count,daily_proactive_max,last_message_age,last_proactive_age,recent}]
- webui_memories：日记与梦境全量（latest_diary/latest_dream 正文不截断、diaries≤20 带 content + diaries_full_text、dreams≤20 带全文）
- webui_dates：重要日期与待确认候选
- webui_information_digest：新闻≤10（含 url+content + news_full_text）/探索笔记≤10（含 query+sources_text + explorations_full_text）/搜索历史≤20（含 error_class+results_digest）
- webui_bookshelf_overview：书柜文档≤20（带 content + books_full_text）/创作运行/今日创作额度
- webui_usage：今日与近 7 日 Token 统计（chart 友好）
- webui_config_summary / webui_admin_summary：配置开关与管理计数

【现状】v2 共 7 页（麦麦此刻/日程时间线/社交与牵挂/内心世界/见闻与搜索/用量统计/系统与配置），叙事优先大屏 + 内容页"列表 tab 扫读 + 全文 tab 连读"双层结构。

【你的任务】
1. 重新设计信息架构：可以合并、拆分、增删页面（≤12 页为宜），明确每页回答主人什么问题。
2. 输出完整的 webui.json 草案：workspace 大屏放"一屏看懂麦麦现在怎么样"（状态/日程/曲线），sidebar 按主题组织；重视图表运用（状态轨迹、Token 趋势），表格给明细。
3. 紧凑原则：一两行文字不要包全宽卡片；相关指标合并 grid；说明性文字放 tab 或裸 text 收尾。
4. 附每页设计说明与自检清单（字段存在性/类型/空库不炸页/上限）。
5. 如需新增数据字段，单独列一节"需要的后端补充"（字段名+类型+语义）。

【交付格式】一个完整 webui.json + 设计说明文档。先自检再交付。
```
