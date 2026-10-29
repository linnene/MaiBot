"""遥测按需加载与双事件循环生命周期测试，不使用实例配置或网络。"""

from types import SimpleNamespace
from typing import Any, Callable, List

import asyncio
import logging
import subprocess
import sys

import pytest

from src.common.runtime_loop import get_main_loop, set_main_loop
from src.services import telemetry_lifecycle as lifecycle_module
from src.services.telemetry_lifecycle import TelemetryLifecycleService


class _ConfigManager:
    def __init__(self, enabled: bool = False) -> None:
        self.config = SimpleNamespace(telemetry=SimpleNamespace(enable=enabled))
        self.callbacks: List[Callable[..., Any]] = []
        self.registrations = 0

    def get_global_config(self) -> Any:
        return self.config

    def register_reload_callback(self, callback: Callable[..., Any], *, sections: tuple[str, ...]) -> None:
        assert sections == ("telemetry",)
        self.registrations += 1
        self.callbacks.append(callback)

    def unregister_reload_callback(self, callback: Callable[..., Any]) -> None:
        self.callbacks.remove(callback)


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    manager = _ConfigManager()
    created = []
    imports = []

    class _FakeTask:
        def __init__(self, task_name: str) -> None:
            self.task_name = task_name
            self.started = asyncio.Event()
            self.finished = asyncio.Event()
            self.loop = None
            created.append(self)

        async def start_task(self, abort_flag: asyncio.Event) -> None:
            self.loop = asyncio.get_running_loop()
            self.started.set()
            try:
                await abort_flag.wait()
            finally:
                # 异步清理必须被 stop 完整等待。
                await asyncio.sleep(0)
                self.finished.set()

    remote = SimpleNamespace(
        TelemetryHeartBeatTask=lambda: _FakeTask("heartbeat"),
        TelemetryStatsUploadTask=lambda: _FakeTask("statistics"),
    )

    def fake_import(name: str) -> Any:
        assert name == "src.common.remote"
        imports.append(name)
        return remote

    monkeypatch.setattr(lifecycle_module, "_get_config_manager", lambda: manager)
    monkeypatch.setattr(lifecycle_module, "import_module", fake_import)
    return SimpleNamespace(manager=manager, created=created, imports=imports, remote=remote)


def test_import_does_not_load_private_config_or_telemetry_upload_implementation() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from src.services.telemetry_lifecycle import TelemetryLifecycleService; "
            "TelemetryLifecycleService(); "
            "assert 'src.config.config' not in sys.modules; "
            "assert 'src.common.remote' not in sys.modules; "
            "assert 'src.manager.local_store_manager' not in sys.modules; "
            "assert 'src.services.telemetry_stats_service' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_initially_disabled_registers_once_without_import_or_tasks(runtime: SimpleNamespace) -> None:
    service = TelemetryLifecycleService()
    try:
        await service.start()
        await service.start()
        assert runtime.manager.registrations == 1
        assert len(runtime.manager.callbacks) == 1
        assert runtime.imports == []
        assert runtime.created == []
        assert service._tasks == {}
    finally:
        await service.stop()
    assert runtime.manager.callbacks == []


@pytest.mark.asyncio
async def test_enable_is_idempotent_and_disable_awaits_both_tasks(runtime: SimpleNamespace) -> None:
    service = TelemetryLifecycleService()
    try:
        await service.start()
        runtime.manager.config.telemetry.enable = True
        await service.on_config_reload(("bot",))
        await asyncio.gather(*(task.started.wait() for task in runtime.created))
        await service.on_config_reload(("bot",))
        assert len(runtime.imports) == 1
        assert len(runtime.created) == 2
        assert len(service._tasks) == 2

        runtime.manager.config.telemetry.enable = False
        await service.on_config_reload(("bot",))
        assert service._tasks == {}
        assert all(task.finished.is_set() for task in runtime.created)
        # 停用不注销回调，后续仍可热启用。
        assert len(runtime.manager.callbacks) == 1

        runtime.manager.config.telemetry.enable = True
        await service.on_config_reload(("bot",))
        assert len(runtime.created) == 4
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_stop_waits_for_cleanup_and_late_reload_cannot_restart(runtime: SimpleNamespace) -> None:
    runtime.manager.config.telemetry.enable = True
    service = TelemetryLifecycleService()
    await service.start()
    await asyncio.gather(*(task.started.wait() for task in runtime.created))
    late_callback = runtime.manager.callbacks[0]
    await service.stop()
    assert all(task.finished.is_set() for task in runtime.created)
    assert runtime.manager.callbacks == []
    await late_callback(("bot",))
    await service.stop()
    assert len(runtime.created) == 2
    assert service._tasks == {}


@pytest.mark.asyncio
async def test_explicit_start_after_stop_starts_one_new_generation(runtime: SimpleNamespace) -> None:
    runtime.manager.config.telemetry.enable = True
    service = TelemetryLifecycleService()
    try:
        await service.start()
        await asyncio.gather(*(task.started.wait() for task in runtime.created))
        await service.stop()
        await asyncio.gather(service.start(), service.start())
        await asyncio.gather(*(task.started.wait() for task in runtime.created[2:]))
        await asyncio.gather(
            service.on_config_reload(("bot",)),
            service.on_config_reload(("bot",)),
        )
        assert runtime.manager.registrations == 2
        assert len(runtime.manager.callbacks) == 1
        assert len(runtime.created) == 4
        assert len(service._tasks) == 2
        assert all(task.finished.is_set() for task in runtime.created[:2])
        assert all(not task.finished.is_set() for task in runtime.created[2:])
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_unrelated_scope_does_not_reconcile(runtime: SimpleNamespace) -> None:
    service = TelemetryLifecycleService()
    try:
        await service.start()
        runtime.manager.config.telemetry.enable = True
        await service.on_config_reload(("model",))
        assert runtime.imports == []
        assert runtime.created == []
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_webui_reload_is_dispatched_to_main_loop(runtime: SimpleNamespace) -> None:
    previous_loop = get_main_loop()
    main_loop = asyncio.get_running_loop()
    set_main_loop(main_loop)
    service = TelemetryLifecycleService()
    try:
        await service.start()
        runtime.manager.config.telemetry.enable = True
        await asyncio.to_thread(lambda: asyncio.run(service.on_config_reload(("bot",))))
        await asyncio.gather(*(task.started.wait() for task in runtime.created))
        assert all(task.loop is main_loop for task in runtime.created)

        await asyncio.to_thread(lambda: asyncio.run(service.stop()))
        assert all(task.finished.is_set() for task in runtime.created)
        assert runtime.manager.callbacks == []
    finally:
        await service.stop()
        set_main_loop(previous_loop)


@pytest.mark.asyncio
async def test_start_import_failure_is_exposed_and_callback_rolled_back(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace,
) -> None:
    def fail_import(_name: str) -> Any:
        raise ImportError("synthetic import failure")

    monkeypatch.setattr(lifecycle_module, "import_module", fail_import)
    runtime.manager.config.telemetry.enable = True
    service = TelemetryLifecycleService()
    with pytest.raises(ImportError, match="synthetic import failure"):
        await service.start()
    assert runtime.manager.callbacks == []
    assert service._tasks == {}
    assert not service._started


@pytest.mark.asyncio
async def test_background_exception_is_logged_with_traceback(
    caplog: pytest.LogCaptureFixture, runtime: SimpleNamespace,
) -> None:
    class _FailingTask:
        task_name = "failing heartbeat"

        async def start_task(self, _abort_flag: asyncio.Event) -> None:
            raise RuntimeError("synthetic telemetry failure")

    runtime.remote.TelemetryHeartBeatTask = _FailingTask
    runtime.manager.config.telemetry.enable = True
    service = TelemetryLifecycleService()
    try:
        with caplog.at_level(logging.ERROR, logger="telemetry_lifecycle"):
            await service.start()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert any(
            "遥测后台任务失败" in record.message
            and record.exc_info is not None
            and isinstance(record.exc_info[1], RuntimeError)
            for record in caplog.records
        )
    finally:
        await service.stop()
