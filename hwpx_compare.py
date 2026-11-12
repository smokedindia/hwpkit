#!/usr/bin/env python3
"""Compare two HWPX (OWPML) files for content fidelity.

Usage:
    python3 hwpx_compare.py A.hwpx B.hwpx [--json]

Extracts the content that a converter could lose -- body paragraph text,
tables (shape, merged cells, cell text), images (BinData hashes and pic
elements), footnotes/endnotes, memos, headers/footers, equations, text in
shapes/captions, and resolved paragraph numbering -- and reports the
differences. Elements are matched by XML local name only, so namespace
prefixes and URIs may differ between writers. Layout caches (linesegarray),
ids, zOrder, style ID numbers, Preview/*, settings.xml and version.xml are
ignored.

Exit codes: 0 equivalent, 3 differences found, 11 unreadable / not HWPX.
Stdlib only.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import posixpath
import re
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field

EXIT_EQUIVALENT = 0
EXIT_DIFFERENT = 3
EXIT_UNREADABLE = 11

SNIPPET_LEN = 80
HUMAN_MAX_DIFFS = 30
JSON_MAX_DIFFS = 1000
MAX_CELL_DIFFS_PER_TABLE = 5

_WS_RE = re.compile(r"\s+")
_SECTION_RE = re.compile(r"^Contents/section(\d+)\.xml$", re.IGNORECASE)

# Inline children of <t> that stand for characters.
_T_INLINE_CHARS = {"tab": "\t", "lineBreak": "\n", "nbSpace": " ", "fwSpace": " ", "hyphen": "-"}


class HwpxError(Exception):
    """Raised when a file cannot be read as HWPX."""


# --------------------------------------------------------------------------- helpers

def local(name: str) -> str:
    """Local part of an element or attribute name ('{uri}p' -> 'p', 'hp:p' -> 'p')."""
    if "}" in name:
        name = name.rsplit("}", 1)[1]
    return name.rsplit(":", 1)[-1]


def attr(elem: ET.Element, name: str, default: str | None = None) -> str | None:
    """Attribute lookup by local name."""
    if name in elem.attrib:
        return elem.attrib[name]
    for key, value in elem.attrib.items():
        if local(key) == name:
            return value
    return default


def int_attr(elem: ET.Element, name: str, default: int = 0) -> int:
    try:
        return int(attr(elem, name, str(default)))
    except (TypeError, ValueError):
        return default


def first_desc(elem: ET.Element, name: str) -> ET.Element | None:
    for e in elem.iter():
        if e is not elem and local(e.tag) == name:
            return e
    return None


def normalize(text: str) -> str:
    """NFC, collapse whitespace runs to a single space, strip."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def snippet(value) -> str:
    s = value if isinstance(value, str) else repr(value)
    return s if len(s) <= SNIPPET_LEN else s[:SNIPPET_LEN] + "..."


# --------------------------------------------------------------------------- data model

@dataclass
class Table:
    rows: int
    cols: int
    spans: list = field(default_factory=list)   # [(row, col, rowSpan, colSpan)]
    cells: list = field(default_factory=list)   # [((row, col), text)]

    def signature(self):
        return (self.rows, self.cols, tuple(self.spans), tuple(self.cells))


@dataclass
class Doc:
    path: str
    body: list = field(default_factory=list)          # non-empty normalized body paragraphs
    empty_body_paragraphs: int = 0
    tables: list = field(default_factory=list)
    footnotes: list = field(default_factory=list)
    endnotes: list = field(default_factory=list)
    memos: list = field(default_factory=list)
    headers: list = field(default_factory=list)
    footers: list = field(default_factory=list)
    equations: list = field(default_factory=list)
    shape_texts: list = field(default_factory=list)   # text boxes, captions
    numbering: list = field(default_factory=list)     # resolved tuples, document order
    pic_count: int = 0
    bindata: Counter = field(default_factory=Counter)  # sha256 -> count
    manifest_bindata: Counter = field(default_factory=Counter)  # media-type -> count
    sections: list = field(default_factory=list)

    def body_text(self) -> str:
        return "\n".join(self.body)

    def counts(self) -> dict:
        return {
            "sections": len(self.sections),
            "body_paragraphs": len(self.body),
            "empty_body_paragraphs": self.empty_body_paragraphs,
            "body_chars": sum(len(p) for p in self.body),
            "tables": len(self.tables),
            "table_cells": sum(len(t.cells) for t in self.tables),
            "merged_cells": sum(len(t.spans) for t in self.tables),
            "pic_elements": self.pic_count,
            "bindata_files": sum(self.bindata.values()),
            "manifest_bindata_items": sum(self.manifest_bindata.values()),
            "footnotes": len(self.footnotes),
            "endnotes": len(self.endnotes),
            "memos": len(self.memos),
            "headers": len(self.headers),
            "footers": len(self.footers),
            "equations": len(self.equations),
            "shape_texts": len(self.shape_texts),
            "numbered_paragraphs": len(self.numbering),
        }


# --------------------------------------------------------------------------- header.xml

class Numbering:
    """Resolves paraPr IDs to (kind, format, numFormat, start, level) tuples."""

    def __init__(self, header: ET.Element | None):
        self.para_heading: dict[str, tuple[str, str, int]] = {}
        self.numberings: dict[str, dict[int, tuple]] = {}
        self.numbering_order: list[str] = []
        self.bullets: dict[str, str] = {}
        if header is None:
            return
        for e in header.iter():
            name = local(e.tag)
            if name == "paraPr":
                h = first_desc(e, "heading")
                if h is not None:
                    self.para_heading[attr(e, "id", "")] = (
                        (attr(h, "type") or "NONE").upper(),
                        attr(h, "idRef", "0"),
                        int_attr(h, "level"),
                    )
            elif name == "numbering":
                nid = attr(e, "id", "")
                levels = {}
                for ph in e:
                    if local(ph.tag) == "paraHead":
                        levels[int_attr(ph, "level")] = (
                            normalize(ph.text or ""),
                            attr(ph, "numFormat", ""),
                            int_attr(ph, "start", 1),
                        )
                self.numberings[nid] = levels
                self.numbering_order.append(nid)
            elif name == "bullet":
                self.bullets[attr(e, "id", "")] = attr(e, "char", "")

    def _numbering(self, ref: str) -> dict:
        if ref in self.numberings:
            return self.numberings[ref]
        try:  # some writers use a 0-based position instead of an id
            return self.numberings[self.numbering_order[int(ref)]]
        except (ValueError, IndexError):
            pass
        return self.numberings[self.numbering_order[0]] if self.numbering_order else {}

    def resolve(self, para_pr_id: str | None, outline_ref: str) -> tuple | None:
        info = self.para_heading.get(para_pr_id or "")
        if not info or info[0] == "NONE":
            return None
        kind, ref, level = info
        if kind == "BULLET":
            return ("BULLET", self.bullets.get(ref, ""), "", 0, level)
        levels = self._numbering(outline_ref if kind == "OUTLINE" else ref)
        fmt, num_format, start = levels.get(level + 1, ("", "", 0))  # paraHead level is 1-based
        return (kind, fmt, num_format, start, level)


# --------------------------------------------------------------------------- section walker

class SectionWalker:
    """Walks a section tree and routes text into Doc categories."""

    def __init__(self, doc: Doc, numbering: Numbering):
        self.doc = doc
        self.numbering = numbering
        self.outline_ref = "0"

    def walk_section(self, root: ET.Element) -> None:
        sec_pr = first_desc(root, "secPr")
        if sec_pr is not None:
            self.outline_ref = attr(sec_pr, "outlineShapeIDRef", "0") or "0"
        for p in self._paragraphs_in(root):
            text = self.paragraph(p)
            if text:
                self.doc.body.append(text)
            else:
                self.doc.empty_body_paragraphs += 1

    @staticmethod
    def _paragraphs_in(container: ET.Element):
        """Top-level <p> elements beneath container (not nested inside another p)."""
        stack = list(reversed(list(container)))
        while stack:
            e = stack.pop()
            if local(e.tag) == "p":
                yield e
            else:
                stack.extend(reversed(list(e)))

    def _text_of(self, container: ET.Element) -> str:
        parts = [self.paragraph(p) for p in self._paragraphs_in(container)]
        return normalize(" ".join(x for x in parts if x))

    def paragraph(self, p: ET.Element) -> str:
        """Return normalized own text of p; nested objects go to their categories."""
        resolved = self.numbering.resolve(attr(p, "paraPrIDRef"), self.outline_ref)
        if resolved is not None:
            self.doc.numbering.append(resolved)
        parts: list[str] = []
        self._scan(p, parts)
        return normalize("".join(parts))

    def _scan(self, node: ET.Element, parts: list[str]) -> None:
        for c in node:
            name = local(c.tag)
            if name == "t":
                parts.append(self._t_text(c))
            elif name in ("linesegarray", "lineseg"):
                continue
            elif name == "tbl":
                self.table(c)
            elif name == "footNote":
                self.doc.footnotes.append(self._text_of(c))
            elif name == "endNote":
                self.doc.endnotes.append(self._text_of(c))
            elif name == "header":
                self.doc.headers.append(self._text_of(c))
            elif name == "footer":
                self.doc.footers.append(self._text_of(c))
            elif name == "memo" or (name == "fieldBegin" and (attr(c, "type") or "").upper() == "MEMO"):
                self.doc.memos.append(self._text_of(c))
            elif name == "equation":
                script = first_desc(c, "script")
                self.doc.equations.append(normalize(script.text or "") if script is not None else "")
            elif name == "p":  # paragraph nested in a shape / caption / text box
                text = self.paragraph(c)
                if text:
                    self.doc.shape_texts.append(text)
            else:
                if name == "pic":
                    self.doc.pic_count += 1
                self._scan(c, parts)

    @staticmethod
    def _t_text(t: ET.Element) -> str:
        out = [t.text or ""]
        for c in t:
            out.append(_T_INLINE_CHARS.get(local(c.tag), ""))
            if local(c.tag) == "t":  # defensive: nested t
                out.append(SectionWalker._t_text(c))
            out.append(c.tail or "")
        return "".join(out)

    def table(self, tbl: ET.Element) -> None:
        table = Table(rows=int_attr(tbl, "rowCnt", -1), cols=int_attr(tbl, "colCnt", -1))
        self.doc.tables.append(table)  # append before nested tables
        cells = []
        max_r = max_c = 0
        row_count = 0
        for tr in tbl:
            if local(tr.tag) != "tr":
                continue
            for tc in tr:
                if local(tc.tag) != "tc":
                    continue
                addr = first_child(tc, "cellAddr")
                span = first_child(tc, "cellSpan")
                r = int_attr(addr, "rowAddr", row_count) if addr is not None else row_count
                c = int_attr(addr, "colAddr", len(cells)) if addr is not None else len(cells)
                rs = int_attr(span, "rowSpan", 1) if span is not None else 1
                cs = int_attr(span, "colSpan", 1) if span is not None else 1
                if rs > 1 or cs > 1:
                    table.spans.append((r, c, rs, cs))
                max_r, max_c = max(max_r, r + rs), max(max_c, c + cs)
                cells.append(((r, c), self._text_of(tc)))
            row_count += 1
        table.cells = sorted(cells, key=lambda x: x[0])
        table.spans.sort()
        if table.rows < 0:
            table.rows = max(max_r, row_count)
        if table.cols < 0:
            table.cols = max_c


def first_child(elem: ET.Element, name: str) -> ET.Element | None:
    for c in elem:
        if local(c.tag) == name:
            return c
    return None


# --------------------------------------------------------------------------- loading

def _parse(zf: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(zf.read(name))
    except (ET.ParseError, KeyError, zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise HwpxError(f"cannot parse {name}: {exc}") from exc


def _section_names(zf: zipfile.ZipFile, names: set[str]) -> list[str]:
    """Section parts in spine order from content.hpf, else numeric order."""
    numeric = sorted((n for n in names if _SECTION_RE.match(n)),
                     key=lambda n: int(_SECTION_RE.match(n).group(1)))
    if "Contents/content.hpf" not in names:
        return numeric
    try:
        pkg = ET.fromstring(zf.read("Contents/content.hpf"))
    except ET.ParseError:
        return numeric
    href_by_id = {}
    for e in pkg.iter():
        if local(e.tag) == "item":
            href = attr(e, "href", "") or ""
            full = posixpath.normpath(href if href.startswith("Contents/") or "/" in href
                                      else posixpath.join("Contents", href))
            href_by_id[attr(e, "id", "")] = full
    ordered = []
    for e in pkg.iter():
        if local(e.tag) == "itemref":
            href = href_by_id.get(attr(e, "idref", ""))
            if href in names and _SECTION_RE.match(href) and href not in ordered:
                ordered.append(href)
    ordered += [n for n in numeric if n not in ordered]
    return ordered


def _manifest_bindata(zf: zipfile.ZipFile, names: set[str]) -> Counter:
    out = Counter()
    if "Contents/content.hpf" not in names:
        return out
    try:
        pkg = ET.fromstring(zf.read("Contents/content.hpf"))
    except ET.ParseError:
        return out
    for e in pkg.iter():
        if local(e.tag) == "item" and "bindata" in (attr(e, "href", "") or "").lower():
            out[attr(e, "media-type", "") or ""] += 1
    return out


def load(path: str) -> Doc:
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise HwpxError(f"not a readable zip: {exc}") from exc
    with zf:
        names = set(zf.namelist())
        if "mimetype" in names:
            mime = zf.read("mimetype").decode("ascii", "replace").strip()
            if mime != "application/hwp+zip":
                raise HwpxError(f"unexpected mimetype {mime!r}")
        sections = _section_names(zf, names)
        if not sections:
            raise HwpxError("no Contents/section*.xml")
        doc = Doc(path=path, sections=sections)
        header = _parse(zf, "Contents/header.xml") if "Contents/header.xml" in names else None
        walker = SectionWalker(doc, Numbering(header))
        for name in sections:
            walker.walk_section(_parse(zf, name))
        for name in names:
            if name.lower().startswith("bindata/") and not name.endswith("/"):
                try:
                    doc.bindata[hashlib.sha256(zf.read(name)).hexdigest()] += 1
                except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                    raise HwpxError(f"cannot read {name}: {exc}") from exc
        doc.manifest_bindata = _manifest_bindata(zf, names)
    return doc


# --------------------------------------------------------------------------- comparison

def diff(category: str, kind: str, location: str, a=None, b=None) -> dict:
    d = {"category": category, "kind": kind, "location": location}
    if a is not None:
        d["a"] = snippet(a)
    if b is not None:
        d["b"] = snippet(b)
    return d


def diff_sequences(category: str, a: list, b: list, label: str = "#") -> list[dict]:
    """Item-level differences between two ordered lists, aligned with difflib."""
    if a == b:
        return []
    out = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        paired = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(paired):
            out.append(diff(category, "changed", f"A{label}{i1 + k} / B{label}{j1 + k}",
                            a[i1 + k], b[j1 + k]))
        for i in range(i1 + paired, i2):
            out.append(diff(category, "missing_in_B", f"A{label}{i} (near B{label}{j1 + paired})", a=a[i]))
        for j in range(j1 + paired, j2):
            out.append(diff(category, "extra_in_B", f"B{label}{j} (near A{label}{i1 + paired})", b=b[j]))
    return out


def compare_table(ia: int, ta: Table, ib: int, tb: Table) -> list[dict]:
    loc = f"table A#{ia} / B#{ib}"
    out = []
    if (ta.rows, ta.cols) != (tb.rows, tb.cols):
        out.append(diff("tables", "shape", loc, f"{ta.rows}x{ta.cols}", f"{tb.rows}x{tb.cols}"))
    if ta.spans != tb.spans:
        sa, sb = set(ta.spans), set(tb.spans)
        fmt = lambda s: ", ".join(f"r{r}c{c}:{rs}x{cs}" for r, c, rs, cs in sorted(s)) or "-"
        out.append(diff("tables", "merged_cells", loc, fmt(sa - sb), fmt(sb - sa)))
    cells_a, cells_b = dict(ta.cells), dict(tb.cells)
    cell_diffs = []
    for addr in sorted(set(cells_a) | set(cells_b)):
        va, vb = cells_a.get(addr), cells_b.get(addr)
        if va != vb:
            cell_diffs.append(diff("tables", "cell_text", f"{loc} cell r{addr[0]}c{addr[1]}",
                                   "<no cell>" if va is None else va,
                                   "<no cell>" if vb is None else vb))
    out += cell_diffs[:MAX_CELL_DIFFS_PER_TABLE]
    if len(cell_diffs) > MAX_CELL_DIFFS_PER_TABLE:
        out.append(diff("tables", "cell_text_more", loc,
                        f"+{len(cell_diffs) - MAX_CELL_DIFFS_PER_TABLE} more differing cells"))
    return out


def compare_tables(a: list[Table], b: list[Table]) -> list[dict]:
    sig_a = [t.signature() for t in a]
    sig_b = [t.signature() for t in b]
    if sig_a == sig_b:
        return []
    out = []
    sm = difflib.SequenceMatcher(None, sig_a, sig_b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        paired = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(paired):
            out += compare_table(i1 + k, a[i1 + k], j1 + k, b[j1 + k])
        for i in range(i1 + paired, i2):
            first = next((txt for _, txt in a[i].cells if txt), "")
            out.append(diff("tables", "missing_in_B", f"table A#{i} ({a[i].rows}x{a[i].cols})", a=first))
        for j in range(j1 + paired, j2):
            first = next((txt for _, txt in b[j].cells if txt), "")
            out.append(diff("tables", "extra_in_B", f"table B#{j} ({b[j].rows}x{b[j].cols})", b=first))
    return out


def compare_images(a: Doc, b: Doc) -> list[dict]:
    out = []
    if a.pic_count != b.pic_count:
        out.append(diff("images", "pic_count", "pic elements", str(a.pic_count), str(b.pic_count)))
    only_a, only_b = a.bindata - b.bindata, b.bindata - a.bindata
    for h, n in sorted(only_a.items()):
        out.append(diff("images", "missing_in_B", f"BinData sha256 {h[:16]} x{n}"))
    for h, n in sorted(only_b.items()):
        out.append(diff("images", "extra_in_B", f"BinData sha256 {h[:16]} x{n}"))
    if a.manifest_bindata != b.manifest_bindata:
        fmt = lambda c: ", ".join(f"{k or '?'}:{v}" for k, v in sorted(c.items())) or "-"
        out.append(diff("images", "manifest", "content.hpf BinData items",
                        fmt(a.manifest_bindata), fmt(b.manifest_bindata)))
    return out


def similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def compare(a: Doc, b: Doc) -> dict:
    diffs: list[dict] = []
    diffs += diff_sequences("body", a.body, b.body, label="¶")
    diffs += compare_tables(a.tables, b.tables)
    diffs += compare_images(a, b)
    for cat in ("footnotes", "endnotes", "memos", "headers", "footers", "equations", "shape_texts"):
        diffs += diff_sequences(cat, getattr(a, cat), getattr(b, cat))
    diffs += diff_sequences("numbering", a.numbering, b.numbering, label="#")
    by_cat = Counter(d["category"] for d in diffs)
    ta, tb = a.body_text(), b.body_text()
    return {
        "a": a.path,
        "b": b.path,
        "equivalent": not diffs,
        "counts": {"a": a.counts(), "b": b.counts()},
        "body_chars": {"a": len(ta.replace("\n", "")), "b": len(tb.replace("\n", ""))},
        "similarity": round(similarity(ta, tb), 6),
        "difference_count": len(diffs),
        "differences_by_category": dict(by_cat),
        "differences": diffs,
    }


# --------------------------------------------------------------------------- output

def pick_diffs(diffs: list[dict], limit: int) -> list[dict]:
    """Up to `limit` diffs, taken round-robin per category so no category hides the others."""
    queues: dict[str, list[dict]] = {}
    for d in diffs:
        queues.setdefault(d["category"], []).append(d)
    chosen: dict[str, int] = dict.fromkeys(queues, 0)
    taken = 0
    while taken < min(limit, len(diffs)):
        for cat, q in queues.items():
            if chosen[cat] < len(q) and taken < limit:
                chosen[cat] += 1
                taken += 1
    return [d for cat, q in queues.items() for d in q[:chosen[cat]]]


def print_human(report: dict) -> None:
    ca, cb = report["counts"]["a"], report["counts"]["b"]
    print(f"A: {report['a']}")
    print(f"B: {report['b']}")
    print()
    width = max(len(k) for k in ca)
    print(f"{'category'.ljust(width)}  {'A':>10}  {'B':>10}")
    for key in ca:
        mark = "" if ca[key] == cb[key] else "  *"
        print(f"{key.ljust(width)}  {ca[key]:>10}  {cb[key]:>10}{mark}")
    print()
    print(f"body text chars: A={report['body_chars']['a']}  B={report['body_chars']['b']}")
    print(f"body similarity: {report['similarity']:.4f}")
    print()
    if report["equivalent"]:
        print("RESULT: equivalent (no content differences)")
        return
    by_cat = ", ".join(f"{k}={v}" for k, v in report["differences_by_category"].items())
    print(f"RESULT: {report['difference_count']} difference(s) [{by_cat}]")
    for d in pick_diffs(report["differences"], HUMAN_MAX_DIFFS):
        print(f"- [{d['category']}] {d['kind']} @ {d['location']}")
        if "a" in d:
            print(f"    A: {d['a']}")
        if "b" in d:
            print(f"    B: {d['b']}")
    hidden = report["difference_count"] - HUMAN_MAX_DIFFS
    if hidden > 0:
        print(f"... {hidden} more (use --json)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare two HWPX files for content fidelity.")
    ap.add_argument("a", help="first .hwpx file")
    ap.add_argument("b", help="second .hwpx file")
    ap.add_argument("--json", action="store_true", help="print a JSON report")
    args = ap.parse_args(argv)

    docs = []
    for path in (args.a, args.b):
        try:
            docs.append(load(path))
        except HwpxError as exc:
            if args.json:
                print(json.dumps({"error": str(exc), "file": path}, ensure_ascii=False))
            else:
                print(f"error: {path}: {exc}", file=sys.stderr)
            return EXIT_UNREADABLE

    report = compare(*docs)
    if args.json:
        out = dict(report)
        out["differences"] = report["differences"][:JSON_MAX_DIFFS]
        out["differences_truncated"] = report["difference_count"] > JSON_MAX_DIFFS
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    return EXIT_EQUIVALENT if report["equivalent"] else EXIT_DIFFERENT


if __name__ == "__main__":
    sys.exit(main())
