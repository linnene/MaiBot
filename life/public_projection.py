"""Public life categories; never export summaries, locations, books or relationships."""
from typing import Any
import math

ACTIVITIES = {'leisure': '自由活动', 'rest': '休息', 'meal': '吃饭', 'study': '学习',
              'work': '工作', 'sleep': '睡觉', 'nap': '睡觉', 'travel': '出行',
              'exercise': '运动', 'shower': '洗漱'}


def public_life_state(state: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    kind = current.get('kind') if isinstance(current, dict) else None
    kind = kind if kind in ACTIVITIES else 'unknown'
    awake = state.get('sleep_phase') == 'awake'
    result = {'source_kind': 'public_life_projection', 'activity_kind': kind,
              'current_activity': ACTIVITIES.get(kind, '未知'),
              'can_browse': awake and kind in {'leisure', 'rest'}, 'sleeping': not awake}
    for name, low, high in [('energy', 0, 100), ('mood_valence', -1, 1)]:
        value = state.get(name)
        if type(value) in (int, float) and math.isfinite(value) and low <= value <= high:
            result[name] = value
    return result
