# SVG 简笔画生成器

## 功能介绍

想让你的麦麦拥有小孩般的绘画能力吗？

SVG 简笔画生成器是一个 MaiBot 第三方插件，用于一句话生成和渲染简单的 SVG 图像。该插件可以由 LLM 自主调用，实现动态创建各种矢量图形。

### 核心能力

- **生成 SVG 图像** - 接收 SVG 代码字符串，直接转换为 PNG 图片发送
- **LLM 集成** - 完全支持 LLM 自主调用生成图形（`generate_svg` 为核心工具，对 LLM 始终可见，无需搜索即可调用）
- **图片输出** - 所有生成的 SVG 自动渲染为高质量 PNG 图片（默认白色背景）

## 依赖安装

**此插件需要 resvg-py 库来将 SVG 转换为 PNG 图片。必须安装才能使用。**
`resvg-py` 基于 Rust，自带原生渲染库，**无需在系统上额外安装 cairo 等图形库**，Windows / Linux / macOS 均可直接使用。

```bash
# 安装必需依赖
pip install resvg-py>=0.4.0
```

如果未安装 `resvg-py`，调用工具时会返回错误提示，请按提示安装依赖。

## 安装方式

1. 将插件目录放入 MaiBot 的 `plugins/` 文件夹
2. 在命令行安装依赖：`pip install resvg-py>=0.4.0`
3. 启动或重启 MaiBot
4. 在 WebUI 插件管理中启用 "SVG 简笔画生成器"

```bash
# 完整安装步骤
cp -r maibot-easysvg-creator /path/to/MaiBot/plugins/
cd /path/to/MaiBot
pip install resvg-py>=0.4.0
```

## 配置

插件启用后会自动生成 `config.toml` 文件。默认配置如下：

```toml
[plugin]
enabled = true
config_version = "1.0.0"

[render]
max_pixel = 800
background_enabled = true
background_color = "#ffffff"
```

### 配置项说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `plugin.enabled` | 是否启用插件 | `true` |
| `plugin.config_version` | 配置版本号 | `1.0.0` |
| `render.max_pixel` | 生成图片长边的最大尺寸（像素）；宽高比由 LLM 指定，长边将精确缩放为该值（如 `max_pixel=800` 时，可得到 `800x640` 或 `400x800`） | `800` |
| `render.background_enabled` | 是否启用默认背景色；关闭后背景保持透明 | `true` |
| `render.background_color` | 默认背景色（颜色名或十六进制） | `#ffffff` |

配置修改后在 WebUI 保存即可热重载，无需重启 MaiBot。

## 使用方法

### 1. 生成 SVG 图像

**工具名称**: `generate_svg`

接收完整的 SVG 代码字符串，验证后发送给用户。

**参数**:
- `svg_code` (必需) - 完整的 SVG 代码，必须包含 `<svg>` 根标签
- `description` (可选) - 对生成图像的简单描述

**示例**:
```python
# LLM 调用示例
await plugin.generate_svg(
    svg_code='<svg width="200" height="200" xmlns="http://www.w3.org/2000/svg"><circle cx="100" cy="100" r="80" fill="red"/></svg>',
    description="一个红色的圆"
)
```

**返回结果**:
```json
{
  "content": "✅ SVG 图像已生成\n描述: 一个红色的圆",
  "status": "success",
  "svg_code": "<svg width=\"200\" height=\"200\">...</svg>",
  "image_base64": "iVBORw0KGgoAAAANS..."
}
```

或当 stream_id 存在时，图片会直接发送到聊天：
```json
{
  "content": "✅ SVG 图像已生成并发送\n描述: 一个红色的圆",
  "status": "success",
  "svg_code": "<svg width=\"200\" height=\"200\">...</svg>",
  "image_sent": true
}
```

## LLM 集成

该插件的 `@Tool` 装饰器工具已注册到 MaiBot SDK。`generate_svg` 被声明为核心工具（`core_tool=True`），会**始终出现在 LLM 的工具列表中**，无需搜索即可发现和调用：

```
在对话中，用户可以请求 LLM 生成图形：
用户: "帮我生成一个 200px 的红色圆形"
LLM: 构造 SVG 代码并调用 generate_svg(svg_code="<svg ...><circle .../></svg>", description="一个红色圆形")
MaiBot: 发送渲染后的 SVG 图像（工具直接发送图片，LLM 应只做极简确认或保持沉默，避免重复发送消息）
```

## 测试方式

### 1. 静态检查
```bash
# 验证 _manifest.json
python -m json.tool _manifest.json

# 验证 plugin.py 可导入
python -c "from plugin import create_plugin; print(create_plugin())"
```

### 2. 本地加载测试
1. 启动 MaiBot
2. 观察日志确认插件加载: `SVG 简笔画生成器插件已加载`
3. 在 WebUI 插件管理中确认插件出现

### 3. 功能测试
在 MaiBot 对话中测试：

```
# 测试生成 SVG
/test generate_svg '<svg width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>'
```

### 4. LLM 测试
```
用户: "生成一个 300px 的蓝色矩形"
预期: LLM 直接调用 generate_svg 工具生成矩形，而不是回复 "/svg ..." 之类的命令文本
```

### 5. 卸载测试
1. 禁用或卸载插件
2. 观察日志确认: `SVG 简笔画生成器插件已卸载`
3. 确认没有后台任务继续运行

## 常见问题

### Q: 显示缺少 resvg-py 依赖，如何解决？
**A**: 这是必需的依赖。请安装：
```bash
pip install resvg-py>=0.4.0
```
安装后重启 MaiBot。

### Q: 为什么总是报错 "缺少依赖"？
**A**: 
1. 确保已安装 resvg-py：`pip install resvg-py`
2. 如果使用虚拟环境（如 MaiBot OneKey Desktop），确保安装到了 MaiBot 使用的 Python 环境中
3. 重启 MaiBot 后错误仍然出现？检查 MaiBot 日志查看具体错误信息

### Q: 能否只返回 SVG 代码而不转换为图片？
**A**: 目前不支持。插件设计为总是生成 PNG 图片。如果需要 SVG 代码，可以从返回结果的 `svg_code` 字段获取。



## 许可证

MIT License - 详见 LICENSE 文件

## 作者

- spica - GitHub: https://github.com/sp1ca0722

## 贡献

欢迎提交 Issue 和 Pull Request！

## 相关文档

- [MaiBot 开发指南](https://docs.mai-mai.org/plugin/)
- [SVG 官方文档](https://developer.mozilla.org/zh-CN/docs/Web/SVG)
- [Vibe Coding 指南](https://docs.mai-mai.org/plugin/vibe-coding)

## Companion deployment changes (local development branch)

This variant allows occasional context-relevant drawings without waiting for an explicit request. It does not schedule drawing, force every conversation to use images, or call another model provider. The host's existing chat model writes SVG.

```toml
[behavior]
cooldown_seconds = 300
repeat_hours = 24
render_timeout_seconds = 8
```

Cooldown and duplicates apply per actual conversation and persist under the SDK data directory. Repetition means the same normalized SVG code, not perceptual similarity. Only confirmed image delivery consumes cooldown. Missing conversation IDs are rejected; no base64 image is returned into the LLM context.

Rendering runs in a separate process (one drawing at a time); maximum 50KB SVG, 2,000 XML nodes, 1,024 pixels on the longer side and 8MiB PNG. Linux adds 512MiB memory and 5 seconds CPU limits. No external images/resources, scripting or animation. The existing `max_pixel` default is 800. Dependencies are pinned to `resvg-py==0.4.0`.

The original README examples describing `svg_code` and `image_base64` in tool results refer to upstream. This variant returns concise send status only. It requires a real conversation context and never returns an unsent image as base64.

Test without any QQ delivery or external LLM request:

```sh
REAL_RENDER=1 python -m unittest discover -s test -v
```
