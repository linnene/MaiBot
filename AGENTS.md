# AGENTS.md — 给在本仓库工作的 AI 代理

本文件是你在 `Mai_life`（麦生活 / MaiBot 插件）上干活的操作契约。
**动手前先读 `docs/plugin-guide.md`**（插件完全文档：架构、模块参考、数据模型、消息生命周期、关键流程、已知坑）。本文件只讲"怎么在这里正确地干活"，不重复架构内容。

---

## 1. 环境与跑测试

```bash
cd D:\workdoc\plugin            # ⚠️ 必须是 Mai_life 的父目录，cwd 放错会测到旧代码
PYTHONPATH="D:/MaiBot/plugin/maibot-plugin-sdk-2.8.2;D:/MaiBot/plugin" \
  "C:/Users/octmicy/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
  -m unittest discover -s Mai_life/tests
```

- 全量 542 个测试、100 秒内跑完。**提交前必须全绿**，没有例外。
- 单独跑一个模块：把 `discover -s Mai_life/tests` 换成 `Mai_life.tests.test_core` 这类模块路径。
- 部署环境验证（用宿主自带 Python + 真实 maibot_sdk，用于确认 SDK 兼容性）：
  `"C:/Users/octmicy/AppData/Roaming/MaiBotOneKeyDesktop/0163381a22cb/python-env/python.exe"`，cwd 同上，**不需要 PYTHONPATH**。
- 改动 `webui.json` 后必须过宿主 schema（命令在 `docs/webui-extension.md` 第 7 节末尾）。

## 2. 硬规则（违反即事故）

1. **没有所有人的明确允许，不得 `git push`、不得建 GitHub Release。** 提交和打 tag 可以留在本地，推送永远等一句话。
2. **README.md 不许动**（所有人为维护）。CHANGELOG.md、CONTRIBUTING.md、docs/ 可以改。
3. **`HANDOVER.md` 永不入 git**（已在 .gitignore，别手动 add）。
4. **`config.toml` 不入 git**（Runner 按 config_model 生成）；只改 `config.toml.example`。
5. **`main` 分支永远不含 `tests/`**（分发版）。同步方式：`git checkout main && git merge --no-ff dev && git rm -r tests`，把删除折叠进合并提交。
6. **测试不许为了通过而弱化**。意图改变的断言要重写并注明理由；不确定就问，不许放水。
7. 改完 `_manifest.json` 必须验 JSON；改版本号必须同步全部 6 处（见第 4 节）。

## 3. 本仓库特有的坑（都踩过）

- **CRLF 行尾**：仓库里 `.py`/`.json`/`.md` 大多是 CRLF。用字符串匹配改文件时锚点带 `\r` 会静默失败——要么按行处理、要么用 Python 读写整个文件，别用单次字符串替换猜行尾。
- **改文件用 Python 二进制读写更稳**：本环境的编辑工具偶尔会把直引号规范化成弯引号（Python 直接语法错误），改完务必 `py_compile`。
- **两套 SDK**：开发用 2.8.2 副本，部署是 2.12.0。行为差异集中在 `llm.generate` 的 `task_name` 契约和 WebUI 协议版本（现在是 v2：`schema_version: 2`，无 repeat/gallery/upload，无 `scope:"item"`）。
- **WebUI 协议以宿主源码为准**，不是公开文档：`C:/Users/octmicy/AppData/Roaming/MaiBotOneKeyDesktop/0163381a22cb/modules/MaiBot/src/plugin_runtime/webui_schema.py`。协议变更时"文档路径真实性 + 模块覆盖"这类机械核查会被宿主打脸。
- **休息闸门有两条独立触发路径**：时间窗 **OR** `gate_segment_types`（默认含 sleep/nap）。改夜窗开始时间不管用，是因为日程一进睡眠段闸门就生效——详见 `docs/plugin-guide.md` 第 4 章。
- **测试替身不全**：本机默认 Python 没有 `maibot_sdk`，子代理用桩跑全量会出现大量环境性失败；判回归要用 HEAD 快照对照，别把环境噪声当回归。
- **`/workflow` 跑动态工作流时**：`world.run` 的 cwd 不保证是仓库根，命令里用绝对路径；门禁逻辑先在小片段里试通再进脚本。
- **麦麦绘图联动两条隐性耦合**：引导注入要求 payload 非空，而 payload 只在 `context.enabled=True` 且非群被动轮构建（on_planner 构建分支，plugin.py:1255-1257）——关掉上下文配置，联动静默消失；drawpic 检测是 10 分钟一复检的内存态，注入期另查 `draw` 工具是否在本轮工具定义里（零 RPC），关掉的是「插件中途卸载后的陈旧窗口」。详见 `docs/plugin-guide.md` §11.5。

## 4. 发版流程（版本号 6 处 + CHANGELOG）

改 `PLUGIN_VERSION`（`config.py:15`）时同步这 6 处，`tests/test_contract.py` 会替你守住一致性：

1. `config.py` 的 `PLUGIN_VERSION`
2. `_manifest.json` 的 `version`
3. `plugin.py` 模块 docstring 的版本号
4. `tests/test_contract.py` 的 manifest 断言
5. `tests/test_contract.py` 的 PLUGIN_VERSION 断言与注释
6. `tests/test_command_menu.py` 的 manifest 断言

然后：CHANGELOG 顶部加条目（新增/修复/说明三段式，写清"为什么"和已知限制）→ 全量测试 → `compileall` → commit（`release: vX.Y.Z <主题>`）→ `git tag -a vX.Y.Z`。**推送和 Release 等允许。**

配置字段增删不需要动 `CONFIG_SCHEMA_VERSION`（纯新增有默认值）；DB schema 变更才动 `SCHEMA_VERSION` 并写迁移。

## 5. 提交前自查清单

- [ ] 全量测试全绿（cwd 是父目录）
- [ ] `python -m compileall Mai_life` 通过
- [ ] 改了 `webui.json` → 宿主 schema 校验通过 + `V11417WebuiProtocolV2Tests` 与 `V11412WebuiJsonStructureTests` 两个类绿
- [ ] 改了配置/API → `test_contract.py` 绿（它守着版本号、API 注册形态、配置摘要键集）
- [ ] 改了提示词 → `test_fixes.py` 里对应的注入点断言已同步
- [ ] 版本号 6 处一致 + CHANGELOG 已写
- [ ] 没有碰 README / HANDOVER.md / config.toml
- [ ] 工作区没有误入的临时文件（`webui_*_backup.json`、`_draft_*.md` 之类已在 .gitignore）

## 6. 代码风格（延续现有代码）

- 紧凑、分号连写、少空行——**跟着相邻代码的密度走**，别为你的新函数换风格。
- 注释只写代码表达不出来的约束（为什么、坑、协议来源）；不写"这行在干什么"。
- 公开行为变更必须落在 CHANGELOG；内部重构不必。
- 失败要暴露，不要静默兜底；兜底必须留日志（参考 `core/llm_service.py` 的降级告警）。

## 7. 去哪查

| 想知道什么 | 去哪 |
|---|---|
| 架构 / 模块职责 / 表结构 / 消息链路 | `docs/plugin-guide.md`（主地图，先读它） |
| WebUI 协议与页面设计 | `docs/webui-extension.md` + 宿主 `webui_schema.py` |
| 某个配置项的含义与默认值 | `config.py` 对应 Settings 类的 `_ui(...)` |
| 测试怎么组织的 | `tests/test_fixes.py` 按版本分节，节头有注释 |
| 发版与分支细节 | `CONTRIBUTING.md` |
| 麦麦绘图联动全链（检测/信号/注入/展示） | `docs/plugin-guide.md` §11.5 |
