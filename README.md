# QUILL Social

**Accessibility-first social reading, publishing, and community workspace for Mastodon and Bluesky.**

Part of the QUILL family (QuillBeacon, QUILL Radio, QUILL Cast, QUILL Audio Studio, QuilleSync). QUILL Social is designed first for a blind keyboard and screen-reader user: every core action is reachable by keyboard, every important state is available as text, and reading, drafting, organizing, splitting, and scheduling all work locally before a single credential is entered.

Tagline: *Every conversation within reach.*

## What this build is

This contribution improves the working Mastodon and Bluesky reader and composer, with Standard and Advanced interfaces. It primarily covers PRD phases 1 (Reader) and 2 (Composer), plus parts of phase 3 (Organization) and phase 4 (Publishing Studio). See [docs/PHASES.md](docs/PHASES.md) for the implementation map and remaining boundaries. It does not claim completion of every P0–P2 release requirement.

The app includes local storage and deterministic mock backends for trying features without credentials. Live Mastodon and Bluesky accounts use the network adapters. Later roadmap modules include prototypes and service interfaces; their presence is not a release-readiness claim. Sounds remain deferred.

### Implemented

- **Accessible Standard and Advanced interfaces** — account list, account-scoped navigation, post list, and details; Standard mode hides advanced publishing tools. Up/Down move between posts; Left/Right read the configured fields of the focused post; Enter opens details. Focus is never stolen by a refresh. Command center, Where Am I, context help, remappable keymap.
- **Capability-driven architecture** — the UI adapts to what each account supports instead of assuming every network is the same. Mastodon, Bluesky, and GitHub have live adapters and capability descriptors; deterministic mock backends make everything runnable.
- **Intelligent thread splitting** — paragraph/sentence/word boundaries, never breaks links/mentions/hashtags/Markdown/code, `1/n` numbering reserved out of the limit, Mastodon URL/mention weighting.
- **Publishing studio** — scheduler + thread publisher state machines (pause-on-failure, repair/resume, idempotency, backoff), DST-correct queue schedules, accessible agenda/calendar with conflict detection, approvals with a role matrix and audit trail, recurring content with safeguards, CSV/TSV/JSON/Markdown bulk import, and optimal-time suggestions.
- **Local-first SQLite** — WAL, FTS5 search, a generic document store, and re-fetch that preserves local read/flag/folder state. Pruning never touches drafts, notes, schedules, folders, or bookmarks.
- **Moderation & safety** — local filters (text/regex/author/domain/…), mutes, blocks, reports that exclude private notes, and a slur-to-placeholder helper.
- **Notifications** — categories, per-account/category policies, duplicate grouping, quiet hours, focus modes, and plain-language digests.
- **AI assistance** (optional, never publishes) — provider gateway with disclosure and a deterministic mock provider, prompt-injection defense, writing tools that return drafts, source-cited summaries, and a heuristic accessibility assistant.
- **Media & ecosystem** — media engine interface (mpv boundary) with a null backend, transcripts (SRT/VTT/TXT/MD), the QUILL/Radio/Cast/Audio Studio/Beacon bridge as inspectable intents, and QUILL Longform (safe Markdown→HTML, teaser threads).
- **GitHub** — issues, PRs, discussions, releases, notifications, plus social↔GitHub (issue drafts with attribution and no private notes, releases→campaigns).
- **Analytics** — measured metrics as accessible data tables with CSV/Markdown export and period comparison.
- **Resilience & extensibility** — offline outbox that never silently publishes expired posts, circuit breaker, plugin system (manifest validation, permissions, safe mode, crash isolation), credential store that persists references not tokens, and redacted diagnostics.
- **Headless CLI** — `quill-social-cli` for `accounts`, `refresh`, `search`, and `split`.

### Live network sign-in

The Mastodon, Bluesky, and GitHub adapters have real dual-mode implementations. Add an account with an access token / app password (stored in the OS credential vault via `keyring` — the database only holds a reference) and, when the network's client library is installed, the adapter calls the live service and maps responses into the domain model. With no client or credential it keeps a clear "not enabled" boundary and the app runs on the mock backends. The local scheduler runs on a timer while the app is open, so scheduled posts actually publish.

### Remaining documented boundaries

Implemented as interfaces with deterministic defaults and an `available()` probe — wiring the real dependency changes no schema or UI: a real AI provider, libmpv playback, and QUILL Cloud scheduling. See [docs/PHASES.md](docs/PHASES.md).

## Running from source

Requires Python 3.12+ and wxPython.

```
pip install -e ".[networks,security]"
quill-social            # launch the accessible shell
run-quill-social.bat    # Windows: launch with a portable data folder
```

The first launch seeds a demo account so the timeline is immediately usable. Use **File > Add Account** to register a Mastodon or Bluesky account (live sign-in lights up when the `networks` extra is installed).

The Accounts list starts with **Unified Home**, followed by your accounts.
Choose an account with Up/Down to show its Home and scope its navigation,
cached posts, search results and publishing entries. Choose Unified Home to
see all accounts again. Advanced mode retains all existing destinations and tools.
GitHub and management tools retain their existing workspace-wide behavior.

### Additional timelines and direct messages

Use **Timeline** in Standard mode, or **View** in Advanced mode, to open a user,
hashtag or list timeline. Mastodon also supports local and remote-instance
views. Leave the list ID blank to choose from your account's lists. Added
views are saved per account and can be closed from the same menu. Remote-instance
views use public access and are read-only.

**Direct messages** appears in the reading navigation. Use **Post > New direct
message** (Standard) or **Compose > New direct message** (Advanced) to send,
or Reply on a received message. Mastodon shows the latest message in each
conversation; Bluesky loads recent chat messages. These views use separate
caches from Home. F5 refreshes the current added timeline or messages view.

New-view announcements are off initially. Enable them when opening a timeline,
or use **Toggle timeline announcements** for the current added view. Message
announcements are enabled per account in **Preferences > Account reading**.
Background checks run every minute; the first fetch does not read old history.

### Standard and Advanced modes

Choose **View > Standard mode / Advanced mode**, or **File > Preferences >
Interface mode**. The choice is saved and takes effect
immediately. Your account and ordinary timeline selection are preserved;
switching from an advanced-only view to Standard returns to Home. Standard is
the default when no mode has been saved.

Standard follows the recovered client's layout: Accounts, a flat Timelines list,
Posts, and Refresh/New Post/Reply/View/Links buttons. Enter opens the selected
post in a read-only dialog. Its menu bar is **File, Timeline, Post, Navigate,
View, Tools**, focused on everyday reading and posting. Search is available
with Ctrl+L or Navigate. It uses the recovered client's dialog: enter search
text, choose All, Posts, Users or Hashtags, and the selected account's service
opens the results in a closeable timeline. Unified Home asks which account to
use. Unread, Flagged, the old cached Search Results view, and Catch Up remain
available in Advanced mode without occupying the Standard timeline list. Posts
has its own visible label and accessible region.
Advanced publishing, creation and integration tools are hidden from Standard's
menus and Command Center.

Advanced keeps the account list, grouped navigation tree, visible search and
inline Details pane, along with Studio, calendar, scheduling, approvals,
analytics, plugins and integration tools. Expand tree sections with Right Arrow.

The Standard composer starts in the post editor. Its primary tab sequence is
editor, visibility, Publish, Save draft, Cancel, then **More options**. More
options exposes account selection, content warnings, media and alt text, and
polls and thread splitting. Collapsing options keeps their values and reports
active options. Templates and scheduling are shown only in Advanced.
Existing thread drafts open in the selected mode with threading preserved.
Tab navigates out of the editor in both modes.

Both modes use the same accounts, storage, adapters and publishing services;
they do not load the original recovered application. Sounds remain deferred.

Both File menus include **Edit My Profile**, **Export Current Timeline**,
**Export All Timelines**, **Create Settings Backup**, and **Restore Settings Backup**.
Profile editing uses the selected account, or asks for an account from Unified
Home. Mastodon supports name, biography, avatar/header, custom fields and supported
profile privacy settings. Bluesky supports name and biography. Changes are sent
only when Save is chosen; loading and saving run in the background.

Timeline exports are UTF-8 text with full post text, timestamps, links, content
warnings, media descriptions and polls. Current Timeline exports the displayed
posts; All Timelines exports the selected account's cached reading timelines
(all accounts from Unified Home), including smart folders. It does not download
older server history or export publishing queues as timelines.

Settings backups contain Preferences and keyboard shortcuts, not accounts,
credentials, posts, drafts or database settings. Automatic snapshots are made
at startup, on preference changes and on exit. Restore validates a Quill Social
backup, preserves a recovery copy and applies restored settings immediately.
Recovered-client backup files use a different format and cannot be imported.

Preferences includes General and Reading tabs. Reading controls include newest
posts at bottom, condensed leading mentions, hiding URLs from post rows,
relative or absolute timestamps, a 12-hour clock for absolute times, and read
state announcements. These presentation choices keep full post text available
in View Post and exports.

Additional Preferences pages port the recovered client's remaining non-sound
controls into the fork:

- **General / Manage Accounts:** change local aliases, choose a default posting
  account, pause accounts, or remove an account with explicit confirmation.
- **Composition:** word wrapping, separate reply recipients, Enter or Ctrl+Enter
  to publish, and publish/boost confirmations. Ctrl+Enter is the default; with
  Enter-to-send enabled, Shift+Enter inserts a newline. A declined publishing
  confirmation saves the post as a draft.
- **Timelines and fields:** per-account timeline names, order and visibility;
  post-field order and visibility; and cached sources to combine in Home.
  Duplicate posts appear once. Home remains available, and Advanced mode
  retains its grouped navigation. Restore defaults is available per account.
- **Account reading:** show post text, warning plus text, or only the content
  warning; choose automatic new-item announcements and mute them per account;
  optionally synchronize the Mastodon Home reading position. Marker requests
  run in the background, and do not move focus away from the control in use.
- **Behavior and cache:** open single links directly or use a chooser; optionally
  strip emojis/non-ASCII characters from reading rows; shorten automatic
  notification announcements to one sentence; optionally announce item and
  unread counts while navigating accounts and timelines in Advanced mode;
  restore the last account/timeline/post at startup; minimize to the system
  tray; and set server-fetch/display limits. Standard mode leaves those counts
  silent so the native account and timeline labels can be read uninterrupted.
  Cache clearing retains saved, flagged, favourited and annotated posts, drafts,
  and scheduled work. Multiple links always use a chooser.
- **Shortcuts:** assign, clear and reset local shortcuts, plus optional global
  shortcuts for showing Quill Social, composing, and refreshing. Conflicts are
  reported; menu accelerators follow the saved mappings. Global shortcuts are
  included in new settings backups; old backups preserve existing globals.

Screen-reader navigation remains available when automatic announcements are
muted. Presentation filters do not change stored post text or exports. Sounds
and soundpacks remain deferred.

Favourites, reposts and bookmarks announce their completed action (for example,
"Favourited" or "Repost removed") after the server accepts it. Failures are
announced without changing the local state. These explicit confirmations remain
audible at minimal verbosity. Windows installations include `accessible-output2`
for supported screen readers, with system speech fallback; messages also appear
in the status bar.

When automatic announcements are enabled, Quill checks for updates every minute
while open. It skips the initial history, announces each notification once,
and queues incoming messages without stealing focus. In the active account it
reads "from [author]: [post]"; elsewhere it uses "New home post from" or
"New mention from", with the account name when needed. Notifications identify
who favourited, boosted or followed. The first-sentence option shortens
notification details only, leaving ordinary Home posts complete.

Profile saves, minimizing/restoring the tray window, and opening links also
announce their outcome. Thread posting runs in the background and announces
each successfully sent part, completion, or the exact partial failure.
Scheduled deliveries announce success, queued retries and failures; removing an
offline Outbox item announces completion. Resolver failures on one scheduled
account do not stop delivery attempts for other accounts.

Use **Tools > Repeat last announcement**, the Command Center, or
**Ctrl+Shift+Space** to hear the last spoken message again. The shortcut can be
changed in Preferences. Quiet status updates do not replace the last spoken
message. These confirmations remain available with minimal verbosity.

Adding an account selects it and loads its timeline automatically. Live refresh
runs in the background; failures are reported without preventing other accounts
from loading. The `security` extra supplies the OS credential backend. If it is
unavailable, the app explicitly reports that sign-in lasts for the current session.
The Windows batch launcher uses `.venv` when present, otherwise the default Python.

This is the first gradual migration from the recovered client: account selection
and usable sign-in/refresh, adapted to this repository's existing UI, services,
adapters and database. Sounds and further recovered features are deferred.

Displayed dates and times use your system timezone by default, including its
daylight-saving rules. In **File > Preferences > Display time zone**, choose
**System timezone** or **UTC**. The choice is saved and applies immediately to
the main window and to subsequently opened dialogs. Relative times are unchanged.
This is a display preference: composer scheduling inputs remain explicitly
labelled UTC, queue schedules keep their own timezone, and stored posting times
do not change.

### Headless CLI

```
quill-social-cli accounts
quill-social-cli refresh
quill-social-cli search accessibility
quill-social-cli split "a long post..." --limit 300
```

## Keyboard model

| Key | Action |
| --- | --- |
| Up / Down | Previous / next post |
| Left / Right | Read previous / next field of the focused post |
| Enter | Open the post in Details |
| Ctrl+N | Compose |
| Ctrl+R | Reply |
| Ctrl+Shift+R | Boost / repost |
| Ctrl+Q | Quote |
| Ctrl+F | Favourite |
| Alt+B | Bookmark |
| Ctrl+G | Open conversation |
| Ctrl+Shift+C | Command center |
| Ctrl+Shift+I | Where Am I |
| F5 | Refresh |
| F6 / Shift+F6 | Next / previous visible pane (Accounts, Timelines, Posts in Standard) |
| F1 | Help |

Command shortcuts are remappable (Preferences); F6 provides pane navigation.

## Development

```
pip install -e ".[dev]"
pytest          # automated regression tests, wx-free domain + guarded UI smoke
ruff check .
```

Architecture notes live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

MIT. See [LICENSE](LICENSE).
