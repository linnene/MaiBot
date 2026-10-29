# Dialogue attribution repair

Goal: preserve who said what across Planner/Replyer handoff without changing persona or adding model calls.

Approved direction: host-resolved dialogue evidence, and correction before continuation of an unsupported interpretation. Stronger identity rules alone do not address the observed event-role reversal. A second model judge would add latency and cost; do not add one.

Architecture: extend the existing handoff with a bounded recent dialogue window ending at the actual target message. Speaker role comes only from structured source_kind, never a nickname or model summary. Include source IDs, display names, timestamps and original excerpts. Keep ordinary user and guided_reply messages only; exclude planning output, tools and plugin-generated proactive prompts. Preserve existing explicit and automatic memory evidence. Unknown target/proactive turns get no arbitrary historical window. Evidence records are data, not instructions. Planner and Replyer must check prior utterances when the user challenges attribution and must not rationalize an earlier error as a deliberate joke without evidence.

Constraints: no private account details or real chats in Git; no external replay of private conversations; retain short gentle voice and current confidentiality; maintain Chinese/English/Japanese parity; no schema/DB/dependency/plugin changes. Base: 67b144d90; existing deploy tag is rollback anchor.

Execution (inline, user already confirmed the repair):

- Add synthetic tests in pytests/maisaka/test_reply_handoff.py for role preservation with misleading names, target anchoring, bounded content, omission of internal/unknown sources, and prior own reply vs actual user correction.
- Run tests against old code and retain expected failure evidence.
- Implement host-resolved recent_dialogue in src/chat/replyer/handoff.py and add speaker/role to explicit message evidence.
- Update handoff/review templates in all three locales and Planner harness contract in src/maisaka/chat_loop_service.py. Keep inference in the model; do not add a keyword classifier or pretend to deterministically parse event actors.
- Run targeted handoff/review/generator/Planner regression and broader appropriate related tests, inspect diff for privacy and unintended behavior.
- Commit, fast-forward dev and push first; deploy exact pushed commit only if source remains clean and plugin lock unchanged, preserve configs/state and restart, verify service and pinned plugins.

Acceptance: automated tests prove correct evidence construction and exclusion; they do not prove every future model response respects evidence. No promise that ordinary banter can be deterministically distinguished from semantic mistakes. Live conversation quality still needs observation.

Verification ledger: baseline handoff 20 passed; initial new tests 7 failed for missing evidence; actual generator target-wiring tests proved 2 failures when host target was omitted, then passed for both standard and retro paths. No-ID self speech and proactive exclusion added. Targeted 121 passed. Broader tests 508 passed, 1 preexisting failure test_builtin_prompt_templates_have_intro_metadata, confirmed on unchanged base67 (a_memorix_chat_summary metadata). Independent review found target stripping and no-ID omission; both repaired and re-reviewed.
