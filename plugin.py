"""SVG 简笔画生成器插件 - 用于生成和渲染 SVG 图像。"""

import asyncio
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

from maibot_sdk import MaiBotPlugin, PluginConfigBase, Tool, Field
from maibot_sdk.types import ToolParameterInfo, ToolParamType


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class RenderSectionConfig(PluginConfigBase):
    """渲染配置。"""

    __ui_label__ = "渲染"
    __ui_icon__ = "image"
    __ui_order__ = 1

    max_pixel: int = Field(default=800, description="生成图片长边的最大尺寸（像素），宽高比由 LLM 控制，长边将精确缩放为该值")
    background_enabled: bool = Field(default=True, description="是否启用默认背景色")
    background_color: str = Field(default="#ffffff", description="默认背景色（颜色名或十六进制，如 #ffffff）")


class BehaviorSectionConfig(PluginConfigBase):
    __ui_label__ = "绘画行为"
    cooldown_seconds: int = Field(default=300, description="同一会话成功发图后的冷却秒数，至少30秒")
    repeat_hours: int = Field(default=24, description="同一会话相同图像代码的重复抑制小时数，1至168")
    render_timeout_seconds: int = Field(default=8, description="独立渲染进程超时，2至15秒")


class EasySVGCreatorConfig(PluginConfigBase):
    """SVG 简笔画生成器插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    render: RenderSectionConfig = Field(default_factory=RenderSectionConfig)
    behavior: BehaviorSectionConfig = Field(default_factory=BehaviorSectionConfig)


class EasySVGCreatorPlugin(MaiBotPlugin):
    """SVG 简笔画生成器插件。支持生成和渲染 SVG 图像。"""

    config_model = EasySVGCreatorConfig

    async def on_load(self) -> None:
        """插件加载时执行。"""
        self._guard = asyncio.Lock()
        self._processes = set()
        self._state_path = Path(self.ctx.paths.data_dir) / "drawing-state.json"
        self._state = await asyncio.to_thread(self._read_state)
        self.ctx.logger.info("SVG 简笔画生成器插件已加载；独立渲染进程、冷却与重复抑制已启用")

    async def on_unload(self) -> None:
        """插件卸载时执行。"""
        for process in tuple(self._processes):
            if process.returncode is None:
                process.kill()
            await process.wait()
        self.ctx.logger.info("SVG 简笔画生成器插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热重载时执行。"""
        del config_data
        self.ctx.logger.info(f"SVG 简笔画生成器配置已更新 (scope={scope}, version={version})")

    def _read_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"last_sent": {}, "recent": {}}
        state = json.loads(self._state_path.read_text())
        if not isinstance(state.get("last_sent"), dict) or not isinstance(state.get("recent"), dict):
            raise ValueError("Invalid drawing state")
        return state

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            os.chmod(temporary, 0o600)
            json.dump(self._state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._state_path)

    @staticmethod
    def _prepare_svg(svg_code: str, max_pixel: int) -> str:
        # Small bounded XML input; never resolve filesystem/network resources.
        if len(svg_code.encode("utf-8")) > 50000:
            raise ValueError("SVG 超过50KB，请简化画面")
        if re.search(r"<!DOCTYPE|<!ENTITY|<\?", svg_code, re.I):
            raise ValueError("不支持DTD、实体或处理指令")
        root = ET.fromstring(svg_code)
        if root.tag not in {"svg", "{http://www.w3.org/2000/svg}svg"}:
            raise ValueError("根节点必须是SVG")
        if root.tag == "svg":
            root.set("xmlns", "http://www.w3.org/2000/svg")
        nodes = list(root.iter())
        if len(nodes) > 2000:
            raise ValueError("SVG节点过多，请简化画面")
        for node in nodes:
            tag = node.tag.rsplit("}", 1)[-1].lower()
            if tag in {"script", "foreignobject", "image", "feimage", "style", "animate", "animatetransform", "set"}:
                raise ValueError("只支持静态矢量绘画和文字")
            for key, value in node.attrib.items():
                name = key.rsplit("}", 1)[-1].lower()
                if name.startswith("on") or (name == "href" and not value.startswith("#")):
                    raise ValueError("不支持外部资源或事件")
                if "url" in value.lower() and not re.fullmatch(r"url\(#[A-Za-z0-9_.:-]+\)", value):
                    raise ValueError("只支持内部SVG资源引用")
        viewbox = root.get("viewBox", "").replace(",", " ").split()
        def dimension(name: str, index: int) -> float:
            value = root.get(name, "")
            if value:
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:px)?", value):
                    raise ValueError("宽高应为正数或px")
                number = float(value.removesuffix("px"))
            elif len(viewbox) == 4:
                number = float(viewbox[index])
            else:
                raise ValueError("请设置width/height或viewBox")
            if not math.isfinite(number) or not 0 < number <= 100000:
                raise ValueError("尺寸无效")
            return number
        width, height = dimension("width", 2), dimension("height", 3)
        limit = max(64, min(int(max_pixel), 1024))
        scale = limit / max(width, height)
        root.set("width", str(max(1, round(width * scale))))
        root.set("height", str(max(1, round(height * scale))))
        if not viewbox:
            root.set("viewBox", f"0 0 {width} {height}")
        elif len(viewbox) != 4 or not all(math.isfinite(float(n)) for n in viewbox) or float(viewbox[2]) <= 0 or float(viewbox[3]) <= 0:
            raise ValueError("viewBox无效")
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        return ET.tostring(root, encoding="unicode")

    async def _render_png(self, svg_code: str) -> bytes:
        timeout = max(2, min(15, self.config.behavior.render_timeout_seconds))
        background = self.config.render.background_color if self.config.render.background_enabled else ""
        worker = Path(__file__).with_name("render_worker.py")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(worker), background,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self._processes.add(process)
        try:
            output, error = await asyncio.wait_for(process.communicate(svg_code.encode()), timeout)
            if process.returncode != 0:
                raise RuntimeError("渲染进程失败：" + error.decode(errors="replace")[-160:])
            if not output.startswith(b"\x89PNG\r\n\x1a\n") or len(output) > 8 * 1024 * 1024:
                raise RuntimeError("渲染输出无效或过大")
            return output
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            self._processes.discard(process)

    @Tool(
        "generate_svg",
        brief_description=(
            "生成并渲染 SVG 图像。当用户请求创建任何矢量图形（矩形、圆形、三角形、线条、"
            "图标、图表、插画等）时，或你想顺着当前话题偶尔分享一张自己的小涂鸦时，编写SVG并调用本工具发图。不要为用工具而打断聊天，不要每次提到绘画都调用。"
        ),
        detailed_description=(
            "用户请你画图，或当前话题让你自然想分享一张小涂鸦时，可以自主使用。画面可以贴合自己的兴趣，但不要虚构用户要求、共同经历或代替用户画其私人信息。工具会将SVG本地渲染为PNG并发送。\n"
            "不要在回复中输出诸如 \"/svg xxx\" 之类的命令文本，也不要直接把 SVG 源码贴给用户，请始终通过本工具生成图片。\n\n"
            "使用步骤：\n"
            "1. 根据用户需求编写 SVG 代码（必须包含 <svg> 根标签，并设置 width 与 height；宽高比由你决定，\n"
            "   渲染时会按长边等比缩放到配置的 max_pixel，无需刻意写很大的尺寸）。\n"
            "2. 调用 generate_svg，将代码放入 svg_code 参数，可选的 description 用于简要说明图像内容。\n\n"
            "常用图形示例：\n"
            "- 矩形：<svg width=\"200\" height=\"160\" xmlns=\"http://www.w3.org/2000/svg\"><rect x=\"20\" y=\"30\" width=\"160\" height=\"100\" rx=\"10\" fill=\"#4A90D9\" stroke=\"#2C5F8A\" stroke-width=\"3\"/></svg>\n"
            "- 圆形：<svg width=\"200\" height=\"200\" xmlns=\"http://www.w3.org/2000/svg\"><circle cx=\"100\" cy=\"100\" r=\"80\" fill=\"#E74C3C\"/></svg>\n"
            "- 带文字：<svg width=\"320\" height=\"120\" xmlns=\"http://www.w3.org/2000/svg\"><text x=\"160\" y=\"70\" font-size=\"36\" text-anchor=\"middle\" fill=\"#333\">你好，世界</text></svg>\n\n"
            "注意事项：\n"
            "- svg_code 必须是合法、完整的 SVG/XML 代码。\n"
            "- 颜色可自由使用颜色名（如 red、blue）或十六进制值（如 #FF0000）。\n"
            "- 根据用户对尺寸、颜色、样式的描述合理设置 width、height 与各属性。"
        ),
        parameters=[
            ToolParameterInfo(
                name="svg_code",
                param_type=ToolParamType.STRING,
                description="完整的 SVG 代码，必须包含 <svg> 根标签并设置 width/height。例如：<svg width=\"200\" height=\"200\" xmlns=\"http://www.w3.org/2000/svg\"><rect x=\"20\" y=\"30\" width=\"160\" height=\"100\" fill=\"blue\" rx=\"8\"/></svg>",
                required=True,
            ),
            ToolParameterInfo(
                name="description",
                param_type=ToolParamType.STRING,
                description="对生成的图像的简要说明，用于消息展示（可选）",
                required=False,
            ),
        ],
        core_tool=True,
    )
    async def generate_svg(self, svg_code: str, description: str = "", stream_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """生成并发送 SVG 图像。

        Args:
            svg_code: SVG 代码字符串
            description: 图像描述
            stream_id: 消息流 ID（用于直接发送图片）
            **kwargs: 其他参数

        Returns:
            包含结果的字典
        """
        del kwargs
        if not self.config.plugin.enabled:
            return {"status": "disabled", "image_sent": False, "content": "绘画已停用"}
        if not stream_id:
            return {"status": "error", "image_sent": False, "content": "缺少真实会话，不发送图像"}
        if self._guard.locked():
            return {"status": "busy", "image_sent": False, "content": "正在画另一张，不要立即重试"}
        async with self._guard:
            try:
                svg = await asyncio.to_thread(self._prepare_svg, svg_code, self.config.render.max_pixel)
                stream = hashlib.sha256(stream_id.encode()).hexdigest()
                digest = hashlib.sha256(svg.encode()).hexdigest()
                key = stream + ":" + digest
                now = time.time()
                cooldown = max(30, self.config.behavior.cooldown_seconds)
                repeat = max(1, min(168, self.config.behavior.repeat_hours)) * 3600
                if now - self._state["recent"].get(key, 0) < repeat:
                    return {"status": "skipped", "reason": "duplicate", "image_sent": False, "content": "这张图最近发过，不重复发送，也不要立即重画绕过限制"}
                if now - self._state["last_sent"].get(stream, 0) < cooldown:
                    return {"status": "skipped", "reason": "cooldown", "image_sent": False, "content": "发图冷却中，继续正常聊天，不要立即重试"}
                png = await self._render_png(svg)
                sent = await self.ctx.send.image(base64.b64encode(png).decode(), stream_id, return_details=True)
                if not (sent.get("sent", False) if isinstance(sent, dict) else sent is True):
                    return {"status": "error", "image_sent": False, "content": "平台未确认图片发送成功"}
                # Count only platform-confirmed delivery, persist across restarts.
                self._state["last_sent"][stream] = time.time()
                self._state["recent"][key] = time.time()
                for mapping in self._state.values():
                    for old in [k for k,v in mapping.items() if now - v > max(repeat, cooldown)]:
                        del mapping[old]
                try:
                    await asyncio.to_thread(self._save_state)
                except OSError:
                    self.ctx.logger.exception("图片已发送，但冷却状态保存失败；本进程内仍抑制重复")
                return {"status": "success", "image_sent": True, "content": "画已发出，不要再次调用或重复发图。若需要文字，最多一句自然的短话；也可以不再说话。不要输出SVG源码。"}
            except asyncio.TimeoutError:
                return {"status": "error", "image_sent": False, "content": "画面渲染超时，请先正常聊天，不要连续重试"}
            except (ValueError, ET.ParseError) as exc:
                return {"status": "error", "image_sent": False, "content": str(exc)}
            except Exception:
                self.ctx.logger.exception("SVG绘画失败")
                return {"status": "error", "image_sent": False, "content": "绘画失败，没有发送图片"}



def create_plugin() -> EasySVGCreatorPlugin:
    """创建 SVG 简笔画生成器插件实例。"""
    return EasySVGCreatorPlugin()
