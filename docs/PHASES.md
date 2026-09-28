# QUILL Social implementation map

This is a map of PRD requirements to existing modules, not a declaration that every phase is complete. This contribution focuses on usable, accessible social reading and composing: phases 1 and 2, with improvements to parts of phases 3 and 4. Standard mode keeps everyday reading and posting accessible; Advanced mode retains the existing workspace and publishing tools.

Live Mastodon and Bluesky reading, authentication, notifications, conversations, and publishing are connected. Automated tests use fake clients and local data. The user has tested the interface with NVDA; wider screen-reader and platform validation remains future work.

Phases 5–8 have existing foundations and prototypes, but ecosystem integrations, hosted collaboration, production AI and media backends, and cross-platform release validation are not completed by this PR. Sounds remain deferred. The tables below locate code for review; they do not certify an entire priority as finished.

## Priorities

### P0 — Foundational release (PRD 36)

| Area | Module(s) |
| --- | --- |
| Accessible wx shell | `ui/app.py`, `ui/announce.py`, `ui/commands.py`, `ui/composer.py` |
| Mastodon + Bluesky (capability-driven) | `adapters/{base,mastodon,bluesky,mock,registry}.py`, `capabilities.py` |
| Multiple accounts, capability registry | `model.py` (Account/Workspace), `capabilities.py` |
| Timelines, threads, profiles, details | `db.py`, `fields.py`, `ui/app.py` |
| Compose/reply/quote/repost/like/bookmark | `services/composer.py`, `ui/composer.py`, `ui/app.py` |
| Media + alt text, content warnings, visibility, polls | `model.py`, `services/composer.py` |
| Intelligent thread splitting | `services/thread_splitter.py` |
| Search, folders, saves, drafts | `db.py` (FTS5), `services/smartfolder.py` |
| Local scheduling; native scheduling interfaces | `services/scheduler.py`, `services/thread_publisher.py` |
| Command center, Where Am I, help, remappable keys | `ui/commands.py`, `whereami.py`, `keymap.py` |
| Secure credentials | `security/credentials.py` |

### P1 — Power release (PRD 36)

| Area | Module(s) |
| --- | --- |
| Full campaigns and queues | `services/queue_schedule.py`, `services/calendar.py`, `model.py` (Campaign) |
| Approvals | `services/approvals.py` |
| Cross-network variants | `services/composer.py` (per-network variants), `services/thread_splitter.py` |
| Smart folders | `services/smartfolder.py` |
| AI | `services/ai/{gateway,prompt_guard,writing,understand,accessibility}.py` |
| Transcription orchestration | `services/transcripts.py` |
| Full moderation center | `services/moderation.py` |
| GitHub | `adapters/github.py`, `services/github_bridge.py` |
| QUILL ecosystem integration | `services/ecosystem.py`, `services/longform.py` |
| Analytics | `services/analytics.py` |

### P2 — Community and scale (PRD 36)

| Area | Module(s) |
| --- | --- |
| Team workspaces + approvals/roles | `services/approvals.py` (role matrix) |
| QUILL Longform hosting | `services/longform.py` |
| Advanced automation / recurring | `services/recurring.py` |
| Plugin system | `services/plugins.py`, `plugins/` |
| Additional networks via adapters | `adapters/base.py` contract, `adapters/registry.py` |

## Implementation phases (PRD 37)

- **Phase 0 Foundations** — adapter contracts (`adapters/base.py`), threat-model
  seams (`security/`, `services/ai/prompt_guard.py`), accessible-control proof
  (`ui/`), media proof (`services/media.py`).
- **Phase 1 Reader** — accounts, timelines, notifications, caching, reading
  positions, details, threads (`db.py`, `ui/app.py`, `services/catchup.py`).
- **Phase 2 Composer** — media, alt text, polls, CWs, visibility, thread gates,
  splitting, capability validation (`services/composer.py`, `ui/composer.py`).
- **Phase 3 Organization** — folders, smart folders, saves, notes, templates,
  search, catch-up (`services/smartfolder.py`, `services/templates.py`, `db.py`).
- **Phase 4 Publishing Studio** — drafts, queues, agenda/calendar, scheduling,
  campaigns, approvals, recurring, bulk import, retries, analytics foundation
  (`services/{queue_schedule,calendar,optimal_time,approvals,recurring,bulk_import,scheduler,analytics}.py`).
- **Phase 5 Media + Ecosystem** — player, transcripts, chapters, QUILL / Radio /
  Cast / Audio Studio / Beacon / Sync bridge, longform
  (`services/{media,transcripts,ecosystem,longform}.py`).
- **Phase 6 AI** — provider gateway, writing tools, summaries, descriptions,
  transcription orchestration, accessibility checks, prompt-injection defenses
  (`services/ai/`).
- **Phase 7 GitHub + Teams** — GitHub views, issue/discussion workflows, release
  campaigns, shared-workspace roles (`adapters/github.py`,
  `services/github_bridge.py`, `services/approvals.py`).
- **Phase 8 Cross-Platform** — platform data dirs (`paths.py`, macOS/Windows),
  plugin system (`services/plugins.py`), offline resilience
  (`services/outbox.py`), diagnostics (`security/diagnostics.py`).

## Remaining boundaries

- Mastodon, Bluesky, and GitHub adapters can call live clients when configured. Credentials are stored through the OS keyring; the database stores references.
- Local scheduling works while the app is open. Cloud scheduling and full native scheduling orchestration remain future work.
- AI defaults to a deterministic mock provider; production provider configuration needs separate work.
- Media playback defaults to a null backend. A complete player and ecosystem integrations remain future work.
- Bluesky publishing currently supports images, replies, and quotes. Unsupported polls or other attachment kinds fail clearly and retain the draft.
- This PR does not certify every P0 acceptance criterion or provide a release package. Use the PRD and manual testing guide for remaining validation.
