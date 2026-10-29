# Companion deployment baseline

Record the intended chat scope, enabled plugins, model task bindings, dependency lock and source revision before removing modules. Keep runtime configuration, access tokens, real chat excerpts, production counters and database copies outside the repository.

Use `scripts/collect_slim_baseline.py` and `scripts/run_slim_baseline.py` with `evals/slim-baseline/suite.json` to collect local evidence and run the fixed structural suite. Review known failures in the same environment; do not skip them to manufacture a green result. Synthetic tests validate contracts, not persona naturalness, retrieval quality or actual platform delivery.

Store the resulting private report in a separate instance directory and pin only sanitized source and test definitions in Git. Back up code, configuration and state separately, verify SQLite copies, and retain an explicit rollback procedure.
