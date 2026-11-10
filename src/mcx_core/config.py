"""
config.py - the adapter project (.mcx, YAML). No Qt.

One file holds everything an adapter serves: sources (connection settings, discovered objects,
mapped items), tools (generic and curated, draft + published snapshot, version history), tool
profiles, the MCP endpoint, authentication, safety limits, audit and licence settings.

Rules
- Every field has a default; `from_dict` accepts older / partial files (hand-written YAML too).
- Secrets: API keys are stored only as SHA-256 hashes (+ last 4 characters to recognise them);
  source passwords may be given as ${ENV_VAR} so they stay out of the file.
- Tools are served from their *published snapshot*; editing a tool changes only the draft until
  Publish (`Project.publish`), which bumps the version and logs it in the tool's history.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import yaml

PROJECT_EXT = ".mcx"
FILE_VERSION = 1
RISKS = ("R0", "R1", "R2", "R3", "R4", "R5")
GENERIC_TOOLS = ("browse_model", "list_assets", "read_tag", "query_history")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")      # MCP tool names: lower snake case


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _build(cls, d: dict | None):
    """Dataclass from a dict: unknown keys ignored, missing keys default."""
    d = d or {}
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


# ======================================================================= pieces
@dataclass
class Limits:
    max_rows: int = 500              # rows per answer (more -> truncated: true)
    max_days: int = 31               # date range per history / curated call
    timeout_s: float = 10.0          # per source call
    rate_per_min: int = 60           # calls per minute per client
    cache_s: float = 5.0             # short local cache for live reads


@dataclass
class Endpoint:
    host: str = "127.0.0.1"
    port: int = 8811
    path: str = "/mcp"
    http: bool = True                # Streamable HTTP
    stdio: bool = True               # `python -m mcx_core stdio project.mcx`
    tls_cert: str = ""
    tls_key: str = ""
    public_url: str = ""             # how clients reach it (behind a proxy / gateway), shown in Studio
    allowed_origins: list = field(default_factory=list)   # browser Origins allowed (DNS-rebinding guard)

    def base_url(self) -> str:
        if self.public_url:
            return self.public_url.rstrip("/")
        scheme = "https" if self.tls_cert else "http"
        return f"{scheme}://{self.host}:{self.port}{self.path}"


@dataclass
class ApiKey:
    id: str
    name: str = ""
    profile: str = ""               # the profile this key may use ("" = any)
    hash: str = ""                  # sha256 of the secret
    last4: str = ""
    created: str = ""
    client: str = ""                # platform shown in Studio (Copilot Studio, ContextForge ...)


@dataclass
class Auth:
    mode: str = "api_key"           # none (local only) | api_key | oauth (planned)
    api_keys: list = field(default_factory=list)
    oauth_issuer: str = ""
    oauth_audience: str = ""

    @classmethod
    def from_dict(cls, d):
        a = _build(cls, d)
        a.api_keys = [k if isinstance(k, ApiKey) else _build(ApiKey, k) for k in (a.api_keys or [])]
        return a


@dataclass
class Profile:
    id: str
    title: str = ""
    description: str = ""
    max_risk: str = "R3"
    scope: str = ""                 # ISA-95 prefix the profile may see ("" = all)


@dataclass
class Column:
    name: str
    type: str = "string"
    pii: bool = False               # personal data: never returned


@dataclass
class SourceObject:
    """A discovered table / view / sheet / topic. New objects start hidden (allow-list)."""
    name: str
    kind: str = "table"             # table | view | sheet | file | topic | endpoint
    columns: list = field(default_factory=list)
    exposed: bool = False
    blocked: bool = False           # never expose (rule), e.g. sys_* audit tables
    risk: str = "R1"
    note: str = ""

    @classmethod
    def from_dict(cls, d):
        o = _build(cls, d)
        o.columns = [c if isinstance(c, Column) else _build(Column, c) for c in (o.columns or [])]
        return o

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)

    def pii_columns(self) -> set[str]:
        return {c.name for c in self.columns if c.pii}


@dataclass
class Item:
    """One live data point mapped to the ISA-95 model (OPC UA node, register, topic field ...)."""
    address: str
    path: str = ""                  # ISA-95 equipment path: Site/Area/Line/Machine
    name: str = ""                  # friendly name at that path: temperature, kw ...
    unit: str = ""
    lo: float | None = None         # engineering limits (shown to the AI as context)
    hi: float | None = None
    datatype: str = "float"
    description: str = ""
    exposed: bool = True

    @property
    def full_path(self) -> str:
        return f"{self.path.strip('/')}/{self.name}" if self.path else self.name


@dataclass
class SourceCfg:
    id: str
    name: str = ""
    type: str = "sim"
    enabled: bool = True
    settings: dict = field(default_factory=dict)
    objects: list = field(default_factory=list)
    items: list = field(default_factory=list)
    isa95: str = ""                 # default ISA-95 context of this source (Bangkok/Assembly)
    description: str = ""
    discovered: str = ""            # time of the last discovery

    @classmethod
    def from_dict(cls, d):
        s = _build(cls, d)
        s.settings = dict(s.settings or {})
        s.objects = [o if isinstance(o, SourceObject) else SourceObject.from_dict(o) for o in (s.objects or [])]
        s.items = [i if isinstance(i, Item) else _build(Item, i) for i in (s.items or [])]
        return s

    def object(self, name: str) -> SourceObject | None:
        return next((o for o in self.objects if o.name.lower() == name.lower()), None)

    def resolved_settings(self) -> dict:
        """Settings with ${ENV} references replaced (secrets kept out of the file)."""
        out = {}
        for k, v in self.settings.items():
            out[k] = os.path.expandvars(v) if isinstance(v, str) else v
        return out


@dataclass
class ToolParam:
    name: str
    type: str = "string"            # string | number | integer | boolean | date | datetime | enum
    description: str = ""
    required: bool = False
    column: str = ""                # filter: column compared with the value
    op: str = "="                   # = | >= | <= | > | < | in | contains (text, LIKE)
    values: list = field(default_factory=list)       # enum choices
    default: object = None
    group: dict = field(default_factory=dict)        # group_by param: choice -> [columns]


@dataclass
class Measure:
    column: str
    agg: str = "avg"                # avg | min | max | sum | count | none
    unit: str = ""
    label: str = ""                 # output name (default: the column)

    @property
    def out(self) -> str:
        return self.label or self.column


@dataclass
class ToolDef:
    name: str
    kind: str = "curated"           # generic | curated
    title: str = ""
    description: str = ""
    source: str = ""                # curated: source id
    object: str = ""                # curated: table / view
    risk: str = "R1"
    params: list = field(default_factory=list)
    measures: list = field(default_factory=list)     # aggregated outputs (with group_by)
    columns: list = field(default_factory=list)      # plain columns returned without measures
    order_by: str = ""
    date_column: str = ""           # range checked against Limits.max_days
    isa95: str = ""                 # context path put in every answer
    profiles: list = field(default_factory=list)
    enabled: bool = True
    sensitive_ok: bool = False      # R2: an admin enabled it for this tool
    version: int = 0
    published: dict | None = None   # snapshot served by MCP (None = never published)
    history: list = field(default_factory=list)      # [{version, time, user, note}]

    DRAFT_KEYS = ("title", "description", "source", "object", "risk", "params", "measures", "columns",
                  "order_by", "date_column", "isa95", "profiles", "enabled", "sensitive_ok")

    @classmethod
    def from_dict(cls, d):
        t = _build(cls, d)
        t.params = [p if isinstance(p, ToolParam) else _build(ToolParam, p) for p in (t.params or [])]
        t.measures = [m if isinstance(m, Measure) else _build(Measure, m) for m in (t.measures or [])]
        t.profiles = list(t.profiles or [])
        t.columns = list(t.columns or [])
        return t

    def draft(self) -> dict:
        d = asdict(self)
        return {k: d[k] for k in self.DRAFT_KEYS}

    def live(self) -> "ToolDef | None":
        """The published version (what MCP clients see), or None."""
        if not self.published:
            return None
        t = ToolDef.from_dict({"name": self.name, "kind": self.kind, **copy.deepcopy(self.published)})
        t.version, t.published = self.version, self.published
        return t

    @property
    def changed(self) -> bool:
        return self.published is None or self.draft() != self.published

    @property
    def state(self) -> str:
        if self.published is None:
            return "draft"
        return "changed" if self.changed else "published"


@dataclass
class Licence:
    edition: str = "Starter"        # Starter | Standard | Plant
    holder: str = ""
    max_sources: int = 1
    max_risk: str = "R1"
    profiles: bool = False          # tool profiles allowed
    expires: str = ""
    machine: str = ""

    EDITIONS = {"Starter": (1, "R1", False), "Standard": (5, "R3", True), "Plant": (0, "R3", True)}

    def set_edition(self, ed: str):
        self.edition = ed
        self.max_sources, self.max_risk, self.profiles = self.EDITIONS.get(ed, self.EDITIONS["Starter"])


@dataclass
class AuditCfg:
    enabled: bool = True
    syslog_host: str = ""           # forward every record (RFC 5424 over UDP) to a SIEM
    syslog_port: int = 514


# ======================================================================= project
@dataclass
class Project:
    name: str = "New adapter"
    site: str = ""
    timezone: str = "UTC"
    description: str = ""
    version: int = FILE_VERSION
    endpoint: Endpoint = field(default_factory=Endpoint)
    auth: Auth = field(default_factory=Auth)
    limits: Limits = field(default_factory=Limits)
    licence: Licence = field(default_factory=Licence)
    audit: AuditCfg = field(default_factory=AuditCfg)
    sources: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    profiles: list = field(default_factory=list)

    # ---- file
    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        d = dict(d or {})
        p = _build(cls, {k: v for k, v in d.items() if k in ("name", "site", "timezone", "description", "version")})
        p.endpoint = _build(Endpoint, d.get("endpoint"))
        p.auth = Auth.from_dict(d.get("auth"))
        p.limits = _build(Limits, d.get("limits"))
        p.licence = _build(Licence, d.get("licence"))
        p.audit = _build(AuditCfg, d.get("audit"))
        p.sources = [SourceCfg.from_dict(s) for s in d.get("sources") or []]
        p.tools = [ToolDef.from_dict(t) for t in d.get("tools") or []]
        p.profiles = [_build(Profile, x) for x in d.get("profiles") or []]
        p.version = FILE_VERSION
        p.ensure_generic_tools()
        return p

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Project":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f) or {})

    def save(self, path: str | os.PathLike):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"# {self.name} - MCX Adapter project (YAML). Edit in MCX Studio or by hand; "
                    "the service reloads it on change.\n")
            yaml.safe_dump(self.to_dict(), f, sort_keys=False, allow_unicode=True, width=110)
        os.replace(tmp, path)

    @classmethod
    def new(cls, name: str = "New adapter") -> "Project":
        p = cls(name=name)
        p.profiles = [Profile("all", "All tools", "Every published tool", "R3")]
        p.ensure_generic_tools()
        return p

    # ---- lookups
    def source(self, sid: str) -> SourceCfg | None:
        return next((s for s in self.sources if s.id == sid), None)

    def tool(self, name: str) -> ToolDef | None:
        return next((t for t in self.tools if t.name == name), None)

    def profile(self, pid: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == pid), None)

    def unique_id(self, base: str, taken) -> str:
        base = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_") or "item"
        n, out = 1, base
        while out in taken:
            n += 1
            out = f"{base}_{n}"
        return out

    def ensure_generic_tools(self):
        """The four generic tools always exist (they read the ISA-95 model of every tag source)."""
        from mcx_core.tools import generic_tool_def        # late: tools imports config
        have = {t.name for t in self.tools}
        for i, name in enumerate(GENERIC_TOOLS):
            if name not in have:
                self.tools.insert(i, generic_tool_def(name, [p.id for p in self.profiles]))

    # ---- publish
    def pending(self) -> list[ToolDef]:
        return [t for t in self.tools if t.changed]

    def publish(self, names=None, user: str = "", note: str = "", errors: list | None = None) -> list[str]:
        """Snapshot the drafts of `names` (default: every changed tool). Returns the published names.
        A tool that breaks a rule raises SafetyError - or, with `errors` given, is held back and
        reported there as (name, reason) while the others publish."""
        from mcx_core.safety import SafetyError, check_publishable
        done = []
        for t in self.tools:
            if (names is not None and t.name not in names) or not t.changed:
                continue
            try:
                check_publishable(t, self)
            except SafetyError as e:
                if errors is None:
                    raise
                errors.append((t.name, str(e)))
                continue
            t.version += 1
            t.published = copy.deepcopy(t.draft())
            t.history.append({"version": t.version, "time": _now(), "user": user, "note": note})
            done.append(t.name)
        return done

    def unpublish(self, name: str, user: str = ""):
        t = self.tool(name)
        if t and t.published is not None:
            t.published = None
            t.history.append({"version": t.version, "time": _now(), "user": user, "note": "hidden"})

    # ---- API keys
    def new_api_key(self, name: str, profile: str, client: str = "") -> tuple[ApiKey, str]:
        """Create a key; the secret is returned once and only its hash is kept."""
        secret = "mcx_" + secrets.token_urlsafe(24)
        k = ApiKey(id=self.unique_id(name or "key", {x.id for x in self.auth.api_keys}), name=name,
                   profile=profile, hash=hash_secret(secret), last4=secret[-4:], created=_now(), client=client)
        self.auth.api_keys.append(k)
        return k, secret

    def rotate_api_key(self, kid: str) -> str:
        k = next(x for x in self.auth.api_keys if x.id == kid)
        secret = "mcx_" + secrets.token_urlsafe(24)
        k.hash, k.last4, k.created = hash_secret(secret), secret[-4:], _now()
        return secret


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
