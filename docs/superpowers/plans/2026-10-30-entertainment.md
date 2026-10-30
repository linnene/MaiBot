# Entertainment and natural sharing implementation

Goal: idle interest-led reading, source-aware reading memories, natural per-channel sharing, and relevant optional QQ Space images. User approved implementation on feat/entertainment (dev/entertainment conflicts with existing dev ref). Base3beaba9c3; keep dev/production unchanged during development.

Architecture: independent local.entertainment plugin owns read-only localhost Xiaohongshu MCP transport, persistent reading/material/share records and bounded idle browsing. Maizone owns QQ Space draft/content/image selection and receipt; existing host owns chat sends. Source material is reading evidence, never user facts or personal experiences. A material may be ignored; every browsing session need not share. Persona stays short and gentle. Configurable interest defaults add cooking/travel/food. No private account IDs/config/chat contents in Git.

Boundaries: no XiaoHongShu write tools; optional local sidecar supplied separately, login by user. New features default off/preview; no automatic real messages in tests or installing browser through test imports. Existing Space and chat paths remain usable when entertainment unavailable. No paid image generation added. Main SDK/market/WebUI remain intact.

Interfaces (API calls use fully qualified local.entertainment name):
- get_materials(channel:str='qzone',limit:int=3)->dict with success, materials list. Each has id, source_kind='external_reading', title, url (public token-free), author, observed_at, impression, images list of public HTTPS URLs. No cookies/xsec_token. Only approved/remembered reading material eligible. Unknown channel rejected. API is data-only; no send.
- record_share(material_id:str,channel:str,target_id:str='',receipt_id:str='')->dict; call only after confirmed successful receipt. Persistent dedup across channels; failed generation/send never counts as shared.
- optional browse/status/recall/prepare tools for Planner and authenticated plugin UI; native SDK config controls auto reading, idle activities, budget, channel allowlists and dry-run. All SQLite operations off event loop.

Work items (parallel independent implementation, followed by shared review):
- Main: fix filtering-before-memory-handoff with separate host-owned evidence input, cover real generate entry in normal/retro + rebuild without exposing raw tools as conversation. Only src/chat/replyer and related tests.
- Entertainment plugin: transport whitelist, bounded validated records, SQLite source-aware recall and share receipts, background lifecycle/idle gate, tool/API entry points, tests with mocked HTTP/LLM/SDK. Configuration includes interests photography/books/drawing/cooking/travel/food; concrete old food preferences not hardcoded to identity.
- Maizone: opt-in natural-content pipeline and entertainment material integration; optional image search API from existing search plugin where possible (never call a chat-sending action to retrieve images). Real image checking and source linkage. Plain text valid, no forced emoji; drafts may skip. Preserve legacy defaults until new mode enabled. Avoid raw private life fields in new path. Tests mock SDK/HTTP/Qzone; no real sends.
- Primary: expose small data-only image search API if missing, verify SDK contracts, document sidecar setup and config/UI workflows, pin independent plugin commits, review privacy and compatibility, targeted meaningful tests. Package exact code and explain unverified login/live paths. No merge to dev or production deploy in this task unless user explicitly requests it.

Acceptance: mocked service failures cannot block chat; no readings become real-life/user events; no duplicate records/shares after restart; actual receipts govern dedup; unknown material returns explicit failure; no browsing without explicit opt-in and allowed idle state; no silent arbitrary image attachments; old no-entertainment behavior passes regressions. Tests prove mechanics, not naturalness or account stability.

## Completion evidence

Implemented host-owned memory evidence handoff, a read-only platform receipt projection, independent interest-reading plugin, minimal public life state, data-only image API and opt-in two-stage natural QQ Space drafts. Reading reservations and confirmed receipts prevent ambiguous retries; source text is isolated from independent drafts. Plugin revisions are pinned in deployment/plugin-lock.json.

Main full suite under the same sanitized configuration: base 2956 passed / 137 failed / 3 skipped; feature 2966 passed / 137 failed / 3 skipped. Failed node sets match exactly, with no new failures. Scoped replyer and receipt tests: 447 passed; runtime/platform suite: 260 passed (overlap exists). Reader: 19 passed; Maizone: 30 passed; search: 14 passed; Mai_life: 567 run, 2 skipped, no failures. Tests used mocks/local interfaces, no real model, QQ or Qzone sends.

No production deployment, account login, sidecar installation or live naturalness claim. New modes remain disabled/preview until configured. Reading recall is literal local search, not integrated embedding retrieval. Source snapshots are independently committed.
