# Testing QUILL Social with real accounts

Install the project dependencies, including the `networks` extra, before testing live sign-in. This guide gets you from launch to a real timeline.

## 1. Launch

From your repository checkout:

```
run-quill-social.bat
```

or

```
python -m quill_social
```

The app opens on the local demo timeline so you can move around before signing
in. Press `F1` for the keyboard guide.

## 2. Add an account

`File > Add Account` (or the command center: `Ctrl+Shift+C`, type "add
account"). The dialog has:

- **Network** — mastodon, bluesky, or mock.
- **Server / instance** — an editable dropdown seeded (alphabetically) with
  `caneandable.social`, `leaseysocial.com`, `mastodon.online`, `mastodon.social`,
  and `tweesecake.social`. Pick one or type any server. (For Bluesky it defaults
  to `bsky.social`.)
- **Handle** — your handle (display on Mastodon; sign-in identifier on Bluesky).
- **Sign in with browser** — starts the OAuth flow (Mastodon) or opens the app
  password page (Bluesky).
- **Authorization code** + **Finish sign-in** — where you paste the Mastodon
  code and exchange it for a token.
- **Verify connection** — tests the credential against the live network and
  reports the result before you commit. Use it first.

Your secret is stored in the Windows Credential Manager, never in the database.

### Mastodon (browser sign-in)

No app setup needed:

1. Pick your server (e.g. `leaseysocial.com`) and choose **Sign in with
   browser**. QUILL Social registers itself with the server (once) and opens
   the server's authorization page.
2. Log in there if needed and approve access. The server shows an
   authorization **code**.
3. Copy the code, paste it into the **Authorization code** field, and choose
   **Finish sign-in**. The app exchanges it for an access token automatically.
4. Choose **Verify connection**, then **OK**.

(You can also paste an access token directly in the "Or paste a token" field if
you already have one.)

### Bluesky app password

1. In the Bluesky app or web: `Settings > Privacy and security > App passwords`.
2. Add an app password and copy it (format `xxxx-xxxx-xxxx-xxxx`).
3. In the dialog: network `bluesky`, server `bsky.social`, handle your full
   handle (e.g. `you.bsky.social`), and paste the **app password** (not your
   real password). Click **Verify connection**.

## 3. Refresh and read

After adding, press `F5` to pull your home timeline and notifications into the
local cache. Then:

- `Up`/`Down` move between posts; `Left`/`Right` read the fields of the focused
  post; `Enter` opens details.
- `Ctrl+N` compose, `Ctrl+R` reply, `Ctrl+F` favourite, `Alt+B` bookmark,
  `Ctrl+Shift+R` boost, `Ctrl+G` open conversation.
- `Ctrl+Shift+I` Where Am I; `Ctrl+Shift+C` command center.

The navigation tree also has GitHub, Library, Publishing, Discover, and per-
account feeds. The Studio and Tools menus reach drafts, the calendar,
approvals, safety, notifications, analytics, and AI tools.

## Notes and limits

- Adding more instances later: just type the server in the dialog; the preset
  list is only a convenience.
- If **Verify connection** fails, the status line shows the network's error
  (bad token, wrong scope, wrong server, rate limit). Fix and retry.
- Mastodon boosts show as one row attributed to the booster; polls, content
  warnings, media, and alt text all map through.
- Bluesky has no native polls and only public visibility; the composer's live
  report will tell you when something is not supported on a target.
- The local scheduler runs every 30 seconds while the app is open, so a
  scheduled post publishes only while QUILL Social is running.

## PR regression checks

Run `python -m pytest -q` and `python -m ruff check .` in the project environment.

Check both Standard and Advanced modes with a real account:

- Navigate accounts and timelines without focus moving away from the selected control.
- Open a conversation whose replies are not yet cached; confirm the network thread loads. Navigate away during loading and confirm it does not replace the new timeline.
- Publish an image with alt text, a Mastodon poll, a reply, and a quote. Confirm the content on the server. Bluesky polls and unsupported attachment kinds should report an error and retain the draft.
- Disconnect during publishing, then reopen the saved draft and check its attachments, poll, reply, visibility, and content warning. For partially sent threads or cross-posts, check successful destinations before resending.
- Schedule a post and confirm keyboard navigation stays responsive during delivery. Failed partial threads should require review rather than restart automatically.
- Set quiet hours covering the current system time and confirm automatic notification speech is suppressed while items remain available.
- Check real notification event wording, author punctuation, quoted-post reading, local timezone display, and preferences persistence.

Screen-reader testing status:

- **NVDA: tested on Windows by the contributor**, including the testing session that identified mixed items in Home.
- JAWS and Narrator: not yet tested.

The Home cache fix has automated regression coverage; check Home again with a real account after restarting. Other platforms and release packaging remain separate validation work. Sounds are deferred.
