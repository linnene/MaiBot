from src.maisaka.builtin_tool.query_memory import get_tool_spec


def test_query_memory_tool_describes_time_contract_and_mode_routing() -> None:
    spec = get_tool_spec()
    properties = spec.parameters_schema["properties"]

    mode_description = properties["mode"]["description"]
    assert "time和hybrid至少提供time_start或time_end" in mode_description
    assert "没有时间条件时使用search" in mode_description
    assert "YYYY/MM/DD或YYYY/MM/DD HH:mm" in properties["time_start"]["description"]
    assert "YYYY/MM/DD或YYYY/MM/DD HH:mm" in properties["time_end"]["description"]



def test_replyer_memory_reference_preserves_available_event_time():
    from src.maisaka.builtin_tool.query_memory import _build_replyer_memory_reference
    text = _build_replyer_memory_reference({"hits": [{
        "content": "User booked a synthetic trip.", "source": "fusion",
        "metadata": {"source": "chat_summary:private-test", "time_meta": {"event_time_start": 100, "event_time_end": 200}},
    }]})
    assert "private-test" in text and "event_time_start=100" in text and "event_time_end=200" in text
