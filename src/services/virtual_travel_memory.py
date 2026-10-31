"""虚拟自身经历到长期记忆的最小桥接。

旅行插件只提供已发生、带来源的事件；本桥接验证归属后调用现有
``memory_service.ingest_summary``，用稳定 ``external_id`` 去重，
确认成功后才让插件停止重复投递。
"""

from __future__ import annotations

import asyncio
import logging

from typing import Any, Awaitable, Callable

logger = logging.getLogger("virtual_travel_memory")

PLUGIN_ID = "local.virtual-travel"
SYNTHETIC_CHAT_ID = "virtual-travel"
DEFAULT_INTERVAL = 300.0
EVENT_SUBJECT = "bot"
EVENT_SOURCE_KIND = "virtual_self_event"


class VirtualTravelMemoryBridge:
    """可测试的桥接核心；实际 RPC 与记忆写入由注入的适配器完成。"""

    def __init__(
        self,
        call_api: Callable[[str, dict[str, Any]], Awaitable[Any]],
        ingest: Callable[..., Awaitable[Any]],
        *,
        interval: float = DEFAULT_INTERVAL,
        limit: int = 20,
    ) -> None:
        self.call_api = call_api
        self.ingest = ingest
        self.interval = interval
        self.limit = limit

    async def flush(self, now: float | None = None) -> dict[str, Any]:
        response = await self.call_api(
            "get_completed_travel_events", {"cursor": "", "limit": self.limit}
        )
        if not isinstance(response, dict):
            return {"skipped": "invalid_response"}
        # 独立开关默认关闭；只有插件配置明确开启时才允许写长期记忆。
        if response.get("export_enabled") is not True:
            return {"skipped": "export_disabled"}
        items = response.get("items")
        if not isinstance(items, list) or not items:
            return {"stored": 0, "acked": 0, "failed": []}

        stored: list[str] = []
        failed: list[str] = []
        for item in items:
            event = self._normalize_event(item)
            if event is None:
                continue
            external_id = f"virtual-travel:{event['event_id']}"
            try:
                result = await self.ingest(
                    external_id=external_id,
                    chat_id=SYNTHETIC_CHAT_ID,
                    text=event["text"],
                    participants=["bot"],
                    time_start=event["at"],
                    time_end=event["at"],
                    metadata=event["metadata"],
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不阻断其余事件
                logger.warning("虚拟旅行记忆写入异常: %s", exc)
                failed.append(event["event_id"])
                continue
            if not _write_succeeded(result):
                failed.append(event["event_id"])
                continue
            stored.append(event["event_id"])

        acked = 0
        if stored:
            ack = await self.call_api("ack_travel_events", {"event_ids": stored})
            if isinstance(ack, dict) and ack.get("acked") is not None:
                acked = int(ack.get("acked") or 0)
        return {"stored": len(stored), "acked": acked, "failed": failed}

    @staticmethod
    def _normalize_event(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        if str(raw.get("subject") or "") != EVENT_SUBJECT:
            return None
        if str(raw.get("source_kind") or "") != EVENT_SOURCE_KIND:
            return None
        event_id = str(raw.get("event_id") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        try:
            at = float(raw.get("at"))
        except (TypeError, ValueError):
            return None
        if not event_id or not summary:
            return None
        timezone = str(raw.get("timezone") or "")
        metadata = {
            "subject": EVENT_SUBJECT,
            "source_kind": EVENT_SOURCE_KIND,
            "event_at": at,
            "timezone": timezone,
            "trip_id": str(raw.get("trip_id") or ""),
            "segment_id": str(raw.get("segment_id") or ""),
        }
        text = f"{summary}（{timezone}）" if timezone else summary
        return {"event_id": event_id, "at": at, "text": text, "metadata": metadata}


def _write_succeeded(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("success"))
    return bool(getattr(result, "success", False))


async def _call_plugin_api(component_name: str, args: dict[str, Any]) -> Any:
    from src.plugin_runtime.integration import get_plugin_runtime_manager

    runtime = get_plugin_runtime_manager()
    for supervisor in runtime.supervisors:
        entry = supervisor.api_registry.get_api(PLUGIN_ID, component_name, version="1")
        if entry is None or not entry.public:
            continue
        response = await supervisor.invoke_api(
            plugin_id=PLUGIN_ID,
            component_name=entry.handler_name,
            args=args,
            timeout_ms=10_000,
        )
        if response.error:
            return {"success": False, "error": str(response.error)}
        payload = response.payload
        if isinstance(payload, dict) and "result" in payload:
            payload = payload["result"]
        return payload
    return {"success": False, "error": "plugin_not_loaded"}


async def _ingest_summary(**kwargs: Any) -> Any:
    from src.services.memory_service import memory_service

    return await memory_service.ingest_summary(**kwargs)


_bridge_task: asyncio.Task | None = None


def start_virtual_travel_memory_bridge() -> asyncio.Task:
    """启动周期性投递；默认关闭由插件配置决定，未开启时只读跳过。"""

    global _bridge_task
    if _bridge_task is not None and not _bridge_task.done():
        return _bridge_task
    bridge = VirtualTravelMemoryBridge(_call_plugin_api, _ingest_summary)
    _bridge_task = asyncio.create_task(_run_bridge(bridge), name="virtual-travel-memory")
    return _bridge_task


async def _run_bridge(bridge: VirtualTravelMemoryBridge) -> None:
    while True:
        try:
            result = await bridge.flush()
            if result.get("stored") or result.get("failed"):
                logger.info("虚拟旅行记忆投递: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台循环不能被单次失败终止
            logger.warning("虚拟旅行记忆投递失败: %s", exc)
        await asyncio.sleep(bridge.interval)
