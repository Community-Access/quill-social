"""Bluesky adapter (PRD 23).

Same two-mode shape as the Mastodon adapter. Without a live client
(``BlueskyAdapter()``) it ships the capability descriptor and raises a clear
"not enabled" :class:`AdapterError` at every network method. With a client
injected -- a real ``atproto.Client`` built and logged in by the registry, or a
fake in tests -- the methods call the client and MAP its responses into the
domain model.

Bluesky's model differs sharply from Mastodon: durable DID identity, no native
polls, quote posts and thread gates as first-class features, custom feeds, a
300-grapheme limit. The capability defaults encode those differences so the UI
adapts without pretending the two networks are the same (PRD 6.2). Identity is
keyed on the account DID, not the mutable handle (PRD 11.3).

The response->model mapping lives in pure module-level functions
(:func:`_feedview_to_item`, :func:`_post_to_item`) that consume plain dicts, so
they can be unit-tested with fixture dicts WITHOUT importing atproto. The live
methods convert the SDK's typed models to dicts via ``model_dump()`` before
mapping.

API assumptions (atproto Python SDK, stable documented API; Context7 MCP was not
reachable so these were confirmed against the published atproto.blue docs):
``Client().login(handle, app_password)``; ``get_timeline(limit=)`` returns a
response with ``.feed`` of ``FeedViewPost`` (each ``.post`` a ``PostView`` with
``uri, cid, author{did,handle,display_name}, record{text,created_at,langs,reply},
reply_count, repost_count, like_count, indexed_at, viewer{like,repost},
embed, labels``); ``send_post(text=)`` returns ``.uri``/``.cid``;
``get_post_thread(uri)`` returns ``.thread`` (a ThreadViewPost tree);
``like(uri, cid)``/``repost(uri, cid)`` and ``unlike``/``unrepost`` toggle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from quill_social.adapters.base import (
    AdapterError,
    NetworkAdapter,
    NotificationEvent,
    PublishRequest,
    PublishResult,
)
from quill_social.capabilities import Capabilities, default_for
from quill_social.model import Media, SocialItem

_NOT_WIRED = (
    "The live Bluesky adapter is not enabled in this build. Install the "
    "'networks' extra and add an account to connect."
)


# -- pure mapping helpers -----------------------------------------------------


def _to_ms(value: Any) -> int:
    """Normalize an AT Protocol timestamp (ISO string | datetime | epoch) to ms."""
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


def _as_dict(obj: Any) -> dict:
    """Coerce an atproto typed model (or dict) to a plain dict for mapping."""
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        result = dump()
        if isinstance(result, dict):
            return result
    return {}


def _handle(author: dict) -> str:
    h = (author or {}).get("handle", "") or ""
    if not h:
        return ""
    return h if h.startswith("@") else f"@{h}"


def _embed_media(embed: dict | None) -> list[Media]:
    """Map an ``app.bsky.embed.images#view`` embed into :class:`Media`."""
    if not embed or not isinstance(embed, dict):
        return []
    images = embed.get("images")
    out: list[Media] = []
    if isinstance(images, list):
        for img in images:
            if not isinstance(img, dict):
                continue
            out.append(
                Media(
                    kind="image",
                    uri=img.get("fullsize") or img.get("thumb") or "",
                    alt_text=img.get("alt", "") or "",
                )
            )
    return out


def _post_to_item(post: dict, *, account_id: str = "", reason: dict | None = None) -> SocialItem:
    """Map one Bluesky ``PostView`` dict into a :class:`SocialItem`."""
    post = post or {}
    author = post.get("author", {}) or {}
    record = post.get("record", {}) or {}
    viewer = post.get("viewer", {}) or {}
    langs = record.get("langs") or []
    uri = post.get("uri", "") or ""
    item = SocialItem(
        network="bluesky",
        account_id=account_id,
        remote_id=uri,
        uri=uri,
        author_handle=_handle(author),
        author_display=author.get("display_name", "") or "",
        author_id=author.get("did", "") or "",
        text=record.get("text", "") or "",
        lang=(langs[0] if langs else "") or "",
        created_at=_to_ms(record.get("created_at") or post.get("indexed_at")),
        reply_count=int(post.get("reply_count", 0) or 0),
        reblog_count=int(post.get("repost_count", 0) or 0),
        favourite_count=int(post.get("like_count", 0) or 0),
        favourited=bool(viewer.get("like")),
        reblogged=bool(viewer.get("repost")),
        media=_embed_media(post.get("embed")),
    )
    reply = record.get("reply") or {}
    if isinstance(reply, dict) and reply:
        item.in_reply_to = (reply.get("parent") or {}).get("uri", "") or ""
        item.thread_root = (reply.get("root") or {}).get("uri", "") or ""
    labels = post.get("labels") or []
    if labels:
        item.sensitive = True
        item.moderation_labels = [
            lbl.get("val", "") for lbl in labels if isinstance(lbl, dict) and lbl.get("val")
        ]
    if reason and isinstance(reason, dict):
        by = reason.get("by") or {}
        if by:
            item.reblog_by = _handle(by)
            item.reblog_of = uri
    return item


def _feedview_to_item(feed_view: dict, *, account_id: str = "") -> SocialItem:
    """Map one ``FeedViewPost`` dict (``{post, reason}``) into a :class:`SocialItem`."""
    feed_view = feed_view or {}
    post = feed_view.get("post") if isinstance(feed_view.get("post"), dict) else feed_view
    return _post_to_item(post, account_id=account_id, reason=feed_view.get("reason"))


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    for arg in getattr(exc, "args", ()) or ():
        if isinstance(arg, int):
            return arg
    return None


def _bluesky_error(exc: Exception) -> AdapterError:
    """Normalize an atproto exception into an :class:`AdapterError`."""
    if isinstance(exc, AdapterError):
        return exc
    name = type(exc).__name__
    status = _status_code(exc)
    msg = str(exc) or "bluesky error"
    if "RateLimit" in name or "Ratelimit" in name or status == 429:
        return AdapterError(msg, kind="transient", retry_after_ms=60_000)
    if "Auth" in name or "Unauthorized" in name or status in (401, 403):
        return AdapterError(msg, kind="permission")
    if "NotFound" in name or "BadRequest" in name or status in (400, 404, 422):
        return AdapterError(msg, kind="validation")
    return AdapterError(msg, kind="unknown")


# -- adapter ------------------------------------------------------------------


class BlueskyAdapter(NetworkAdapter):
    name = "bluesky"

    def __init__(
        self,
        did: str = "",
        service: str = "https://bsky.social",
        *,
        account_id: str = "",
        client: Any = None,
    ) -> None:
        self.did = did  # durable identity; handles are mutable (PRD 11.3)
        self.service = service
        self._account_id = account_id or did
        self._client = client  # a live atproto.Client or an injected fake
        self._caps = default_for("bluesky")

    def capabilities(self) -> Capabilities:
        return self._caps

    def refine_from_server(self, describe: dict) -> Capabilities:
        """Sharpen capabilities from a live ``describeServer`` / limits probe.

        Most Bluesky limits are protocol constants (300 graphemes, 4 images),
        already the defaults; a PDS can still report availability of video and
        DM features, which we honor when present.
        """
        overrides: dict = {}
        if "video" in describe:
            overrides["supports_video"] = bool(describe["video"])
        if "chat" in describe:
            overrides["supports_direct_messages"] = bool(describe["chat"])
        if "version" in describe:
            overrides["server_version"] = str(describe["version"])
        self._caps = self._caps.merge(**overrides)
        return self._caps

    # -- live methods (require an injected/built client) ----------------------

    def _require_client(self) -> Any:
        if self._client is None:
            raise AdapterError(_NOT_WIRED, kind="permission")
        return self._client

    def own_profile(self) -> dict:
        client = self._require_client()
        try:
            response = client.com.atproto.repo.get_record({
                "repo": self.did, "collection": "app.bsky.actor.profile", "rkey": "self",
            })
            record = _as_dict(_attr(response, "value", {}))
        except Exception as exc:
            raise _bluesky_error(exc) from exc
        return {"display_name": record.get("displayName", record.get("display_name", "")) or "",
                "note": record.get("description") or ""}

    def update_profile(self, changes: dict) -> None:
        client = self._require_client()
        try:
            # Read again before saving; retain avatar, banner, labels and unknown fields.
            response = client.com.atproto.repo.get_record({
                "repo": self.did, "collection": "app.bsky.actor.profile", "rkey": "self",
            })
            value = _attr(response, "value", {})
            record = dict(value) if isinstance(value, dict) else value.model_dump(by_alias=True, exclude_none=True)
            if "display_name" in changes:
                record["displayName"] = changes["display_name"]
            if "note" in changes:
                record["description"] = changes["note"]
            client.com.atproto.repo.put_record({
                "repo": self.did, "collection": "app.bsky.actor.profile", "rkey": "self",
                "record": record, "swap_record": _attr(response, "cid"),
            })
        except Exception as exc:
            raise _bluesky_error(exc) from exc

    def home_timeline(self, *, limit: int = 40, since_id: str = "") -> list[SocialItem]:
        client = self._require_client()
        try:
            resp = client.get_timeline(limit=min(limit, 100))
            feed = list(_attr(resp, "feed", []) or [])
            cursor = _attr(resp, "cursor", None)
            seen_cursors = set()
            while len(feed) < limit and cursor and cursor not in seen_cursors:
                seen_cursors.add(cursor)
                resp = client.get_timeline(limit=min(100, limit - len(feed)), cursor=cursor)
                page = list(_attr(resp, "feed", []) or [])
                if not page:
                    break
                feed.extend(page)
                cursor = _attr(resp, "cursor", None)
        except Exception as exc:  # noqa: BLE001 -- normalized below
            raise _bluesky_error(exc) from exc
        return [
            _feedview_to_item(_as_dict(fv), account_id=self._account_id) for fv in feed
        ]

    def notifications(self, *, limit: int = 40) -> list[SocialItem]:
        return [event.item for event in self.notification_events(limit=limit)
                if event.item is not None]

    def fetch_timeline(self, kind: str, value: str = "", *, limit: int = 40) -> list[SocialItem]:
        client = self._require_client()
        if kind in {"local", "instance"}:
            raise AdapterError("Bluesky does not have instance timelines.", kind="validation")
        if kind == "messages":
            return self._messages(limit=limit)
        if kind not in {"user", "hashtag", "list"} or not value.strip():
            raise AdapterError("Choose a user, hashtag or list.", kind="validation")
        try:
            if kind == "user":
                response = client.app.bsky.feed.get_author_feed({
                    "actor": value.strip().lstrip("@"), "limit": min(limit, 100),
                })
            elif kind == "hashtag":
                tag = value.strip().lstrip("#")
                response = client.app.bsky.feed.search_posts({
                    "q": "#" + tag, "tag": [tag], "sort": "latest", "limit": min(limit, 100),
                })
                return [_post_to_item(_as_dict(post), account_id=self._account_id)
                        for post in _attr(response, "posts", []) or []]
            else:
                response = client.app.bsky.feed.get_list_feed({"list": value, "limit": min(limit, 100)})
            return [_feedview_to_item(_as_dict(post), account_id=self._account_id)
                    for post in _attr(response, "feed", []) or []]
        except Exception as exc:
            raise _bluesky_error(exc) from exc

    def timeline_lists(self) -> list[tuple[str, str]]:
        client = self._require_client()
        result = []
        cursor = None
        seen = set()
        try:
            while True:
                params = {"actor": self.did, "limit": 100, "purposes": ["curatelist"]}
                if cursor:
                    params["cursor"] = cursor
                response = client.app.bsky.graph.get_lists(params)
                for entry in _attr(response, "lists", []) or []:
                    entry = _as_dict(entry)
                    if entry.get("uri") and entry.get("purpose", "").endswith("curatelist"):
                        result.append((entry["uri"], entry.get("name") or entry["uri"]))
                cursor = _attr(response, "cursor")
                if not cursor or cursor in seen:
                    break
                seen.add(cursor)
        except Exception as exc:
            raise _bluesky_error(exc) from exc
        return result

    def search(self, query: str, search_type: str = "", *, limit: int = 40) -> list[SocialItem]:
        query = query.strip()
        if not query or search_type not in {"", "statuses", "accounts", "hashtags"}:
            raise AdapterError("Enter search text and choose a valid result type.", kind="validation")
        client = self._require_client()
        items = []
        try:
            if search_type in {"", "statuses"}:
                response = client.app.bsky.feed.search_posts({"q": query, "limit": min(limit, 100)})
                items.extend(_post_to_item(_as_dict(post), account_id=self._account_id)
                             for post in _attr(response, "posts", []) or [])
            if search_type in {"", "accounts"}:
                response = client.app.bsky.actor.search_actors({"q": query, "limit": min(limit, 100)})
                for profile in _attr(response, "actors", []) or []:
                    profile = _as_dict(profile)
                    handle = _handle(profile)
                    display = profile.get("display_name") or profile.get("displayName") or handle
                    description = profile.get("description", "") or ""
                    did = profile.get("did", "") or handle
                    items.append(SocialItem(
                        network="bluesky", account_id=self._account_id,
                        remote_id=f"search:user:{did}", uri=f"https://bsky.app/profile/{did}",
                        author_id=did, author_handle=handle, author_display=display,
                        text=f"User: {display} {handle}. {description}".strip(),
                    ))
            if search_type in {"", "hashtags"}:
                tag = query.lstrip("#").strip()
                if tag:
                    items.append(SocialItem(
                        network="bluesky", account_id=self._account_id,
                        remote_id=f"search:hashtag:{tag.casefold()}",
                        uri=f"https://bsky.app/hashtag/{quote(tag)}", author_handle=f"#{tag}",
                        author_display="Hashtag", text=f"Hashtag: #{tag}",
                    ))
        except Exception as exc:
            raise _bluesky_error(exc) from exc
        return items

    def _chat(self) -> Any:
        # SDK clones the authenticated client and sets atproto-proxy for bsky.chat.
        return self._require_client().with_bsky_chat_proxy().chat.bsky.convo

    def _message_item(self, message: dict, convo_id: str, members: list) -> SocialItem:
        sender = (message.get("sender") or {}).get("did", "")
        profile = next((_as_dict(member) for member in members if _attr(member, "did") == sender), {})
        return SocialItem(
            network="bluesky", account_id=self._account_id,
            remote_id=f"chat:{convo_id}:{message['id']}",
            author_id=sender, author_handle=_handle(profile) or sender,
            author_display=profile.get("display_name") or profile.get("displayName") or "",
            text=message.get("text", ""), visibility="direct",
            created_at=_to_ms(message.get("sent_at") or message.get("sentAt")),
            thread_root=f"chat:{convo_id}",
        )

    def _messages(self, *, limit: int = 40, convo_id: str = "") -> list[SocialItem]:
        try:
            chat = self._chat()
            if convo_id:
                response = chat.get_convo({"convo_id": convo_id})
                convos = [_attr(response, "convo", {})]
            else:
                response = chat.list_convos({"limit": min(limit, 100)})
                convos = _attr(response, "convos", []) or []
            items = []
            for convo in convos:
                cid = _attr(convo, "id", "")
                if not cid:
                    continue
                response = chat.get_messages({"convo_id": cid, "limit": min(limit, 100)})
                for message in _attr(response, "messages", []) or []:
                    message = _as_dict(message)
                    # Deleted-message views carry no text and must not become posts.
                    if message.get("id") and "text" in message:
                        items.append(self._message_item(message, cid, _attr(convo, "members", []) or []))
            items.sort(key=lambda item: item.created_at, reverse=True)
            return items[:limit]
        except Exception as exc:
            raise _bluesky_error(exc) from exc

    def send_direct_message(self, recipient: str, text: str) -> PublishResult:
        if not recipient.strip() or not text.strip():
            raise AdapterError("A recipient and message are required.", kind="validation")
        try:
            chat = self._chat()
            if recipient.startswith(("chat:", "convo:")):
                convo_id = recipient.split(":")[1]
                chat.get_convo({"convo_id": convo_id})
            else:
                did = recipient.strip().lstrip("@")
                if not did.startswith("did:"):
                    did = _attr(self._require_client().resolve_handle(did), "did", "")
                response = chat.get_convo_for_members({"members": [did]})
                convo_id = _attr(_attr(response, "convo", {}), "id", "")
            if not convo_id:
                raise AdapterError("The conversation could not be found.", kind="validation")
            response = chat.send_message({"convo_id": convo_id, "message": {"text": text}})
            item = self._message_item(_as_dict(response), convo_id, [])
            return PublishResult(remote_id=item.remote_id, item=item)
        except Exception as exc:
            raise _bluesky_error(exc) from exc

    def notification_events(self, *, limit: int = 40) -> list[NotificationEvent]:
        client = self._require_client()
        try:
            resp = client.app.bsky.notification.list_notifications({"limit": min(limit, 100)})
        except Exception as exc:  # noqa: BLE001
            raise _bluesky_error(exc) from exc
        notes = [_as_dict(note) for note in _attr(resp, "notifications", []) or []]
        # Reactions contain a like/repost record, not the post's content.
        subjects = list(dict.fromkeys(
            note.get("reason_subject") for note in notes
            if note.get("reason_subject") and note.get("reason") in {"like", "repost"}
        ))
        posts = {}
        for start in range(0, len(subjects), 25):
            try:
                response = client.get_posts(subjects[start:start + 25])
            except Exception:
                # Deleted/private posts must not discard the notification itself.
                continue
            for post in _attr(response, "posts", []) or []:
                mapped = _as_dict(post)
                posts[mapped.get("uri")] = mapped
        events = []
        for note in notes:
            reason = note.get("reason") or "unknown"
            actor = note.get("author") or {}
            post = note if reason in {"mention", "reply", "quote"} else posts.get(note.get("reason_subject"))
            events.append(NotificationEvent(
                notification_id=note.get("uri") or "", kind=reason,
                actor_name=actor.get("display_name") or "", actor_handle=_handle(actor),
                account_id=self._account_id, created_at=_to_ms(note.get("indexed_at")),
                item=_post_to_item(post, account_id=self._account_id) if post else None,
            ))
        return events

    def thread(self, item: SocialItem) -> list[SocialItem]:
        if item.remote_id.startswith("chat:"):
            return sorted(self._messages(convo_id=item.remote_id.split(":")[1], limit=100),
                          key=lambda message: message.created_at)
        client = self._require_client()
        target = item.remote_id or item.uri
        try:
            resp = client.get_post_thread(target)
        except Exception as exc:  # noqa: BLE001
            raise _bluesky_error(exc) from exc
        node = _as_dict(_attr(resp, "thread", resp))
        collected: dict[str, SocialItem] = {}
        _collect_thread(node, collected, self._account_id)
        items = list(collected.values())
        items.sort(key=lambda it: it.created_at)
        return items

    def publish(self, request: PublishRequest) -> PublishResult:
        if (request.visibility == "direct"
                or (request.in_reply_to or "").startswith("chat:")
                or (request.quote_of or "").startswith("chat:")):
            raise AdapterError("Use the direct message dialog to send a private message.", kind="validation")
        client = self._require_client()
        try:
            if request.poll:
                raise AdapterError("Bluesky does not support polls.", kind="validation")
            if request.visibility != "public":
                raise AdapterError("Bluesky posts support public visibility only.", kind="privacy")
            if any(media.kind != "image" or not media.local_path for media in request.media):
                raise AdapterError("Bluesky publishing currently supports local image attachments only.", kind="validation")
            kwargs = {}
            if request.lang:
                kwargs["langs"] = [request.lang]
            if request.in_reply_to or request.quote_of or request.media:
                from atproto import models

                def reference(uri):
                    response = client.get_posts([uri])
                    post = next((_as_dict(post) for post in _attr(response, "posts", []) or []
                                 if _attr(post, "uri") == uri), None)
                    if not post or not post.get("cid"):
                        raise AdapterError("The referenced post is unavailable.", kind="validation")
                    return models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=post["cid"]), post

                if request.in_reply_to:
                    parent, post = reference(request.in_reply_to)
                    root_data = (post.get("record", {}).get("reply") or {}).get("root")
                    root = models.ComAtprotoRepoStrongRef.Main(**root_data) if root_data else parent
                    kwargs["reply_to"] = models.AppBskyFeedPost.ReplyRef(parent=parent, root=root)
                quote = None
                if request.quote_of:
                    ref, _ = reference(request.quote_of)
                    quote = models.AppBskyEmbedRecord.Main(record=ref)
                images = None
                if request.media:
                    from pathlib import Path
                    if len(request.media) > 4:
                        raise AdapterError("Bluesky supports up to four images per post.", kind="validation")
                    data = [Path(media.local_path).read_bytes() for media in request.media]
                    images = models.AppBskyEmbedImages.Main(images=[
                        models.AppBskyEmbedImages.Image(
                            alt=media.alt_text, image=_attr(client.upload_blob(blob), "blob"))
                        for media, blob in zip(request.media, data, strict=True)])
                if quote and images:
                    kwargs["embed"] = models.AppBskyEmbedRecordWithMedia.Main(record=quote, media=images)
                elif quote or images:
                    kwargs["embed"] = quote or images
            resp = client.send_post(text=request.text, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _bluesky_error(exc) from exc
        uri = _attr(resp, "uri", "") or ""
        item = SocialItem(
            network="bluesky",
            account_id=self._account_id,
            remote_id=uri,
            uri=uri,
            author_id=self.did,
            text=request.text,
            visibility="public",
        )
        return PublishResult(remote_id=uri, uri=uri, item=item)

    def _cid_for(self, uri: str) -> str:
        getter = getattr(self._client, "get_posts", None)
        if getter is None:
            return ""
        try:
            resp = getter([uri])
        except Exception:  # noqa: BLE001 -- best-effort cid lookup
            return ""
        for post in _attr(resp, "posts", []) or []:
            pd = _as_dict(post)
            if pd.get("uri") == uri:
                return pd.get("cid", "") or ""
        return ""

    def _interact(self, on_method: str, off_method: str, remote_id: str, on: bool) -> None:
        if remote_id.startswith("chat:"):
            raise AdapterError("Direct messages cannot be liked or reposted.", kind="validation")
        client = self._require_client()
        try:
            if on:
                getattr(client, on_method)(remote_id, self._cid_for(remote_id))
            else:
                response = client.get_posts([remote_id])
                post = next((_as_dict(post) for post in _attr(response, "posts", []) or []
                             if _attr(post, "uri") == remote_id), None)
                if post is None:
                    raise AdapterError("The post could not be found.", kind="validation")
                record_uri = (post.get("viewer") or {}).get(on_method)
                if record_uri:
                    result = getattr(client, off_method)(record_uri)
                    if result is False:
                        raise AdapterError("The interaction could not be removed.")
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _bluesky_error(exc) from exc

    def set_favourite(self, remote_id: str, on: bool = True) -> None:
        self._interact("like", "unlike", remote_id, on)

    def set_reblog(self, remote_id: str, on: bool = True) -> None:
        self._interact("repost", "unrepost", remote_id, on)

    @classmethod
    def available(cls) -> bool:
        try:
            import atproto  # noqa: F401
        except ImportError:
            return False
        return True


def _collect_thread(node: dict, out: dict, account_id: str) -> None:
    """Flatten a ThreadViewPost tree (parents + replies) into ``out`` by uri."""
    if not isinstance(node, dict):
        return
    post = node.get("post")
    if isinstance(post, dict):
        item = _post_to_item(post, account_id=account_id)
        if item.remote_id:
            out.setdefault(item.remote_id, item)
    parent = node.get("parent")
    if isinstance(parent, dict):
        _collect_thread(parent, out, account_id)
    for reply in node.get("replies") or []:
        if isinstance(reply, dict):
            _collect_thread(reply, out, account_id)
