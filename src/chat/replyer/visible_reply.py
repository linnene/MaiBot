"""Remove leaked host self-history envelopes, not conversational timestamps."""

from datetime import datetime

import re


_OPEN = re.compile(r'<self_message time="(?P<time>[^"\n]+)">')
_CLOSE_LINE = re.compile(r"(?m)^[ \t]*</self_message>[ \t]*(?:\r?\n|$)")


def unwrap_self_history(text: str) -> str:
    """Unwrap only output beginning with our exact timestamped history envelope.

    Also handles repeated envelopes and an unfinished final block observed in
    model output. Prose, code fences, ordinary times and other XML are untouched.
    It does not rewrite body content, silently truncate it or generate a fallback.
    """
    original = text
    text = text.strip()
    first = _OPEN.match(text)
    if first is None:
        return original
    try:
        datetime.fromisoformat(first["time"])
    except ValueError:
        return original

    def remove_open(match: re.Match[str]) -> str:
        try:
            datetime.fromisoformat(match["time"])
        except ValueError:
            return match.group()
        return ""

    # Only structural line starts can open additional history blocks.
    text = re.sub(r'(?m)^<self_message time="(?P<time>[^"\n]+)">[ \t]*(?:\r?\n)?', remove_open, text)
    text = _CLOSE_LINE.sub("", text)
    text = re.sub(r"</self_message>\s*$", "", text)
    return text.strip()
