"""Mastodon adapter (PRD 22).

Two modes share one class. Without a live client (the default,
``MastodonAdapter()``) the adapter still ships the capability descriptor and a
clear boundary: every network method raises an :class:`AdapterError` whose
message contains "not enabled", so the UI can explain the state instead of
failing opaquely. With a client injected -- either a real ``mastodon.Mastodon``
built by the registry from a stored access token (PRD 31.1), or a fake in
tests -- the same methods call the client and MAP its responses into the
domain model.

The response->model mapping lives in pure module-level functions
(:func:`_status_to_item`, :func:`_media_to_model`, :func:`_poll_to_model`) so it
can be unit-tested with fixture dicts WITHOUT importing Mastodon.py. Mastodon.py
returns ``AttribAccessDict`` values that behave like plain dicts, so the mapping
consumes the API response directly.

API assumptions (Mastodon.py, stable documented API; Context7 MCP was not
reachable in this environment so these were confirmed against the published
mastodonpy docs): the client exposes ``timeline_home(limit=, since_id=)``,
``notifications(limit=)``, ``status_context(id)``, ``status(id)``,
``status_post(status, visibility=, spoiler_text=, language=, in_reply_to_id=)``
and the ``status_(un)favourite/bookmark/reblog(id)`` toggles. A status dict
carries ``id, uri, url, content (HTML), created_at (datetime), account,
visibility, spoiler_text, sensitive, language, in_reply_to_id, replies_count,
reblogs_count, favourites_count, favourited, bookmarked, reblogged,
media_attachments, poll, reblog``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from quill_social.adapters.base import (
    AdapterError,
    NetworkAdapter,
    NotificationEvent,
    PublishRequest,
    PublishResult,
)
from quill_social.capabilities import Capabilities, default_for
from quill_social.model import Media, Poll, PollOption, SocialItem, now_ms

_NOT_WIRED = (
    "The live Mastodon adapter is not enabled in this build. Install the "
    "'networks' extra and add an account to connect."
)


# -- pure mapping helpers -----------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_QUOTE_INLINE_RE = re.compile(
    r"<p\b[^>]*\bclass\s*=\s*(?:\"[^\"]*\bquote-inline\b[^\"]*\"|"
    r"'[^']*\bquote-inline\b[^']*')[^>]*>.*?</p\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _to_ms(value: Any) -> int:
    """Normalize a Mastodon timestamp (datetime | ISO string | epoch) to ms."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    return 0


def _html_to_text(html: str) -> str:
    """Flatten Mastodon's HTML ``content`` into readable plain text."""
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = _TAG_RE.sub("", text)
    return unescape(text).strip()


def _status_text(status: dict) -> str:
    """Flatten status content, hiding Mastodon's native-quote compatibility link."""
    content = status.get("content", "") or ""
    has_structured_quote = (
        isinstance(status.get("quote"), dict)
        or isinstance(status.get("quoted_status"), dict)
    )
    if has_structured_quote:
        content = _QUOTE_INLINE_RE.sub("", content)
    return _html_to_text(content)


def _acct_handle(account: dict) -> str:
    acct = (account or {}).get("acct", "") or ""
    if not acct:
        return ""
    return acct if acct.startswith("@") else f"@{acct}"


def _media_to_model(attachments: list | None) -> list[Media]:
    """Map ``media_attachments`` into :class:`Media`."""
    out: list[Media] = []
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        out.append(
            Media(
                kind=att.get("type", "unknown") or "unknown",
                uri=att.get("url") or att.get("remote_url") or "",
                mime=att.get("mime_type", "") or "",
                alt_text=att.get("description", "") or "",
            )
        )
    return out


def _poll_to_model(poll: dict | None) -> Poll | None:
    """Map a Mastodon ``poll`` dict into :class:`Poll`."""
    if not poll or not isinstance(poll, dict):
        return None
    options = [
        PollOption(o.get("title", "") or "", int(o.get("votes_count", 0) or 0))
        for o in poll.get("options", []) or []
        if isinstance(o, dict)
    ]
    return Poll(
        options=options,
        multiple=bool(poll.get("multiple", False)),
        expires_at=_to_ms(poll.get("expires_at")) or None,
        voted=bool(poll.get("voted", False)),
        own_votes=list(poll.get("own_votes", []) or []),
        total_votes=int(poll.get("votes_count", 0) or 0),
    )


def _status_to_item(status: dict, *, account_id: str = "") -> SocialItem:
    """Map one Mastodon status dict into a :class:`SocialItem`.

    A boost (``reblog`` present) is represented as one row whose content is the
    boosted status', with ``reblog_by``/``reblog_of`` recording the booster,
    mirroring ``adapters/mock.py``.
    """
    boost = status.get("reblog") if isinstance(status, dict) else None
    inner = boost if isinstance(boost, dict) else status
    account = inner.get("account", {}) or {}
    item = SocialItem(
        network="mastodon",
        account_id=account_id,
        remote_id=str(inner.get("id", "") or ""),
        uri=inner.get("url") or inner.get("uri", "") or "",
        author_handle=_acct_handle(account),
        author_display=account.get("display_name", "") or "",
        author_id=str(account.get("id", "") or ""),
        text=_status_text(inner),
        lang=inner.get("language", "") or "",
        created_at=_to_ms(inner.get("created_at")),
        visibility=inner.get("visibility", "public") or "public",
        content_warning=inner.get("spoiler_text", "") or "",
        sensitive=bool(inner.get("sensitive", False)),
        in_reply_to=str(inner.get("in_reply_to_id") or "") or "",
        reply_count=int(inner.get("replies_count", 0) or 0),
        reblog_count=int(inner.get("reblogs_count", 0) or 0),
        favourite_count=int(inner.get("favourites_count", 0) or 0),
        favourited=bool(inner.get("favourited", False)),
        bookmarked=bool(inner.get("bookmarked", False)),
        reblogged=bool(inner.get("reblogged", False)),
        media=_media_to_model(inner.get("media_attachments")),
        poll=_poll_to_model(inner.get("poll")),
    )
    if isinstance(boost, dict):
        item.reblog_of = str(inner.get("id", "") or "")
        item.reblog_by = _acct_handle(status.get("account", {}) or {})
    quote = inner.get("quote") if isinstance(inner, dict) else None
    quoted = quote.get("quoted_status") if isinstance(quote, dict) else None
    quote_state = str(quote.get("state") or "") if isinstance(quote, dict) else ""
    if not isinstance(quoted, dict):
        legacy = inner.get("quoted_status") if isinstance(inner, dict) else None
        quoted = legacy if isinstance(legacy, dict) else None
    if isinstance(quoted, dict) and quote_state in {"", "accepted"}:
        item.quote_of = str(quoted.get("id") or "")
        quoted_account = quoted.get("account") or {}
        quoted_author = (quoted_account.get("display_name")
                         or _acct_handle(quoted_account) or "Unknown author")
        quoted_text = _html_to_text(quoted.get("content", "") or "")
        quoted_warning = quoted.get("spoiler_text", "") or ""
        parts = [f"Quoted post by {quoted_author}:"]
        if quoted_warning:
            parts.append(f"Content warning: {quoted_warning}")
        parts.append(quoted_text or "(no text)")
        for media in _media_to_model(quoted.get("media_attachments")):
            parts.append(f"Media ({media.kind}): {media.alt_text or '(no alt text)'}")
        item.text = "\n\n".join(filter(None, (item.text, "\n".join(parts))))
    elif isinstance(quote, dict):
        item.quote_of = str(
            quote.get("quoted_status_id")
            or (quoted.get("id") if isinstance(quoted, dict) else "")
            or quote.get("id") or f"quote:{quote_state or 'unavailable'}")
        state = quote_state.replace("_", " ") or "unavailable"
        wording = {
            "pending": "Quoted post pending approval.",
            "rejected": "Quoted post was rejected.",
            "revoked": "Quoted post permission was revoked.",
            "deleted": "Quoted post was deleted.",
            "unauthorized": "Quoted post is not available to this account.",
            "blocked account": "Quoted post hidden because you blocked its author.",
            "blocked domain": "Quoted post hidden because you blocked its domain.",
            "muted account": "Quoted post hidden because you muted its author.",
        }.get(state, "Quoted post unavailable.")
        item.text = "\n\n".join(filter(None, (item.text, wording)))
    return item


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "http_status", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    for arg in getattr(exc, "args", ()) or ():
        if isinstance(arg, int):
            return arg
    return None


def _retry_ms(exc: Exception) -> int:
    val = getattr(exc, "retry_after_ms", None)
    if isinstance(val, int):
        return val
    secs = getattr(exc, "retry_after", None)
    if isinstance(secs, (int, float)):
        return int(secs * 1000)
    return 60_000


def _mastodon_error(exc: Exception) -> AdapterError:
    """Normalize a Mastodon.py exception into an :class:`AdapterError`."""
    if isinstance(exc, AdapterError):
        return exc
    name = type(exc).__name__
    status = _status_code(exc)
    msg = str(exc) or "mastodon error"
    if "Ratelimit" in name or status == 429:
        return AdapterError(msg, kind="transient", retry_after_ms=_retry_ms(exc))
    if "Unauthorized" in name or status in (401, 403):
        return AdapterError(msg, kind="permission")
    if "NotFound" in name or "BadRequest" in name or status in (400, 404, 422):
        return AdapterError(msg, kind="validation")
    return AdapterError(msg, kind="unknown")


def _notification_detail(note: dict) -> str:
    """Extract readable context from Mastodon notifications without a status."""
    kind = note.get("type") or ""
    if kind == "moderation_warning":
        warning = note.get("moderation_warning") or {}
        return str(warning.get("text") or warning.get("action") or "")
    if kind == "severed_relationships":
        event = note.get("event") or {}
        target = event.get("target_name") or event.get("type") or ""
        followers = int(event.get("followers_count", 0) or 0)
        following = int(event.get("following_count", 0) or 0)
        counts = []
        if followers:
            counts.append(f"{followers} follower relationship{'s' if followers != 1 else ''}")
        if following:
            counts.append(f"{following} following relationship{'s' if following != 1 else ''}")
        return ", ".join(filter(None, (str(target), *counts)))
    if kind == "admin.report":
        report = note.get("report") or {}
        return str(report.get("comment") or "")
    if kind in {"added_to_collection", "collection_update"}:
        collection = note.get("collection") or {}
        return str(collection.get("name") or collection.get("title") or "")
    fallback = note.get("fallback") or {}
    return str(fallback.get("body") or fallback.get("title") or "")


# -- adapter ------------------------------------------------------------------


class MastodonAdapter(NetworkAdapter):
    name = "mastodon"

    def __init__(
        self,
        instance: str = "",
        access_token: str = "",
        *,
        account_id: str = "",
        client: Any = None,
    ) -> None:
        self.instance = instance
        self._token = access_token  # never logged; resolved from the OS keystore
        self._account_id = account_id
        self._client = client  # a live mastodon.Mastodon or an injected fake
        self._caps = default_for("mastodon")

    def capabilities(self) -> Capabilities:
        return self._caps

    def refine_from_instance(self, instance_meta: dict) -> Capabilities:
        """Sharpen capabilities from a live ``/api/v1/instance`` response.

        Mastodon exposes ``configuration.statuses.max_characters``,
        ``max_media_attachments``, poll limits, and (on newer servers) whether
        quote posts are supported. We only overwrite fields the server actually
        reports, leaving conservative defaults elsewhere (PRD 11.4).
        """
        cfg = (instance_meta or {}).get("configuration", {})
        statuses = cfg.get("statuses", {})
        polls = cfg.get("polls", {})
        media = cfg.get("media_attachments", {})
        overrides: dict = {}
        if "max_characters" in statuses:
            overrides["char_limit"] = int(statuses["max_characters"])
        if "max_media_attachments" in statuses:
            overrides["max_media_attachments"] = int(statuses["max_media_attachments"])
        if "max_options" in polls:
            overrides["max_poll_options"] = int(polls["max_options"])
        if "description_limit" in media:
            overrides["max_alt_text"] = int(media["description_limit"])
        if "version" in instance_meta:
            overrides["server_version"] = str(instance_meta["version"])
        api_version = ((instance_meta or {}).get("api_versions", {}) or {}).get("mastodon")
        if api_version is not None:
            try:
                overrides["supports_quote"] = int(api_version) >= 7
            except (TypeError, ValueError):
                pass
        elif "version" in instance_meta:
            try:
                major, minor = (int(part) for part in str(instance_meta["version"]).split(".")[:2])
                overrides["supports_quote"] = (major, minor) >= (4, 5)
            except (TypeError, ValueError):
                pass
        self._caps = self._caps.merge(**overrides)
        return self._caps

    def probe_capabilities(self) -> Capabilities:
        """Fetch current instance metadata and refine per-account capabilities."""
        client = self._require_client()
        try:
            if hasattr(client, "instance_v2"):
                meta = client.instance_v2()
            else:
                meta = client.instance()
        except Exception as exc:  # noqa: BLE001
            raise _mastodon_error(exc) from exc
        return self.refine_from_instance(meta or {})

    # -- live methods (require an injected/built client) ----------------------

    def _require_client(self) -> Any:
        if self._client is None:
            raise AdapterError(_NOT_WIRED, kind="permission")
        return self._client

    def home_timeline(self, *, limit: int = 40, since_id: str = "") -> list[SocialItem]:
        client = self._require_client()
        kwargs: dict[str, Any] = {"limit": min(limit, 40)}
        if since_id:
            kwargs["since_id"] = since_id
        try:
            statuses = list(client.timeline_home(**kwargs) or [])
            while len(statuses) < limit and statuses and len(statuses) % 40 == 0:
                kwargs["max_id"] = statuses[-1]["id"]
                kwargs["limit"] = min(40, limit - len(statuses))
                page = list(client.timeline_home(**kwargs) or [])
                seen = {str(status["id"]) for status in statuses}
                page = [status for status in page if str(status["id"]) not in seen]
                if not page:
                    break
                statuses.extend(page)
        except Exception as exc:  # noqa: BLE001 -- normalized below
            raise _mastodon_error(exc) from exc
        return [_status_to_item(s, account_id=self._account_id) for s in statuses or []]

    def timeline_lists(self) -> list[tuple[str, str]]:
        try:
            return [(str(row["id"]), str(row.get("title") or "Untitled list"))
                    for row in self._require_client().lists() or []]
        except Exception as exc:  # noqa: BLE001
            raise _mastodon_error(exc) from exc

    def fetch_timeline(self, kind: str, value: str = "", *, limit: int = 40) -> list[SocialItem]:
        client = self._require_client()
        value = value.strip()
        if kind in {"list", "user", "hashtag", "instance"} and not value:
            raise AdapterError("Enter a timeline name or address.", kind="validation")
        try:
            if kind == "messages":
                rows = [row.get("last_status") for row in client.conversations(limit=limit) or []]
                rows = [row for row in rows if row and row.get("visibility") == "direct"]
            elif kind == "list":
                rows = client.timeline_list(value, limit=limit)
            elif kind == "user":
                account = client.account_lookup(value.lstrip("@"))
                rows = client.account_statuses(account["id"], limit=limit)
            elif kind == "hashtag":
                tag = value.lstrip("#")
                if not tag or any(c.isspace() for c in tag):
                    raise AdapterError("Enter a single hashtag.", kind="validation")
                rows = client.timeline_hashtag(tag, limit=limit)
            elif kind == "local":
                rows = client.timeline_local(limit=limit)
            elif kind == "instance":
                address = urlsplit(value if "://" in value else "https://" + value)
                if (address.scheme != "https" or not address.hostname or address.username
                        or address.password or address.path not in {"", "/"}
                        or address.query or address.fragment):
                    raise AdapterError("Enter an HTTPS instance address without a path.", kind="validation")
                # This separate public session must never receive the account's token.
                from mastodon import Mastodon
                public = Mastodon(api_base_url="https://" + address.netloc, request_timeout=30)
                rows = public.timeline_local(limit=limit)
            else:
                raise AdapterError("Unknown timeline type.", kind="validation")
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _mastodon_error(exc) from exc
        items = [_status_to_item(row, account_id=self._account_id) for row in rows or []]
        if kind == "instance":
            for item in items:
                item.remote_id = f"instance:{address.netloc.lower()}:{item.remote_id}"
        return items

    def send_direct_message(self, recipient: str, text: str) -> PublishResult:
        recipient = recipient.strip().lstrip("@")
        if not re.fullmatch(r"[\w.-]+(?:@[\w.-]+(?::\d+)?)?", recipient) or not text.strip():
            raise AdapterError("Enter a recipient handle and message.", kind="validation")
        client = self._require_client()
        try:
            account = client.account_lookup(recipient)
            handle = str(account.get("acct") or recipient)
        except Exception as exc:  # noqa: BLE001
            raise _mastodon_error(exc) from exc
        # Direct visibility is explicit, with no fallback to public publishing.
        return self.publish(PublishRequest(text=f"@{handle} {text.strip()}", visibility="direct"))

    def home_position(self) -> str:
        try:
            return str(self._require_client().markers_get("home").get("home", {}).get("last_read_id", ""))
        except Exception as exc:
            raise _mastodon_error(exc) from exc

    def search(self, query: str, search_type: str = "", *, limit: int = 40) -> list[SocialItem]:
        query = query.strip()
        if not query or search_type not in {"", "statuses", "accounts", "hashtags"}:
            raise AdapterError("Enter search text and choose a valid result type.", kind="validation")
        try:
            result = self._require_client().search_v2(
                query, resolve=True, result_type=search_type or None, limit=limit)
        except Exception as exc:
            raise _mastodon_error(exc) from exc
        items = []
        if search_type in {"", "statuses"}:
            items.extend(_status_to_item(row, account_id=self._account_id)
                         for row in result.get("statuses", []) or [])
        if search_type in {"", "accounts"}:
            for account in result.get("accounts", []) or []:
                handle = _acct_handle(account)
                display = account.get("display_name", "") or handle
                note = _html_to_text(account.get("note", "") or "")
                items.append(SocialItem(
                    network="mastodon", account_id=self._account_id,
                    remote_id=f"search:user:{account.get('id') or handle.casefold()}",
                    uri=account.get("url", "") or "", author_id=str(account.get("id", "") or ""),
                    author_handle=handle, author_display=display,
                    text=f"User: {display} {handle}. {note}".strip(),
                ))
        if search_type in {"", "hashtags"}:
            for hashtag in result.get("hashtags", []) or []:
                name = str(hashtag.get("name", "") or "").lstrip("#")
                if name:
                    items.append(SocialItem(
                        network="mastodon", account_id=self._account_id,
                        remote_id=f"search:hashtag:{name.casefold()}",
                        uri=hashtag.get("url", "") or "", author_handle=f"#{name}",
                        author_display="Hashtag", text=f"Hashtag: #{name}",
                    ))
        return items

    def save_home_position(self, remote_id: str) -> None:
        try:
            self._require_client().markers_set("home", remote_id)
        except Exception as exc:
            raise _mastodon_error(exc) from exc

    def own_profile(self) -> dict:
        client = self._require_client()
        try:
            account = client.account_verify_credentials()
        except Exception as exc:
            raise _mastodon_error(exc) from exc
        source = account.get("source") or {}
        result = {
            "display_name": account.get("display_name") or "",
            "note": source.get("note", _html_to_text(account.get("note") or "")),
            "fields": [(f.get("name", ""), f.get("value", ""))
                       for f in source.get("fields", [])],
        }
        for key in ("locked", "bot", "discoverable", "hide_collections", "indexable"):
            if account.get(key) is not None:
                result[key] = bool(account[key])
        return result

    def update_profile(self, changes: dict) -> None:
        client = self._require_client()
        allowed = {"display_name", "note", "fields", "avatar", "header", "locked",
                   "bot", "discoverable", "hide_collections", "indexable"}
        try:
            client.account_update_credentials(**{k: v for k, v in changes.items() if k in allowed})
        except Exception as exc:
            raise _mastodon_error(exc) from exc

    def notifications(self, *, limit: int = 40) -> list[SocialItem]:
        return [event.item for event in self.notification_events(limit=limit)
                if event.item is not None]

    def notification_events(self, *, limit: int = 40) -> list[NotificationEvent]:
        client = self._require_client()
        try:
            notes = client.notifications(limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise _mastodon_error(exc) from exc
        out: list[NotificationEvent] = []
        for note in notes or []:
            if not isinstance(note, dict):
                continue
            status = note.get("status")
            actor = note.get("account") or {}
            out.append(NotificationEvent(
                notification_id=str(note.get("id") or ""),
                kind=note.get("type") or "unknown",
                actor_name=actor.get("display_name") or "",
                actor_handle=_acct_handle(actor), account_id=self._account_id,
                created_at=_to_ms(note.get("created_at")),
                text=_notification_detail(note),
                item=(_status_to_item(status, account_id=self._account_id)
                      if isinstance(status, dict) else None),
            ))
        return out

    def thread(self, item: SocialItem) -> list[SocialItem]:
        client = self._require_client()
        target = item.remote_id
        try:
            context = client.status_context(target)
            focus = client.status(target)
        except Exception as exc:  # noqa: BLE001
            raise _mastodon_error(exc) from exc
        context = context or {}
        rows = list(context.get("ancestors", []) or [])
        if isinstance(focus, dict):
            rows.append(focus)
        rows.extend(context.get("descendants", []) or [])
        items = [_status_to_item(s, account_id=self._account_id) for s in rows]
        items.sort(key=lambda it: it.created_at)
        return items

    def publish(self, request: PublishRequest) -> PublishResult:
        client = self._require_client()
        try:
            if request.quote_of and (request.media or request.poll):
                raise AdapterError("Mastodon quotes cannot include media or a poll.", kind="validation")
            if request.media and request.poll:
                raise AdapterError("Choose media or a poll, not both.", kind="validation")
            kwargs = {
                "visibility": "private" if request.visibility == "followers" else request.visibility or None,
                "spoiler_text": request.content_warning or None,
                "language": request.lang or None,
                "in_reply_to_id": request.in_reply_to or None,
            }
            if request.quote_of:
                kwargs["quoted_status_id"] = request.quote_of
            if request.idempotency_key:
                kwargs["idempotency_key"] = request.idempotency_key
            if request.poll:
                duration = (request.poll_expires_in if request.poll_expires_in is not None
                            else ((request.poll.expires_at or 0) - now_ms()) // 1000)
                if duration < 300 or len(request.poll.options) < 2:
                    raise AdapterError("The poll needs two options and at least five minutes remaining.", kind="validation")
                kwargs["poll"] = client.make_poll(
                    [option.title for option in request.poll.options],
                    expires_in=duration, multiple=request.poll.multiple)
            if request.media:
                uploads = []
                for attachment in request.media:
                    if not attachment.local_path:
                        raise AdapterError("An attachment is missing its local file.", kind="validation")
                    uploads.append(client.media_post(
                        attachment.local_path, description=attachment.alt_text or None,
                        synchronous=True))
                kwargs["media_ids"] = [str(upload["id"]) for upload in uploads]
                kwargs["sensitive"] = any(media.sensitive for media in request.media)
            status = client.status_post(request.text, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _mastodon_error(exc) from exc
        item = _status_to_item(status, account_id=self._account_id)
        return PublishResult(remote_id=item.remote_id, uri=item.uri, item=item)

    def _toggle(self, on_method: str, off_method: str, remote_id: str, on: bool) -> None:
        client = self._require_client()
        try:
            getattr(client, on_method if on else off_method)(remote_id)
        except Exception as exc:  # noqa: BLE001
            raise _mastodon_error(exc) from exc

    def set_favourite(self, remote_id: str, on: bool = True) -> None:
        self._toggle("status_favourite", "status_unfavourite", remote_id, on)

    def set_bookmark(self, remote_id: str, on: bool = True) -> None:
        self._toggle("status_bookmark", "status_unbookmark", remote_id, on)

    def set_reblog(self, remote_id: str, on: bool = True) -> None:
        self._toggle("status_reblog", "status_unreblog", remote_id, on)

    @classmethod
    def available(cls) -> bool:
        try:
            import mastodon  # noqa: F401
        except ImportError:
            return False
        return True
