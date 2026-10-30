"""Remove leaked host self-history envelopes, not conversational timestamps."""

from datetime import datetime

import re


# Models sometimes typographically normalize the quoted history attribute.
# Keep recognition narrow: one timestamp attribute on our own envelope only.
_OPEN_PATTERN = r"""<self_message[ \t]+time[ \t]*=[ \t]*["'“‘](?P<time>[^"'“”‘’<>\r\n]+)["'”’][ \t]*>"""
_OPEN = re.compile(_OPEN_PATTERN)
_OPEN_LINE = re.compile(r"(?m)^[ \t]*" + _OPEN_PATTERN + r"[ \t]*(?:\r?\n)?")
_CLOSE_LINE = re.compile(r"(?m)^[ \t]*</self_message>[ \t]*(?:\r?\n|$)")
_TRAILING_CLOSE = re.compile(r"(?:[ \t\r\n]*</self_message>)+[ \t\r\n]*\Z")


def unwrap_self_history(text: str) -> str:
    """Unwrap output beginning with our timestamped history envelope.

    Also handles repeated envelopes and an unfinished final block observed in
    model output. Prose, code fences, ordinary times and other XML are untouched.
    It does not rewrite body content, silently truncate it or generate a fallback.
    """
    original = text
    text = text.strip()
    first = _OPEN.match(text)
    if first is None:
        # A model can continue an old block with only its closing delimiter.
        # Preserve examples with an opening tag and fenced code verbatim.
        if "<self_message" not in text and "```" not in text and "~~~" not in text:
            trailing = _TRAILING_CLOSE.search(text)
            if trailing is not None:
                return text[:trailing.start()].rstrip()
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
    text = _OPEN_LINE.sub(remove_open, text)
    text = _CLOSE_LINE.sub("", text)
    text = re.sub(r"</self_message>\s*$", "", text)
    return text.strip()
