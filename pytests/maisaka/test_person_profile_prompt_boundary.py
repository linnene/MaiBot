from src.maisaka.memory.person_profile import _format_profile_reference_block


def test_profile_does_not_authorize_current_schedule_inference() -> None:
    text = _format_profile_reference_block(["EMANON：\n  - EMANON需要上学"])

    assert "不得仅凭‘是学生’‘需要上学’等稳定背景" in text
    assert "推断此人当前要上课、签到、写论文或赶时间" in text
    assert "不得把机器人的生活状态、日程或待办转移到此人名下" in text
