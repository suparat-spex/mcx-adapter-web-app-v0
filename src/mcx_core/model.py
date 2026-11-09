"""
model.py - the ISA-95 model the generic tools read. No Qt.

Every exposed item of every enabled tag source has an equipment path (Site/Area/Line/Machine) and
a friendly name; together they form one tree:

    Bangkok
      Assembly
        L2
          Press1            <- equipment
            temperature     <- item (source, address, unit, limits)

Paths are case-insensitive for lookup, kept as written for display. `suggest_path` turns a raw
tag name into a first guess (OPC UA browse path, MQTT topic, register name).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVELS = ("site", "area", "line", "machine", "unit")


@dataclass
class Node:
    name: str
    path: str
    children: dict = field(default_factory=dict)       # lower name -> Node
    items: list = field(default_factory=list)          # [(source id, Item)]

    @property
    def level(self) -> str:
        depth = self.path.count("/")
        return LEVELS[min(depth, len(LEVELS) - 1)] if self.path else "root"


class Model:
    def __init__(self):
        self.root = Node("", "")
        self.by_path: dict[str, tuple[str, object]] = {}   # lower full path -> (source id, Item)

    @classmethod
    def build(cls, project) -> "Model":
        m = cls()
        for s in project.sources:
            if not s.enabled:
                continue
            for it in s.items:
                if it.exposed and it.name:
                    m.add(s.id, it)
        return m

    def add(self, sid: str, item):
        node = self.root
        parts = [p for p in item.path.split("/") if p]
        for i, p in enumerate(parts):
            node = node.children.setdefault(p.lower(), Node(p, "/".join(parts[:i + 1])))
        node.items.append((sid, item))
        self.by_path[item.full_path.lower()] = (sid, item)

    def node(self, path: str) -> Node | None:
        node = self.root
        for p in [x for x in (path or "").split("/") if x]:
            node = node.children.get(p.lower())
            if node is None:
                return None
        return node

    def item(self, path: str):
        """(source id, Item) for a full item path, or None."""
        return self.by_path.get((path or "").strip("/").lower())

    def walk(self, node: Node | None = None):
        node = node or self.root
        yield node
        for c in node.children.values():
            yield from self.walk(c)

    def equipment(self, scope: str = "") -> list[Node]:
        """Nodes under `scope` that hold items (machines / units)."""
        start = self.node(scope)
        return [n for n in self.walk(start) if n.items] if start else []

    def search(self, text: str, limit: int = 50) -> list[str]:
        t = text.lower()
        return [p for p, (_, it) in self.by_path.items()
                if t in p or t in it.description.lower()][:limit]

    def __len__(self):
        return len(self.by_path)


def in_scope(path: str, scope: str) -> bool:
    """A profile scope ("Bangkok/Assembly") allows that path and everything under it."""
    if not scope:
        return True
    p, s = path.strip("/").lower(), scope.strip("/").lower()
    return p == s or p.startswith(s + "/")


_SPLIT = re.compile(r"[/.\\:|]+")


def suggest_path(raw: str, prefix: str = "") -> tuple[str, str]:
    """('Site/Area/Line/Machine', 'name') guess from a raw tag / topic / node name."""
    parts = [p for p in _SPLIT.split(raw) if p and not p.lower().startswith(("ns=", "objects"))]
    if not parts:
        return prefix, raw
    name = re.sub(r"[^A-Za-z0-9_]+", "_", parts[-1]).strip("_").lower() or "value"
    path = "/".join([prefix.strip("/")] + parts[:-1] if prefix else parts[:-1]).strip("/")
    return path, name
