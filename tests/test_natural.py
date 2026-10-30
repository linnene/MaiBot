"""Replay source-aware drafting with actual module and synthetic capabilities only."""
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import base64
import json

import pytest
from PIL import Image

spec = spec_from_file_location("natural", Path(__file__).parents[1] / "natural.py")
natural = module_from_spec(spec)
spec.loader.exec_module(natural)


def source():
    return {"id": "m1", "source_kind": "external_reading", "title": "旅行照片", "url": "https://example.com/post", "author": "作者", "observed_at": "2026-10-30", "impression": "色彩很柔和", "images": []}


def setup(tmp_path, draft=None, materials=None):
    calls = []
    async def call(name, **kwargs):
        calls.append((name, kwargs))
        if name.endswith("get_materials"):
            return {"success": True, "materials": materials if materials is not None else [source()]}
        if name.endswith("get_life_state"):
            return {"energy": 70, "current_location": "SECRET", "current_activity": "SECRET", "diary": "SECRET"}
        if name.endswith("reserve_material"):
            return {"success": True, "lease_id": "lease1"}
        return {"success": True}
    output = draft or {"skip": False, "text": "看到这组旅行照片，柔和的颜色让人想出门走走。", "material_id": "m1", "image_query": ""}
    plugin = SimpleNamespace(personality="轻柔", reply_style="简短", config=SimpleNamespace(send=SimpleNamespace(natural_mode=True, enable_life_context=True, enable_image=True, natural_vision_model="vlm"), plugin=SimpleNamespace(text_model="replyer")), ctx=SimpleNamespace(api=SimpleNamespace(call=call), llm=SimpleNamespace(generate=AsyncMock(return_value={"response": json.dumps(output, ensure_ascii=False)})), logger=SimpleNamespace(warning=lambda *_: None)))
    return natural.NaturalFeedService(plugin, tmp_path / "receipts.json"), calls


def test_preview_source_attribution_no_side_effect(tmp_path):
    service, calls = setup(tmp_path)
    result = asyncio.run(service.draft())
    assert "https://example.com/post" in result["message"]
    assert "作者" in result["message"]
    prompt = service.plugin.ctx.llm.generate.call_args.args[0]
    assert "SECRET" not in prompt and "日报" in prompt
    assert not service.journal_path.exists()
    assert not any(name.endswith(("record_share", "reserve_material")) for name, _ in calls)


def test_unknown_material_schema_fails_clearly(tmp_path):
    material = source() | {"private_chat": "SECRET"}
    service, _ = setup(tmp_path, materials=[material])
    with pytest.raises(ValueError, match="schema"):
        asyncio.run(service.draft())
    assert not service.plugin.ctx.llm.generate.called


def test_skip_and_failure_do_not_record(tmp_path):
    service, calls = setup(tmp_path, draft={"skip": True, "text": "", "material_id": "", "image_query": ""})
    publish = AsyncMock()
    assert asyncio.run(service.publish("", publish))["skipped"]
    publish.assert_not_called()
    service, calls = setup(tmp_path)
    publish = AsyncMock(return_value=None)
    assert not asyncio.run(service.publish("", publish))["success"]
    assert not any(name.endswith("record_share") for name, _ in calls)
    assert service._journal() == {}


def test_receipt_only_record_and_restart_dedup(tmp_path):
    service, calls = setup(tmp_path)
    publish = AsyncMock(return_value="real-fid")
    assert asyncio.run(service.publish("", publish))["success"]
    record = next(kwargs for name, kwargs in calls if name.endswith("record_share"))
    assert record["receipt_id"] == "real-fid" and record["channel"] == "qzone"
    restarted, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="unknown draft material_id"):
        asyncio.run(restarted.publish("", publish))
    assert publish.await_count == 1


def test_ambiguous_exception_retains_pending(tmp_path):
    service, calls = setup(tmp_path)
    with pytest.raises(TimeoutError):
        asyncio.run(service.publish("", AsyncMock(side_effect=TimeoutError())))
    assert service._journal()["m1"]["state"] == "pending"
    assert not any(name.endswith("record_share") for name, _ in calls)


def test_concurrent_publication_is_serialized(tmp_path):
    async def replay():
        service, _ = setup(tmp_path)
        publish = AsyncMock(return_value="fid")
        results = await asyncio.gather(service.publish("", publish), service.publish("", publish), return_exceptions=True)
        assert publish.await_count == 1
        assert sum(isinstance(item, ValueError) for item in results) == 1
    asyncio.run(replay())


def test_projection_no_raw_narrative():
    assert natural.public_life_projection({"current_activity": "private narrative", "current_location": "secret", "energy": float("nan")}) == {}
    assert natural.public_life_projection({"current_activity": "阅读", "energy": 1}) == {"energy": "low", "activity": "reading"}


@pytest.mark.parametrize("url", ["https://127.0.0.1/a", "https://[::1]/a", "http://example.com/a", "https://localhost/a", "https://example.com/a?xsec_token=secret", "https://user:pass@example.com/a"])
def test_nonpublic_urls_rejected(url):
    with pytest.raises(ValueError):
        natural.public_url(url)


def png():
    output = BytesIO()
    Image.new("RGB", (120, 120)).save(output, "PNG")
    return output.getvalue()


def test_image_format_and_size():
    assert natural.checked_image(png())
    for bad in (b"not an image", b"x" * (natural.MAX_IMAGE_BYTES + 1)):
        with pytest.raises((ValueError, OSError)):
            natural.checked_image(bad)


def test_dns_private_download_rejected(monkeypatch):
    monkeypatch.setattr(natural.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(ValueError, match="private"):
        natural.download_image("https://example.com/image")


def test_vision_real_pixels_and_no_match_omit(tmp_path):
    service, _ = setup(tmp_path)
    service._call = AsyncMock(return_value={"success": True, "url": "https://example.com/image", "image_base64": base64.b64encode(png()).decode()})
    service.plugin.ctx.llm.generate.return_value = {"response": '{"matches":false}'}
    assert asyncio.run(service._image("旅行感想", None, "旅行")) == ([], "")
    prompt = service.plugin.ctx.llm.generate.call_args.args[0]
    assert prompt[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    service.plugin.ctx.llm.generate.return_value = {"response": '{"matches":true}'}
    assert asyncio.run(service._image("旅行感想", None, "旅行"))[0] == [png()]


def test_lease_conflict_does_not_send(tmp_path):
    service, _ = setup(tmp_path)
    original = service._call
    async def call(name, **kwargs):
        if name.endswith("reserve_material"):
            return {"success": False, "error": "reserved"}
        return await original(name, **kwargs)
    service._call = call
    publish = AsyncMock()
    assert not asyncio.run(service.publish("", publish))["success"]
    publish.assert_not_called()


def test_record_failure_retains_real_receipt(tmp_path):
    service, _ = setup(tmp_path)
    original = service._call
    async def call(name, **kwargs):
        if name.endswith("record_share"):
            raise TimeoutError()
        return await original(name, **kwargs)
    service._call = call
    assert asyncio.run(service.publish("", AsyncMock(return_value="fid")))["success"]
    assert service._journal()["m1"]["receipt_id"] == "fid"


def test_actual_send_feed_entry_preserves_legacy_branch(tmp_path, monkeypatch):
    from types import ModuleType
    import importlib
    import sys
    package = ModuleType("maizone_replay")
    package.__path__ = [str(Path(__file__).parents[1])]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    qzone = ModuleType("maizone_replay.qzone_api")
    qzone.create_qzone_api = lambda: None
    cookie = ModuleType("maizone_replay.cookie")
    cookie.renew_cookies = AsyncMock()
    image = ModuleType("maizone_replay.image")
    image.generate_images = AsyncMock(return_value=[])
    chat = ModuleType("maizone_replay.chat_fetcher")
    chat.MessageFetcher = object
    chat.set_chat_fetcher_logger = lambda *_: None
    for module in (qzone, cookie, image, chat):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    utils = importlib.import_module("maizone_replay.utils")
    service, _ = setup(tmp_path)
    plugin = service.plugin
    plugin.ctx.logger.error = lambda *_: None
    plugin.ctx.logger.info = lambda *_: None
    plugin.ctx.logger.debug = lambda *_: None
    plugin.config.plugin.http_host = "unused"
    plugin.config.plugin.http_port = 1
    plugin.config.plugin.napcat_token = "unused"
    plugin.natural_service = service
    utils.set_utils_plugin_context(plugin)
    # New entry must never use private chat/schedule or paid image generation.
    utils._fetch_chat_messages = AsyncMock(side_effect=AssertionError("private chat"))
    utils._fetch_public_life_context = AsyncMock(side_effect=AssertionError("raw life"))
    api = SimpleNamespace(publish_emotion=AsyncMock(return_value="fid"))
    monkeypatch.setattr(utils, "create_qzone_api", lambda: api)
    assert asyncio.run(utils.send_feed("旅行"))[0]
    image.generate_images.assert_not_called()
    # Legacy default still reaches the pre-existing prompt/history/image path.
    plugin.config.send.natural_mode = False
    plugin.config.send.prompt = "{topic}"
    plugin.config.send.history_number = 1
    plugin.config.send.image_mode = "only_emoji"
    plugin.config.send.image_number = 1
    plugin.config.send.ai_probability = 0
    utils._fetch_public_life_context = AsyncMock(return_value="")
    utils._fetch_chat_messages = AsyncMock(return_value=([], ""))
    api.get_send_history = AsyncMock(return_value="")
    plugin.ctx.llm.generate.return_value = {"response": "旧模式正文"}
    assert asyncio.run(utils.send_feed("主题")) == (True, "已发送说说：【旧模式正文】")
    image.generate_images.assert_awaited_once()


@pytest.mark.parametrize("status,length,body", [(302, None, b""), (200, str(natural.MAX_IMAGE_BYTES + 1), b""), (200, None, b"x" * (natural.MAX_IMAGE_BYTES + 1))])
def test_download_rejects_redirect_and_oversize(monkeypatch, status, length, body):
    raw = SimpleNamespace(close=lambda: None)
    response = SimpleNamespace(status=status, getheader=lambda *_: length, read=lambda n: body[:n])
    connection = SimpleNamespace(sock=None, request=lambda *a, **k: None, getresponse=lambda: response, close=lambda: None)
    monkeypatch.setattr(natural.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(natural.socket, "create_connection", lambda *a, **k: raw)
    monkeypatch.setattr(natural.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=lambda *a, **k: raw))
    monkeypatch.setattr(natural.http.client, "HTTPSConnection", lambda *a, **k: connection)
    with pytest.raises(ValueError):
        natural.download_image("https://example.com/image")


def test_disabled_does_not_read_or_generate(tmp_path):
    service, calls = setup(tmp_path)
    service.plugin.config.send.natural_mode = False
    assert asyncio.run(service.draft())["success"] is False
    assert calls == []
    service.plugin.ctx.llm.generate.assert_not_called()
