# Mai_life WebUI 开发文档（v2）

> 面向开发者：Mai_life 插件声明式 WebUI（主人后台）的架构、契约、修改与校验流程。
> 协议细节与字段级契约见姊妹篇 [`webui-extension.md`](./webui-extension.md)；本文聚焦"怎么改、怎么验证、别踩什么坑"。
> 当前对应版本：主程序 **≥ 1.3.6**（18 组件协议）、插件 v2 WebUI（7 页）。

---

## 1. 这是什么

Mai_life 在 MaiBot WebUI 后台注册了 **7 个只读页面**（1 个插件工作区大屏 + 6 个侧边栏页），供主人查看麦麦的生活状态、日程、社交关系、日记梦境、书柜创作、联网见闻、Token 用量与系统配置。

技术形态：

- **前端 = 插件目录下的 `webui.json`**（声明式 JSON，无 HTML/CSS/JS），宿主 dashboard 渲染，主题/配色/字体不可控；
- **后端 = `plugin.py` 里 11 个 `@API` 只读方法**（`webui_*`，`public=False`），页面打开/刷新时按 `queries` 依次调用；
- 页面路由由宿主生成：`/extensions/maibot-community.mai-life/{page_id}`，仅 MaiBot 主人可进。

## 2. 页面架构（7 页）

| # | 页面 | id / placement | 查询 | 回答的问题 | 关键交互 |
|---|---|---|---|---|---|
| 1 | 麦麦此刻 | `overview` / workspace（默认页） | status, history | 麦麦现在怎么样、在干嘛 | **30 秒轮询**；「正在做的事/日程流转」双卡；72h 曲线 tabs |
| 2 | 日程时间线 | `schedule` / sidebar | schedule | 今天/明天怎么安排 | 当前/下一节点双卡；明日预览空时隐藏 |
| 3 | 社交与牵挂 | `social` / sidebar | users, dates | 麦麦和谁好、要记什么日子 | tabs：用户关系 / 重要日期 |
| 4 | 内心世界 | `inner` / sidebar | memories, shelf | 麦麦写了/梦了/创作了什么 | tabs 五页签；**点表格行弹窗看全文**；全文连读 tab |
| 5 | 见闻与搜索 | `information` / sidebar | digest | 麦麦看了什么、搜了什么 | tabs 五页签；**三张表均可点行弹窗**（URL+正文+结果摘要） |
| 6 | 用量统计 | `usage` / sidebar | usage | 今天烧了多少 Token | 趋势图收 tabs；空表隐藏 |
| 7 | 系统与配置 | `system` / sidebar | config, admin | 开关都开了没、有无疑难问题 | 运行概览 12 格 + 7 个分类 tab |

### 三个核心交互模式（1.3.6 协议）

1. **行详情弹窗**：`table.selection` + `table.detail` + 顶层 `dialog`，弹窗内绑 `{"scope":"selection","source":"<选择名>","field":"<行字段>"}`。注意 dialog **必须放 content 顶层**、name 页内唯一；查询刷新后选中行自动清除。
2. **全量双轨**：列表 tab 扫读（摘要列）+ 全文 tab 连读（后端 `*_full_text` 拼接大文本，`text` 组件 `whitespace-pre-wrap` 保换行）+ 行弹窗看单条——三层各管一段。
3. **`when` 空数据隐藏**：空数组/空串区块整块消失。注意 **tabs 直接子节点的 `when` 只隐藏内容、页签标签仍渲染**——`when` 要加在 tab 内部的组件上。

## 3. 文件地图

| 文件 | 作用 |
|---|---|
| `Mai_life/webui.json` | 页面声明（唯一被宿主读取的前端文件；`webui_v2.json` 是设计稿副本，`webui_v1_backup.json` 是旧版备份） |
| `Mai_life/plugin.py` | 11 个 `webui_*` API（约 2100–2650 行区段）+ 辅助：`_hhmm_text`、`_search_results_digest`、`_FULL_TEXT_SEP`、`_switch` |
| `D:\workdoc\plugin\_mai_life_webui_check.py` | **离线校验脚本**（改 webui.json 后必跑，见 §6） |
| `Mai_life/tests/test_contract.py`、`test_fixes.py` | API 契约测试（`V11412Webui*Tests` 系列类） |
| `docs/webui-extension.md` | 协议字段级契约（改 API 输出时必须同步） |

## 4. 后端契约要点（v2 全量原则）

**主人后台全量展示**：日记/梦境/作品/新闻/探索的正文与来源 URL **不截断**；布尔开关一律中文（`_switch` → "开启/关闭"）；时间戳格式化（`_hhmm_text` → "MM-DD HH:MM"，0 → `None` 显示 "—"）。

v2 新增/变更字段（完整 11 API 契约见 `webui-extension.md` §6）：

| API | 新增/变更 |
|---|---|
| `webui_memories` | 日记 ≤20 篇带 `content` + `diaries_full_text`；dreams ≤20 全量；latest_* 不截断 |
| `webui_bookshelf_overview` | books 带 `content` + `books_full_text`；latest_work 不截断 |
| `webui_information_digest` | news ≤10 带 `url`+`content` + `news_full_text`；explorations ≤10 带 `query`+`sources_text` + `explorations_full_text`；search_history ≤20 带 `error_class`+`results_digest`；status 三开关中文化 |
| `webui_status_overview` | `sleep.*_until/started_at` 时间戳 → 格式化字符串；`recall.enabled/cache_enabled` 中文化 |

**渲染器硬规则**（违反整页报错）：绑定路径每层必须存在（可空对象返键齐全空字典）；text/stat 只绑标量；table/chart 绑对象数组；chart y 必须有限数字。

## 5. 关键设计决策（为什么这么改）

1. **stat 大字只给数字**：数字指标用 `stat`（3xl 大字合理）；文字值（开关/类型/时间）用 `card` 包 `text`（正常字号）。上一轮一刀切 text→stat 被主人打回，教训是**按运行时值的类型分流**（转换脚本 `_webui_cardify2.py` 即真跑 API 判型）。
2. **裸 text 不出现在 grid 里**：grid 单元格要么 stat 要么 card——无边框 text 与卡片混排视觉割裂（主人截图点名）。
3. **system 页 185/200 组件**：配置项最全的一页，余量仅 15。再添加配置项时合并次要 grid 或外移，勿硬塞。
4. **chart 颜色是宿主写死的**：`fill="var(--primary)"`，retro 浅色主题下呈黑色——主程序问题，插件无法干预（已向主人说明，可提上游 issue）。
5. **全文 tab 与行弹窗并存**：弹窗看单条、连读看全部，不是冗余是两种阅读场景；后端 `*_full_text` 字段同时服务两者。

## 6. 修改与校验流程

### 只改页面（不动 API 字段）

1. 编辑 `Mai_life/webui.json`
2. 跑校验（必做）：
   ```
   cd D:\workdoc\plugin
   <实例根>\python-env\python.exe _mai_life_webui_check.py
   ```
   脚本做三道校验：主程序**真实** `webui_schema` 结构校验 → 临时空库实例化插件真跑 11 个 API 核对每条绑定（路径存在/数组标量匹配/响应 ≤512 KiB）→ 行字段白名单兜底（空库时表格列拼写）。
   > 校验对象默认为 `webui_v2.json`；校验正式文件时改脚本顶部 `WEBUI_FILE`。
3. 同步部署目录并在 WebUI 插件管理**重载插件**（见 §7）。

### 改 API 输出（增改字段、截断、格式）

1. 改 `plugin.py` 对应 `webui_*` 方法（只动输出层，不动存储）
2. **同步测试断言**：`test_contract.py`（WebuiApiContractTests）+ `test_fixes.py`（`V11412Webui*Tests`）——字段集合、条数、格式断言是逐字钉死的
3. **同步 `docs/webui-extension.md` §6 契约表**
4. 跑回归：
   ```
   <实例根>\python-env\python.exe -m unittest Mai_life.tests.test_contract Mai_life.tests.test_fixes.V11412WebuiOverviewTests Mai_life.tests.test_fixes.V11412WebuiDigestTests Mai_life.tests.test_fixes.V11412WebuiBookshelfTests Mai_life.tests.test_fixes.V11412WebuiMemoriesTests
   ```
5. 改 `webui.json` 绑定 → 跑 §6.2 校验 → 部署。

## 7. 部署与回滚

| 项 | 路径 |
|---|---|
| 开发目录 | `D:\workdoc\plugin\Mai_life` |
| 部署目录 | `%APPDATA%\MaiBotOneKeyDesktop\0163381a22cb\modules\MaiBot\plugins\Mai_life` |
| 模块 Python | `%APPDATA%\MaiBotOneKeyDesktop\0163381a22cb\python-env\python.exe` |

- 改完同步 `plugin.py` / `webui.json` 到部署目录（其余目录两侧一致），WebUI 插件管理**重载 Mai_life** 即生效，无需重启主程序；
- 部署目录内备份：`webui_v1_backup.json`（旧 10 页版）、`plugin_v1_backup.py.bak`（全量化前 plugin.py）——回滚改回原名即可；
- 浏览器导航最多 30 秒更新，手动刷新立即可见。

## 8. 已知限制（宿主侧，插件改不了）

- 主题/配色/字体/暗色由 dashboard 控制；无 `className`/`style`/HTML 插槽；
- chart 颜色写死 `var(--primary)`（浅色主题黑柱问题见 §5.4）；
- table 固定 50 行/页；chart ≤2000 行；`tabs` 子节点 `when` 只隐藏内容不隐藏页签；
- API 超时 10 秒、响应 ≤512 KiB；单插件并发 WebUI 请求 2。

## 9. 维护检查单

改完任何一处，过一遍：

- [ ] `_mai_life_webui_check.py` 全绿（schema + 真跑 API + 绑定核对）
- [ ] 相关测试类全过（44 个）
- [ ] `webui-extension.md` §6/§7 与实际输出一致
- [ ] 部署目录已同步、插件已重载
- [ ] 页面数 ≤20、每页组件 ≤200（system 页余量 15）、嵌套 ≤8 层、文件 ≤128 KiB
