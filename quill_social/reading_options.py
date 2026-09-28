"""Account-specific presentation and automatic announcement policy."""
import re
import unicodedata

WARNING_MODES = ("text_only", "warning_then_text", "warning_only")
SPEECH_SCOPES = ("home:all", "attention:mentions", "attention:notifications", "attention:messages")


def sanitize_account_options(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for account, options in value.items():
        if not isinstance(account, str) or not isinstance(options, dict):
            continue
        mode = options.get("content_warning_mode", "warning_then_text")
        result[account] = {
            "content_warning_mode": mode if mode in WARNING_MODES else "warning_then_text",
            "speech_muted": options.get("speech_muted") is True,
            "sync_home_position": options.get("sync_home_position") is True,
            "speech_timelines": [scope for scope in SPEECH_SCOPES
                                 if scope in options.get("speech_timelines", [])]
            if isinstance(options.get("speech_timelines", []), list) else [],
        }
    return result


def plain_characters(text):
    # Explicit opt-in matching the recovered ASCII display filter. Stored text is intact.
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _post_text(item, settings, account_id):
    if item is None:
        return ""
    options = settings.account_options.get(account_id, {})
    mode = options.get("content_warning_mode", "warning_then_text")
    text = " ".join(item.text.split())
    if settings.condense_mentions:
        leading = re.match(r"^((?:@\S+\s+){2,})(.*)$", text)
        if leading:
            mentions = leading.group(1).split()
            count = len(mentions) - 1
            more = "one more" if count == 1 else f"{count} more"
            text = f"{mentions[0]} and {more}. {leading.group(2)}".strip()
    if item.content_warning:
        warning = " ".join(item.content_warning.split())
        if mode == "warning_only":
            text = f"Content warning: {warning}"
        elif mode == "warning_then_text":
            text = f"Content warning: {warning.rstrip('.')}. {text}".strip()
    if settings.exclude_web_addresses:
        text = re.sub(r"\b(?:https?://|www\.)[^\s<>'\"]+", "", text, flags=re.I)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return " ".join(text.split())


def _first_sentence(text):
    text = re.split(r"(?<=[.!?])[\"')\]\u201d\u2019]*\s+", text, maxsplit=1)[0]
    if len(text) > 320:
        text = text[:320].rstrip()
        if " " in text:
            text = text.rsplit(" ", 1)[0]
        text += "..."
    return text


def automatic_text(item, settings, *, scope="home:all", account_label="",
                   account_count=1, active_account_context=False, notification=None, timeline_label=""):
    """Format incoming speech without changing posts or their stored presentation.

    ``notification`` is an adapter NotificationEvent; follow events may have no
    item. The caller supplies foreground/account context, keeping this formatter
    independent of native focus and the screen reader output backend.
    """
    account_id = (getattr(notification, "account_id", "")
                  or getattr(item, "account_id", ""))
    text = _post_text(item, settings, account_id)
    author = ((item.author_display or item.author_handle) if item else "") or "Unknown"
    prefix = f"{account_label}. " if account_count > 1 and account_label and not active_account_context else ""
    if scope == "attention:notifications":
        notification_heading = ""
        if notification is not None:
            actor = notification.actor_name or notification.actor_handle or "Unknown"
            kind = notification.kind
            action = {
                "favourite": "favourited", "like": "liked", "reblog": "boosted",
                "repost": "reposted", "follow": "followed you",
                "follow_request": "requested to follow you",
                "status": "posted", "update": "edited a post", "quote": "quoted",
                "quoted_update": "edited a post you quoted",
                "moderation_warning": "sent a moderation warning to",
                "severed_relationships": "reported a severed relationship with",
            }.get(kind, kind.replace("_", " ") or "notified")
            if kind in {"follow", "follow_request"}:
                author, text = actor, action
            elif kind == "poll":
                notification_heading = f"{prefix}New notification. A poll from {actor} has ended"
            elif kind == "quoted_update":
                author, text = actor, f"{action}: {text}".rstrip(": ")
            elif kind in {"mention", "reply"}:
                author = actor
            else:
                author = f"{actor} {action} {author if item else 'you'}"
        if settings.notification_first_sentence:
            text = _first_sentence(text)
        heading = notification_heading or f"{prefix}New notification from {author}"
    else:
        if item and item.reblog_by:
            author = f"{item.reblog_by} boosted {author}"
        label = ("direct message" if scope == "attention:messages" else
                 "mention" if scope == "attention:mentions" else
                 f"post in {timeline_label}" if timeline_label else "home post")
        heading = f"from {author}" if active_account_context else f"{prefix}New {label} from {author}"
    result = f"{heading}: {text}" if text else heading
    return plain_characters(result) if settings.remove_unicode else result
