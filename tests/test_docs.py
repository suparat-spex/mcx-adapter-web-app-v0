"""PDF / Word document sources: discovery, passages, Word tables, contains search, a curated tool end to end."""
import zipfile

import pytest

from mcx_core.adapters import Query, make_adapter
from mcx_core.adapters.base import AdapterError
from mcx_core.config import SourceCfg
from mcx_core.tools import new_curated

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text, style=""):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"


def make_docx(path):
    rows = [["Machine", "Torque Nm"], ["Press1", "120"], ["Press2", "95.5"]]
    tbl = "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in r) + "</w:tr>"
                              for r in rows) + "</w:tbl>"
    body = (_p("Press maintenance", "Heading1") + _p("Check the hydraulic oil level every shift.")
            + '<w:p><w:r><w:br w:type="page"/><w:t>Replace the filter after 500 h (100% load_test).</w:t></w:r></w:p>'
            + _p("Safety", "Heading1") + _p("Lock out the press before opening the guard.") + tbl)
    styles = (f'<w:styles {NS}><w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {NS}><w:body>{body}</w:body></w:document>')
        z.writestr("word/styles.xml", styles)


def make_pdf(path, pages):
    """A minimal valid PDF, one text line per page (Helvetica)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


@pytest.fixture()
def docs_dir(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    make_docx(d / "press_manual.docx")
    make_pdf(d / "sop_molding.pdf", ["Preheat the mould to 80 C.", "Purge the barrel before a colour change."])
    (d / "notes.txt").write_text("ignored")
    return d


def test_docs_discover_and_search(docs_dir):
    pytest.importorskip("pypdf")
    a = make_adapter(SourceCfg("d", "Docs", "docs", settings={"path": str(docs_dir)}))
    a.ping()
    r = a.discover()
    by = {o.name: o for o in r.objects}
    assert set(by) == {"press_manual", "press_manual.table1", "sop_molding"} and "2 documents" in r.summary
    assert by["press_manual"].kind == "document" and [c.name for c in by["press_manual"].columns] == \
        ["page", "section", "para", "text"]

    cols, rows = a.select(by["press_manual"], Query(where=[("text", "contains", "FILTER")]))
    assert rows == [[2, "Press maintenance", 3, "Replace the filter after 500 h (100% load_test)."]]
    _, rows = a.select(by["press_manual"], Query(where=[("text", "contains", "100% load_")]))
    assert len(rows) == 1                                      # % and _ are literal, not wildcards
    _, rows = a.select(by["press_manual"], Query(where=[("text", "contains", "%")]))
    assert len(rows) == 1
    _, rows = a.select(by["press_manual"], Query(where=[("section", "=", "Safety")]))
    assert [r[3] for r in rows] == ["Safety", "Lock out the press before opening the guard."]

    t = by["press_manual.table1"]
    assert {c.name: c.type for c in t.columns} == {"Machine": "string", "Torque Nm": "number"}
    cols, rows = a.select(t, Query([("Torque Nm", "max", "m")]))
    assert rows[0][0] == 120

    _, rows = a.select(by["sop_molding"], Query(where=[("text", "contains", "purge")]))
    assert len(rows) == 1 and rows[0][0] == 2 and "colour change" in rows[0][3]


def test_docs_bad_files(tmp_path):
    (tmp_path / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(AdapterError, match="legacy .doc"):
        make_adapter(SourceCfg("d", "D", "docs", settings={"path": str(tmp_path / "old.doc")})).ping()
    (tmp_path / "broken.docx").write_bytes(b"not a zip")
    a = make_adapter(SourceCfg("d", "D", "docs", settings={"path": str(tmp_path / "broken.docx")}))
    with pytest.raises(AdapterError, match="not a valid Word"):
        a.discover()


def test_docs_tool_end_to_end(runtime, docs_dir):
    p = runtime.project
    src = SourceCfg("manuals", "Manuals", "docs", settings={"path": str(docs_dir / "press_manual.docx")})
    p.sources.append(src)
    src.objects = make_adapter(src).discover().objects
    obj = src.object("press_manual")
    obj.exposed = True
    t = new_curated("search_press_manual", "manuals", obj, [x.id for x in p.profiles])
    p.tools.append(t)
    runtime.start_source("manuals")
    assert runtime.publish(["search_press_manual"]) == ["search_press_manual"]
    env, err = runtime.call_tool("search_press_manual", {"contains": "hydraulic"}, client="t",
                                 profile=p.profiles[0].id)
    assert not err, err
    assert env["rows"] == [{"page": 1, "section": "Press maintenance", "para": 2,
                            "text": "Check the hydraulic oil level every shift."}]
    _, err = runtime.call_tool("search_press_manual", {"query": "x"}, client="t", profile=p.profiles[0].id)
    assert "unknown argument" in err
