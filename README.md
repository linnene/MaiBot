# MaiBot Companion Fork

This repository is an experimental fork of [MaiBot](https://github.com/Mai-with-u/MaiBot). It keeps MaiBot's core architecture while adapting it for a persistent, private QQ companion rather than a general-purpose assistant or an always-on question-answering bot.

The project is under active development. The current work focuses on identity, relationship continuity, memory boundaries, natural interaction timing, and believable everyday behavior. It is not an official MaiBot distribution.

## Direction

The goal is to build a companion that can:

- maintain a stable identity without repeating persona traits in every reply;
- recognize trusted relationships from verified platform identity rather than nicknames or prompt claims;
- keep the companion's life, schedule, and memories separate from the user's facts;
- remember long-term information while allowing corrections and preventing stale facts from dominating replies;
- decide when to speak, wait, refuse, or remain silent;
- use short, casual language and expressive behavior without turning every conversation into assistant-style Q&A;
- initiate conversations from a coherent personal timeline while respecting privacy and quiet periods.

## Current Work

The active `dev` branch currently includes fork-specific work in these areas:

- trusted relationship binding based on platform and stable user identity;
- separation of thinking delay, typing delay, and scheduled reply delay;
- safeguards that keep transient chat details out of stable person profiles;
- ownership boundaries that prevent the companion's schedule from being attributed to the user;
- privacy-safe life context for plugins and public posting integrations;
- contextual emoji selection, cooldown, and repetition controls;
- compatibility patches for the companion's life and economy plugins.

The next research track is a revised persona harness. This work has not been implemented yet. It will investigate:

- separating stable identity, relationship state, long-term memory, medium-term psychological state, and short-term activity;
- selecting what context should be grounded in a reply, when it should be used, and when it should be ignored;
- reducing conflicts between the Planner, Replyer, memory system, and life simulation;
- replacing detailed reply scripts with compact behavioral intent;
- evaluating naturalness, persona consistency, memory ownership, and long-horizon drift with reproducible comparisons.

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Upstream-aligned baseline. Fork-specific development should not be committed here. |
| `dev` | Active integration branch for this fork. |
| `feature/*` | Isolated work for changes that need review before integration into `dev`. |

## Upstream Documentation

This fork still relies on MaiBot's installation process, configuration system, WebUI, and plugin SDK. Refer to the upstream resources for the base system:

- [MaiBot repository](https://github.com/Mai-with-u/MaiBot)
- [MaiBot documentation](https://docs.mai-mai.org)
- [Deployment guide](https://docs.mai-mai.org/manual/)
- [Plugin SDK guide](https://github.com/Mai-with-u/maibot-plugin-sdk/blob/main/docs/guide.md)

Fork-specific behavior may differ from upstream documentation. Configuration and migration notes will be added here as the modified architecture stabilizes.

## Development Status

This repository currently targets a private deployment and is not packaged as a supported public release. Expect configuration changes, database migrations, and plugin compatibility adjustments on `dev`.

Do not commit access tokens, QQ identifiers, private conversation records, or deployment secrets. Keep private identity bindings and production configuration outside the public repository.

## Attribution and License

MaiBot and its original architecture are developed by the [MaiBot contributors](https://github.com/Mai-with-u/MaiBot/graphs/contributors). This fork preserves the upstream attribution and remains subject to the repository's [GPL-3.0 license](LICENSE), [EULA](EULA.md), and [privacy policy](PRIVACY.md).
