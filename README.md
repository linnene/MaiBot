# Curated voice references

An opt-in SDK hook for `maisaka.replyer.before_model_request`. It adds at most one clearly labelled style example as user/data, leaving the final target, trusted identity, system prompts and review contract unchanged. It has no send capability and cannot send QQ messages itself.

It does not turn on the legacy expression vector backfill or modify the expressions database. The curated index is runtime data; do not place personal account IDs, persona details, chat logs or provider secrets in this repository.

Configuration (under the installed plugin directory):

```toml
[plugin]
enabled = false
config_version = "1.0.0"
[voice]
mode = "off" # off, shadow, active
allowed_sessions = [] # exact existing session IDs; empty means no requests
index_path = "voice-index.json" # relative to ctx.paths.data_dir
min_similarity = 0.60
min_margin = 0.08
```

Only passive `brief` replies with a host-rendered target message are eligible. Serious explanations, clarification, proactive tasks, other sessions and unsupported Context Item schemas are untouched. A model-name/dimension check and a cosine probe check prevent applying mismatched index vectors. A combined two-second embedding budget leaves the original reply unchanged on failure.

The last two injected example IDs per session are suppressed. Query vectors are cached in memory for ten minutes, up to 64 entries; neither queries nor conversation text are persisted. Up to 100 selection decisions (hashed session, reason, example ID and score only) are recorded at `ctx.paths.data_dir/voice-decisions.json`. These recent-example guards and caches reset when the plugin reloads.

Index JSON contains `model_name`, `dimension`, `probe_text`, `probe_vector`, and `entries` with `id`, `situation`, `input`, `output`, `vector`. The examples and probe are configuration/data supplied separately; they are not memories. Only curated content should be put here. This prototype does not manage automatic learning or guarantee human-like expression.

Run behavior tests with the installed SDK:

```sh
python -m unittest discover -s test -v
```

Disable this plugin to stop the trial. No main-repository changes, new Python dependency, schema migration, chat-model switch or persona rewrite is required.
