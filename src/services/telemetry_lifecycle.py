"""按配置启停遥测任务，停用时不加载上传实现。"""

from importlib import import_module
from typing import TYPE_CHECKING, Dict, Sequence

import asyncio
import logging

from src.common.runtime_loop import run_on_main_loop

if TYPE_CHECKING:
    from src.config.config import ConfigManager

logger = logging.getLogger("telemetry_lifecycle")


def _get_config_manager() -> "ConfigManager":
    """避免仅导入生命周期服务就读取实例配置。"""

    from src.config.config import config_manager

    return config_manager


class TelemetryLifecycleService:
    """在主循环持有两个遥测任务，保留配置热更新能力。"""

    def __init__(self) -> None:
        self._started = False
        self._lock = asyncio.Lock()
        self._abort_flag = asyncio.Event()
        self._tasks: Dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        """注册热更新并按当前开关启动任务；重复调用不重复注册。"""

        await run_on_main_loop(self._start())

    async def _start(self) -> None:
        async with self._lock:
            if self._started:
                return
            manager = _get_config_manager()
            manager.register_reload_callback(self.on_config_reload, sections=("telemetry",))
            self._started = True
            try:
                await self._reconcile_locked()
            except BaseException:
                self._started = False
                manager.unregister_reload_callback(self.on_config_reload)
                await self._cancel_tasks_locked()
                raise

    async def on_config_reload(self, changed_scopes: Sequence[str] = ()) -> None:
        """配置监听器或 WebUI 均可通知，任务操作始终回到主循环。"""

        if changed_scopes and "bot" not in changed_scopes:
            return
        await run_on_main_loop(self._apply_config_reload())

    async def _apply_config_reload(self) -> None:
        async with self._lock:
            # 退出前已投递的回调也不能重新拉起任务。
            if self._started:
                await self._reconcile_locked()

    async def _reconcile_locked(self) -> None:
        enabled = _get_config_manager().get_global_config().telemetry.enable
        if not enabled:
            await self._cancel_tasks_locked()
            return
        if len(self._tasks) == 2 and all(not task.done() for task in self._tasks.values()):
            return

        await self._cancel_tasks_locked()
        # 上传依赖、持久化存储与统计查询实现只在开启时加载；导入不阻塞主循环。
        remote = await asyncio.to_thread(import_module, "src.common.remote")
        telemetry_tasks = (remote.TelemetryHeartBeatTask(), remote.TelemetryStatsUploadTask())
        self._abort_flag.clear()
        for telemetry_task in telemetry_tasks:
            task = asyncio.create_task(
                telemetry_task.start_task(self._abort_flag),
                name=telemetry_task.task_name,
            )
            task.add_done_callback(self._task_finished)
            self._tasks[telemetry_task.task_name] = task

    @staticmethod
    def _task_finished(task: asyncio.Task[None]) -> None:
        """后台异常立即上报，不等到下次开关变化才发现。"""

        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error(
                "遥测后台任务失败: %s",
                task.get_name(),
                exc_info=(type(exception), exception, exception.__traceback__),
            )
        else:
            logger.error("遥测后台任务意外结束: %s", task.get_name())

    async def _cancel_tasks_locked(self) -> None:
        self._abort_flag.set()
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            # 每个非取消异常由完成回调记录；gather 确保全部任务结束并回收异常。
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        """注销配置通知，取消并等待所有上传或重试任务。"""

        await run_on_main_loop(self._stop())

    async def _stop(self) -> None:
        async with self._lock:
            if self._started:
                self._started = False
                _get_config_manager().unregister_reload_callback(self.on_config_reload)
            await self._cancel_tasks_locked()


telemetry_lifecycle_service = TelemetryLifecycleService()
