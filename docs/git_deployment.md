# Git-based deployment

The main application is released from this repository's dev branch. A deployment must identify the full pushed commit, not only a branch name. Custom plugin sources are separate clean-history branches under deploy/plugins/; deployment/plugin-lock.json pins exact commits. Unmodified upstream plugins are pinned to their original repository commits. These are deployment snapshots, not claims of official upstream releases. Original licenses and attribution remain with each plugin.

## Release workflow

- Review local and live source differences. Keep real configuration, credentials, accounts, chat excerpts, model request snapshots, cookies, database files and runtime indexes outside Git.
- Run the relevant tests and preserve failures. Publish sanitized source and plugin snapshots before deployment. Do not merge private evaluation history merely to preserve commit ancestry.
- Update the plugin lock in dev, commit and push it, then verify the remote commit IDs. Fetch those exact Git commits on the server and stage them without touching the running instance.
- Back up code, configuration and consistent runtime state independently. If the server has historical uncommitted source, preserve it in a private backup and archived local branch/stash before switching to a clean release. Do not force-reset away unknown changes.
- Stop the application during cutover. Preserve live plugin configuration, cookies, deduplication files and configured reference assets. Keep instance data in place; do not restore an old database merely to roll back code.
- Activate only the pushed main and locked plugin revisions, restart, verify services and compare enabled plugins/settings. Record the released commits under the private instance data directory.

## Verify deployed source

```sh
python scripts/verify_deployment.py --instance-dir /path/to/instance --expected-main FULL_PUSHED_MAIN_SHA
```

After a deployment record has been written to instance data/deployment/current.json with a main_commit field, --expected-main can be omitted. Verification is read-only. It rejects a wrong HEAD or uncommitted source in the application or any locked plugin. Runtime files must remain ignored/private. It does not print credential values, prove chat quality or imply that every configured external API was called successfully.

## Runtime assets and rollback

The plugin lock lists classes of private runtime assets without their values. Keep these outside public source. In particular, curated voice requires a separately prepared voice-index.json in its SDK data directory; QQ Space requires its login cookies and processing state. The SVG plugin's cooldown and the life plugin's schedule database also belong to persistent instance data.

On rollback, select the previous recorded Git revisions and preserve current data. Restore source/configuration only where required by that release. Keep previous plugin directories and deployment records until the new release has been used successfully. A market-installed metadata file is not authoritative when a custom snapshot is deployed; use the Git lock and recorded HEAD.
