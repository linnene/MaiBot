# Local companion drawing changes

## Features
- Let the character optionally share a small drawing when it fits the conversation; no separate image-model API.
- Persistent per-conversation cooldown (default 300 seconds) and exact normalized SVG repeat suppression (default 24 hours).

## Reliability
- Render in a separate process with timeout, Linux memory/CPU limits, and unload cleanup.
- Bound SVG bytes, nodes, output dimensions and PNG size; reject external resources and active content.
- Count only confirmed platform delivery. Return concise tool results without SVG/base64 context duplication.
- Tests cover delivery failure, cooldown, duplicate persistence, per-session isolation and rendering limits.
