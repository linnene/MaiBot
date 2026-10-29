"""Local deployment boundary: concise public queries, no conversation export."""

import re
from typing import Iterable
from urllib.parse import urlparse


def web_search_parameters() -> dict:
    """Canonical schema; SDK ToolParameterInfo may emit invalid nullable schema keys."""
    return {"type":"object","properties":{
        "question":{"type":"string","minLength":1,"maxLength":160,
                    "description":"仅必要的单行公开搜索词，不含账号、联系方式或私聊记录。"},
        "tavily_topic":{"type":"string","description":"可选的 Tavily topic；普通检索留空。"},
    },"required":["question"],"additionalProperties":False}


def query_error(question: str, protected_ids: Iterable[str] = ()) -> str:
    if len(question) > 160 or "\n" in question or "\r" in question:
        return "只提交单行必要的公开搜索词，不要提交聊天记录。"
    if re.search(r"\bsk-[\w.-]{8,}|(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", question):
        return "查询含有可能的凭据或联系方式，未发送到搜索引擎。"
    if any(str(value) and str(value) in question for value in protected_ids):
        return "查询含有私聊对象的稳定账号，未发送到搜索引擎。"
    if any(word in question for word in ("聊天记录", "私聊内容", "access_token", "api_key", "cookie=", "password=")):
        return "不要外发私聊内容或凭据；请改为不含身份的公开问题。"
    return ""


def source_links(results) -> str:
    links=[]; seen=set()
    for result in results:
        try: parsed=urlparse(result.url)
        except ValueError: continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or result.url in seen: continue
        if parsed.username or parsed.password: continue
        seen.add(result.url)
        links.append(f"- {result.title[:120]}: {result.url[:600]}")
    return "\n".join(links[:5])
