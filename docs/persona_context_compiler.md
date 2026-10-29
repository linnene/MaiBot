# Experimental persona context compiler

This optional request-assembly path is disabled by default. It separates stable identity, voice examples, actual history and the current generation request. This is a structural experiment, not proof of naturalness or long-term identity consistency.

Enable experimental.persona_context_compiler only for a bounded evaluation. When disabled, the existing Replyer path is retained. In compiler mode, Planner supplies an enumerated reply_act rather than a free-form draft; identity, source attribution, target timestamps and attachments still come from the host.

Voice examples use personality.dialogue_examples with situation, user and assistant fields, up to eight entries. They are labelled examples, not historical assistant messages or memories. Keep them generic and free of private facts. personality.casual_reply_max_chars is a target budget, not mechanical truncation; detailed replies are exempt. experimental.persona_history_burst_limit optionally limits consecutive assistant-history projection without changing stored messages.

API user messages can carry labelled control/data envelopes, so they must not be interpreted as actual user speech. Labels do not establish model-level hard isolation, and plugin hooks can still modify final requests.

Synthetic evaluation builders scripts/build_persona_compiler_eval.py and scripts/build_persona_policy_eval.py use fictional fixtures. The general scripts/evaluate_replyer_harness.py utility replays supplied snapshots: outside --dry-run, retained text is sent to the configured model provider. Obtain permission for that exact private corpus before running it. No evaluator sends generated messages to QQ.

Run compiler, persona-policy and visible-reply tests before changing this path. Structural tests do not measure human likeness. Keep generation budgets comparable, preserve failures and incomplete outputs, and evaluate history relevance, evidence ownership and voice separately.

Back up private configuration and state outside Git before deployment. Record the released commit and restore only affected source/configuration fields when reverting. Do not publish production chats, account IDs, credentials or private request snapshots and baseline reports.
