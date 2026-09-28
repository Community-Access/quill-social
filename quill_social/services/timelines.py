"""Saved account timelines and their separate cached membership."""

from dataclasses import asdict, dataclass

from quill_social.model import new_id

TIMELINE_KINDS = {
    "messages": "Direct messages", "list": "List", "user": "User",
    "hashtag": "Hashtag", "local": "Local timeline", "instance": "Instance",
    "search": "Search",
}


@dataclass
class TimelineSpec:
    scope: str
    account_id: str
    kind: str
    value: str = ""
    label: str = ""
    announce: bool = False
    search_type: str = ""


class TimelineLibrary:
    def __init__(self, store):
        self.store = store

    def create(self, account_id, kind, value="", label="", announce=False, search_type=""):
        if kind not in TIMELINE_KINDS:
            raise ValueError("Unsupported timeline type.")
        if not self.store.get_account(account_id):
            raise ValueError("Select an existing account.")
        value = value.strip()
        if kind == "hashtag":
            value = value.lstrip("#")
        if kind in {"list", "user", "hashtag", "instance", "search"} and not value:
            raise ValueError("Enter a value for this timeline.")
        if kind != "search":
            search_type = ""
        elif search_type not in {"", "statuses", "accounts", "hashtags"}:
            raise ValueError("Choose a valid search type.")
        for spec in self.list(account_id):
            if (spec.kind, spec.value, spec.search_type) == (kind, value, search_type):
                return spec
        spec = TimelineSpec("timeline:" + new_id("view"), account_id, kind, value,
                            label.strip() or f"{TIMELINE_KINDS[kind]} {value}".strip(),
                            bool(announce), search_type)
        self.store.put_document("timeline", spec.scope, asdict(spec))
        return spec

    def get(self, scope):
        data = self.store.get_document("timeline", scope)
        return TimelineSpec(**data) if data else None

    def list(self, account_id=None):
        return [TimelineSpec(**data) for data in self.store.list_documents("timeline")
                if (account_id is None or data["account_id"] == account_id)
                and self.store.get_account(data["account_id"])]

    def remove(self, scope):
        self.store.delete_document("timeline", scope)
        self.store.delete_document("timeline-cache", scope)

    def store_loaded(self, scope, items):
        spec = self.get(scope)
        if spec is None:
            raise ValueError("This timeline no longer exists.")
        items = list(items)
        if any(item.account_id != spec.account_id for item in items):
            raise ValueError("Timeline items belong to a different account.")
        ids = []
        loaded = []
        for item in items:
            existing = self.store.conn.execute(
                "SELECT item_id FROM items WHERE network=? AND account_id=? AND remote_id=?",
                (item.network, item.account_id, item.remote_id),
            ).fetchone() if item.remote_id else self.store.get_item(item.item_id)
            item = self.store.upsert_item(item)
            if existing is None:
                self.store.put_document("timeline-only", item.item_id, {})
            if item.item_id not in ids:
                ids.append(item.item_id)
                loaded.append(item)
        self.store.put_document("timeline-cache", scope, {"ids": ids})
        return loaded

    def items(self, scope):
        data = self.store.get_document("timeline-cache", scope) or {}
        return [item for item_id in data.get("ids", [])
                if (item := self.store.get_item(item_id)) is not None]
