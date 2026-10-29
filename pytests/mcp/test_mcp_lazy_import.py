"""在独立解释器验证 MCP 按需加载及首次启用、关闭。"""

from pathlib import Path
from textwrap import dedent

import os
import subprocess
import sys

import pytest


def _run_isolated(script: str, tmp_path: Path, *arguments: str) -> None:
    """子进程只使用临时实例，避免读取私人配置或向源码目录写日志。"""
    environment = os.environ.copy()
    environment["MAIBOT_INSTANCE_DIR"] = str(tmp_path / "instance")
    environment["MAIBOT_INITIALIZE_ONLY"] = "1"
    repository = Path(__file__).resolve().parents[2]
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(repository), environment.get("PYTHONPATH", "")))
    )
    result = subprocess.run(
        [sys.executable, "-c", dedent(script), *arguments],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_disabled_mcp_imports_keep_sdk_unloaded(tmp_path: Path) -> None:
    _run_isolated(
        "import asyncio, sys; "
        "from src.mcp_module.service import MCPService; "
        "from src.config.official_configs import MCPConfig; "
        "from src.webui.routers import mcp; "
        "asyncio.run(MCPService().reload(MCPConfig(servers=[]))); "
        "assert 'mcp' not in sys.modules; "
        "assert 'src.mcp_module.manager' not in sys.modules; "
        "assert 'src.mcp_module.connection' not in sys.modules; "
        "assert 'src.mcp_module.host_llm_bridge' not in sys.modules",
        tmp_path,
    )


@pytest.mark.parametrize("sampling_enabled", [False, True])
def test_cold_enable_loads_runtime_and_closes(tmp_path: Path, sampling_enabled: bool) -> None:
    """真实导入 manager/bridge，只替换建连工厂，不运行网络或模型请求。"""
    _run_isolated(
        """
        import asyncio
        import importlib
        import socket
        import sys
        import threading

        def reject_network(*args, **kwargs):
            raise AssertionError("synthetic MCP test must not use the network")

        socket.socket.connect = reject_network
        socket.socket.connect_ex = reject_network

        from src.config.config import Config, ModelConfig, config_manager
        from src.config.default_model_config import create_default_model_config
        from src.config.official_configs import MCPConfig, MCPServerItemConfig
        from src.mcp_module import service as service_module

        # Sampling 桥真实构造 LLM 门面，但只提供内存中的合成配置，不生成响应。
        config_manager.global_config = Config()
        config_manager.model_config = create_default_model_config(ModelConfig)
        sampling_enabled = sys.argv[1] == "true"
        main_thread = threading.get_ident()
        imported = []
        created = []

        class FakeManager:
            server_count = 1
            tool_count = 0

            def __init__(self):
                self.close_count = 0

            def get_status_snapshot(self):
                return {"initialized": True, "server_count": 1, "tool_count": 0, "servers": []}

            def add_status_change_callback(self, callback):
                self.status_callback = callback

            async def close(self):
                assert threading.get_ident() == main_thread
                self.close_count += 1

        async def fake_from_app_config(cls, config, **kwargs):
            assert threading.get_ident() == main_thread
            callbacks = kwargs["host_callbacks"]
            if sampling_enabled:
                assert callbacks is not None
                assert callable(callbacks.sampling_callback)
                bridge = callbacks.sampling_callback.__self__
                assert type(bridge).__module__ == "src.mcp_module.host_llm_bridge"
                assert bridge._sampling_task_name == "planner"
            else:
                assert callbacks is None
            manager = FakeManager()
            created.append(manager)
            return manager

        def tracked_import(name):
            assert threading.get_ident() != main_thread
            module = importlib.import_module(name)
            imported.append(name)
            if name == "src.mcp_module.manager":
                module.MCPManager.from_app_config = classmethod(fake_from_app_config)
            return module

        service_module.import_module = tracked_import

        async def main():
            service = service_module.MCPService()
            await service.reload(MCPConfig(servers=[]))
            assert "src.mcp_module.manager" not in sys.modules
            assert "src.mcp_module.connection" not in sys.modules
            assert "src.mcp_module.host_llm_bridge" not in sys.modules

            config = MCPConfig(servers=[MCPServerItemConfig(name="synthetic", command="never-run")])
            config.client.sampling.enable = sampling_enabled
            try:
                await service.reload(config)
                assert "src.mcp_module.connection" in sys.modules
                assert "src.mcp_module.manager" in imported
                assert ("src.mcp_module.host_llm_bridge" in sys.modules) == sampling_enabled
                assert ("src.mcp_module.host_llm_bridge" in imported) == sampling_enabled
                assert service.get_status_snapshot()["server_count"] == 1
                await service.reload(config.model_copy(deep=True))
                assert len(created) == 1
                await service.reload(MCPConfig(servers=[]))
                assert created[0].close_count == 1
                assert service.get_status_snapshot()["server_count"] == 0
            finally:
                await service.close()
            assert created[0].close_count == 1
            assert service.get_status_snapshot()["initialized"] is False

        asyncio.run(main())
        """,
        tmp_path,
        str(sampling_enabled).lower(),
    )
