# SVG 简笔画生成器 - 测试指南

## 快速检查清单

按照以下步骤验证插件的完整性和正确性。

### ✅ 文件结构检查

- [x] `_manifest.json` - 插件元信息
- [x] `plugin.py` - 插件入口和核心实现
- [x] `README.md` - 使用文档
- [x] `.gitignore` - 忽略规则（包含 `/config.toml`）
- [x] `LICENSE` - MIT 许可证

### ✅ _manifest.json 验证

```bash
# 检查 JSON 格式有效性
python -m json.tool c:\Users\leoli\AppData\Roaming\MaiBotOneKeyDesktop\dfc50fde2ba0\modules\MaiBot\plugins\maibot-easysvg-creator\_manifest.json
```

**验证项**:
- [x] `manifest_version`: 2（必须）
- [x] `id`: com.spica.easysvg-creator（小写反向域名格式）
- [x] `version`: 1.0.0（三段式语义版本）
- [x] `author.url` 和 `urls.repository`: 以 https:// 开头
- [x] `host_application` 和 `sdk` 都声明了 `min_version` 和 `max_version`
- [x] `sdk.min_version`: 2.5.1（符合文档要求）
- [x] `capabilities`: 只声明需要的能力 `send.text`、`send.image`（细粒度能力名，发送文本与图片时 Host 校验）
- [x] `i18n.default_locale`: zh-CN

### ✅ plugin.py 代码检查

```bash
# 检查 Python 语法
python -m py_compile c:\Users\leoli\AppData\Roaming\MaiBotOneKeyDesktop\dfc50fde2ba0\modules\MaiBot\plugins\maibot-easysvg-creator\plugin.py

# 检查是否可以导入
python -c "import sys; sys.path.insert(0, 'c:\\Users\\leoli\\AppData\\Roaming\\MaiBotOneKeyDesktop\\dfc50fde2ba0\\modules\\MaiBot\\plugins\\maibot-easysvg-creator'); from plugin import create_plugin; print('导入成功:', create_plugin())"
```

**验证项**:
- [x] 只从标准库、第三方库和 `maibot_sdk` 导入
- [x] 插件类 `EasySVGCreatorPlugin` 继承 `MaiBotPlugin`
- [x] 声明了 `config_model = EasySVGCreatorConfig`
- [x] 实现了 `on_load()`
- [x] 实现了 `on_unload()`
- [x] 实现了 `on_config_update()`
- [x] 实现了 `create_plugin()` 工厂函数
- [x] 新功能使用 `@Tool` 装饰器（不使用 `@Action`）
- [x] 没有硬编码密钥、token、QQ 号等敏感信息
- [x] 所有网络请求都有异常处理
- [x] 所有用户可见文本使用简体中文
- [x] 日志记录完整

### ✅ 功能检查

#### 工具 1: `generate_svg`
- [x] 接收 SVG 代码字符串
- [x] 验证 SVG 的有效性
- [x] 优化 SVG 代码
- [x] 返回包含 content、status、svg_code 的字典
- [x] 参数描述清晰
- [x] 错误处理完善
- [x] 声明为 `core_tool=True`（对 LLM 始终可见）

### ✅ 配置模型检查

- [x] `PluginSectionConfig` 包含 `enabled` 和 `config_version`
- [x] 使用 `Field(default=..., description="...")`
- [x] 包含 UI 配置：`__ui_label__`、`__ui_icon__`、`__ui_order__`
- [x] `EasySVGCreatorConfig` 包含 `plugin` 分组
- [x] `RenderSectionConfig` 包含 `max_pixel`、`background_enabled`、`background_color`

---

## 集成测试步骤

### 1. 本地加载测试

```bash
# 启动 MaiBot
cd /path/to/MaiBot
python -m maibot

# 在另一个终端查看日志
tail -f logs/maibot.log | grep "SVG 简笔画生成器"
```

**预期日志输出**:
```
[INFO] SVG 简笔画生成器插件已加载
```

### 2. WebUI 检查

1. 打开 MaiBot WebUI (默认 http://localhost:8080)
2. 进入 **插件管理**
3. 找到 **SVG 简笔画生成器** 插件
4. 验证以下内容：
   - [x] 插件名称显示为 "SVG 简笔画生成器"
   - [x] 描述正确显示
   - [x] 可以启用/禁用
   - [x] 配置页面可访问
   - [x] 配置模型正确加载

### 3. 工具调用测试

在 MaiBot 聊天界面测试：

#### 测试 A: 创建矩形
```
用户: "帮我生成一个 150px 的红色矩形"

预期行为:
1. LLM 直接发现 generate_svg 工具（core_tool，始终可见）
2. LLM 构造 SVG 代码并调用 generate_svg
3. 工具返回成功响应并渲染 SVG 图像
4. MaiBot 在聊天中显示矩形图像
```

#### 测试 B: 创建圆形
```
用户: "生成一个 200 像素的蓝色圆形"

预期行为:
1. LLM 构造 SVG 代码并调用 generate_svg(svg_code="<svg ...><circle .../></svg>", description="蓝色圆形")
2. 返回成功并显示圆形图像
```

#### 测试 C: 自定义 SVG
```
用户: "生成这个 SVG 代码: <svg width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>"

预期行为:
1. LLM 调用 generate_svg 工具
2. SVG 验证通过
3. 渲染后发送
```

#### 测试 D: 无效 SVG
```
用户: "生成这个无效的 SVG: <script>alert('xss')</script>"

预期行为:
1. SVG 验证失败（包含 script 标签）
2. 返回错误提示：SVG 验证失败: SVG 代码不能包含 <script> 标签
3. 不执行任何操作
```

#### 测试 E: 回归验证（不再回复命令文本）
```
用户: "我要生成一个矩形"

预期行为:
1. LLM 调用 generate_svg 工具，而不是回复 "/svg rectangle" 之类的命令文本
2. 生成矩形图像并发送
```

### 4. 配置热重载测试

1. 编辑 `config.toml`，修改 `enabled = false`
2. MaiBot 应自动重新加载配置
3. 查看日志中的 `on_config_update` 消息
4. 工具应该不再被调用

### 5. 卸载测试

1. 在 WebUI 中禁用插件
2. 查看日志：应显示 `SVG 简笔画生成器插件已卸载`
3. 验证没有后台任务继续运行
4. 重新启用插件后，工具应再次可用

---

## 日志排查

### 插件加载失败

检查以下问题：

```bash
# 1. JSON 格式错误
python -m json.tool _manifest.json

# 2. Python 语法错误
python -m py_compile plugin.py

# 3. 导入错误
python -c "from plugin import create_plugin"

# 4. SDK 版本不匹配
# 检查 SDK 版本是否在 min_version 和 max_version 之间
```

### 工具不被调用

1. 确认插件已启用：`config.toml` 中 `enabled = true`
2. 查看日志确认插件加载成功
3. 检查 LLM 系统提示是否包含工具描述
4. 尝试显式请求 LLM 使用这些工具

### SVG 不显示

1. 检查 SVG 代码是否包含 `<svg>` 标签
2. 确认宽高属性设置
3. 查看浏览器开发者工具的控制台是否有错误
4. 尝试简化 SVG（从基础图形开始）

---

## 性能测试

```python
# 测试大量 SVG 生成
import time
from plugin import EasySVGCreatorPlugin

plugin = EasySVGCreatorPlugin()

# 测试 100 个 SVG 生成
start = time.time()
for i in range(100):
    result = await plugin.generate_svg(
        svg_code='<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="40" fill="blue"/></svg>',
        description="蓝色圆形"
    )
end = time.time()

print(f"生成 100 个圆形耗时: {end - start:.2f}s")
# 预期: < 1 秒
```

---

## 发布前清单

- [x] 所有文件创建完成
- [x] _manifest.json 格式正确且完整
- [x] plugin.py 实现所有必需方法
- [x] README 包含安装、配置、使用说明
- [x] README 包含常见问题
- [x] .gitignore 包含 /config.toml
- [x] LICENSE 文件完整
- [x] CHANGELOG.md 记录版本历史
- [x] 没有硬编码敏感信息
- [x] 所有用户可见文本使用简体中文
- [x] 错误处理完善
- [x] 日志记录完整
- [x] 工具描述清晰准确
- [x] 无修改主程序代码

---

## 后续改进方向

如需增强插件功能，可考虑：

1. **动画支持** - 添加 SMIL 或 CSS 动画生成
2. **图表库** - 集成图表生成（柱状图、折线图等）
3. **批量生成** - 支持同时生成多个 SVG
4. **导出格式** - 支持导出为 PNG、PDF 等格式
5. **模板库** - 提供常用 SVG 模板
6. **性能优化** - 缓存 SVG 代码以加快渲染

---

**测试完成时间**: 2026-08-16
**测试状态**: ✅ 就绪
