"""
docs.py - PDF and Word (.docx) documents (folder or single file) as read-only tables. No Qt.

Each document is one object of text passages:  page | section | para | text
  page     PDF page number; for Word the page as last laid out by Word (rendered page breaks)
  section  the nearest heading above the passage (Word Heading / Title styles; empty for PDF)
  para     running passage number in the document
  text     the passage (a paragraph, long ones split at max_chars)
Each Word table is one more object (<document>.table<n>) with its first row as the header.

Like CSV / Excel, a query loads the object into an in-memory SQLite table and runs the same
structured Query - use a "contains" filter on text to search. Word is read with the standard
library (zip + XML, never macros or embedded objects); PDF needs pypdf. Legacy .doc is not supported.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from mcx_core.adapters.base import Adapter, AdapterError, DiscoverResult, F, Query
from mcx_core.adapters.files import _guess, _Mem, _num
from mcx_core.adapters.sql import classify
from mcx_core.config import Column, SourceObject

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SUFFIXES = (".pdf", ".docx")
TEXT_HEADER = ["page", "section", "para", "text"]
TEXT_COLUMNS = [Column("page", "integer"), Column("section", "string"), Column("para", "integer"),
                Column("text", "string")]
MAX_DOCX_XML = 64 * 1024 * 1024          # uncompressed document.xml cap (zip bombs)


def _split(text: str, max_chars: int) -> list[str]:
    """One passage -> pieces of at most max_chars, cut at sentence / word ends where possible."""
    text = re.sub(r"\s+", " ", text).strip()
    out = []
    while len(text) > max_chars:
        cut = max(text.rfind(". ", 0, max_chars), text.rfind(" ", 0, max_chars))
        cut = cut + 1 if cut > max_chars // 2 else max_chars
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def _docx(path: Path, max_chars: int) -> tuple[list, list[list]]:
    """(passages [[page, section, para, text]], tables [[[cell, ...], ...], ...]) of one .docx."""
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML:
                raise AdapterError(f"{path.name}: document too large")
            root = ET.fromstring(z.read(info))
            styles = {}
            if "word/styles.xml" in z.namelist():
                for s in ET.fromstring(z.read("word/styles.xml")).iter(W + "style"):
                    n = s.find(W + "name")
                    styles[s.get(W + "styleId")] = n.get(W + "val") if n is not None else ""
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise AdapterError(f"{path.name}: not a valid Word .docx file") from None

    def text_of(el) -> str:
        parts = []
        for t in el.iter():
            if t.tag == W + "t":
                parts.append(t.text or "")
            elif t.tag == W + "tab":
                parts.append("\t")
            elif t.tag in (W + "br", W + "cr") and t.get(W + "type") != "page":
                parts.append("\n")
        return "".join(parts)

    body = root.find(W + "body")
    if body is None:
        return [], []
    passages, tables = [], []
    page, section, n = 1, "", 0
    for el in body:
        if el.tag == W + "tbl":
            rows = []
            for tr in el.iter(W + "tr"):
                rows.append([re.sub(r"\s+", " ", text_of(tc)).strip() for tc in tr.findall(W + "tc")])
            if rows:
                tables.append(rows)
            continue
        if el.tag != W + "p":
            continue
        breaks = sum(1 for b in el.iter() if b.tag == W + "lastRenderedPageBreak"
                     or (b.tag == W + "br" and b.get(W + "type") == "page"))
        page += breaks
        txt = text_of(el).strip()
        if not txt:
            continue
        sid = el.find(f"{W}pPr/{W}pStyle")
        style = styles.get(sid.get(W + "val"), sid.get(W + "val")) if sid is not None else ""
        if re.match(r"(?i)(heading|title)", style or ""):
            section = re.sub(r"\s+", " ", txt)[:200]
        for piece in _split(txt, max_chars):
            n += 1
            passages.append([page, section, n, piece])
    return passages, tables


def _pdf(path: Path, max_chars: int) -> list:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise AdapterError("PDF documents need pypdf (pip install pypdf)") from None
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise AdapterError(f"{path.name}: password-protected PDF")
        passages, n = [], 0
        for i, pg in enumerate(reader.pages, 1):
            text = pg.extract_text() or ""
            blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
            if len(blocks) <= 1:                      # no blank lines: group lines into passages
                blocks, cur = [], ""
                for line in text.splitlines():
                    cur = f"{cur} {line}".strip()
                    if len(cur) >= max_chars // 2 or line.rstrip().endswith((".", ":")) and len(cur) > 80:
                        blocks.append(cur)
                        cur = ""
                if cur:
                    blocks.append(cur)
            for b in blocks:
                for piece in _split(b, max_chars):
                    n += 1
                    passages.append([i, "", n, piece])
        return passages
    except PermissionError:
        raise AdapterError(f"{path.name}: file locked") from None
    except PdfReadError as e:
        raise AdapterError(f"{path.name}: not a readable PDF ({e})") from None


class DocsAdapter(Adapter):
    TYPE = "docs"
    LABEL = "PDF / Word documents"
    FAMILY = "table"
    CATEGORY = "Files and APIs"
    ICON = "file-text"
    SCHEMA = [
        F("path", "Folder or file", "str", "", tip="A folder (every .pdf / .docx in it) or one document"),
        F("max_chars", "Max characters per passage", "int", 1500, (200, 10000),
          tip="Long paragraphs are split into passages of at most this length"),
    ]

    def __init__(self, cfg, limits=None):
        super().__init__(cfg, limits)
        self._cache: dict[str, tuple[float, dict]] = {}

    def _files(self) -> list[Path]:
        p = Path(self.setting("path") or "")
        if not str(p) or not p.exists():
            raise AdapterError(f"not found: {p}")
        if p.is_file():
            if p.suffix.lower() == ".doc":
                raise AdapterError(f"{p.name}: legacy .doc is not supported - save it as .docx")
            if p.suffix.lower() not in SUFFIXES:
                raise AdapterError(f"{p.name}: not a PDF or Word .docx document")
            return [p]
        return sorted(f for f in p.iterdir() if f.suffix.lower() in SUFFIXES and not f.name.startswith("~$"))

    def ping(self):
        files = self._files()
        if not files:
            raise AdapterError("no .pdf / .docx documents in the folder")
        for f in files:
            try:
                with open(f, "rb") as fh:
                    fh.read(1)
            except PermissionError:
                raise AdapterError(f"{f.name}: file locked") from None

    def _load(self, f: Path) -> dict[str, tuple[list, list]]:
        """{object name: (header, rows)} for one document."""
        try:
            mc = max(200, min(10000, int(self.setting("max_chars"))))
        except (TypeError, ValueError):
            mc = 1500
        if f.suffix.lower() == ".pdf":
            return {f.stem: (TEXT_HEADER, _pdf(f, mc))}
        passages, tables = _docx(f, mc)
        out = {f.stem: (TEXT_HEADER, passages)}
        for i, rows in enumerate(tables, 1):
            width = max(len(r) for r in rows)
            header, seen = [], set()
            for j in range(width):
                h = (rows[0][j] if j < len(rows[0]) else "") or f"col{j + 1}"
                while h.lower() in seen:
                    h += "_"
                seen.add(h.lower())
                header.append(h)
            out[f"{f.stem}.table{i}"] = (header, [[_num(v) if v != "" else None for v in r] for r in rows[1:]])
        return out

    def _all(self) -> dict[str, tuple[list, list]]:
        out = {}
        for f in self._files():
            m = f.stat().st_mtime
            hit = self._cache.get(str(f))
            if hit is None or hit[0] != m:
                hit = (m, self._load(f))
                self._cache[str(f)] = hit
            out.update(hit[1])
        return out

    def discover(self) -> DiscoverResult:
        objs, nd = [], 0
        for name, (header, rows) in self._all().items():
            if header is TEXT_HEADER:
                nd += 1
                o = SourceObject(name, "document", [Column(c.name, c.type) for c in TEXT_COLUMNS])
            else:
                o = SourceObject(name, "table",
                                 [Column(h, _guess([r[i] if i < len(r) else None for r in rows]))
                                  for i, h in enumerate(header)])
            classify(o)
            if header is TEXT_HEADER and not o.note:
                o.note = f"{len(rows)} passages"
            objs.append(o)
        nt = len(objs) - nd
        return DiscoverResult(objects=objs, summary=f"{nd} documents" + (f", {nt} tables" if nt else ""))

    def select(self, obj, q: Query, timeout_s: float = 10.0):
        data = self._all().get(obj.name)
        if data is None:
            raise AdapterError(f"{obj.name} not found (document renamed or removed?)")
        return _Mem().run(obj, *data, q, timeout_s)
