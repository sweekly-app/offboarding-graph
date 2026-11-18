"""`offgraph web`: one self-contained HTML page that runs the same questions in the
browser, on Relate's WebAssembly build (@relate/relate-wasm), over the exports
embedded in the page. No server: open the file, or publish it as a static page."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import graph
from .data import Exports

RELATE_WASM = "1.0.1"  # the npm version the page loads; the same engine version as relate-db
CDN = f"https://cdn.jsdelivr.net/npm/@relate/relate-wasm@{RELATE_WASM}"
# Subresource Integrity of the three files the page fetches from the CDN (sha384 of the npm 1.0.1 files;
# the browser refuses a file that does not match). Recompute them when RELATE_WASM changes.
OPFS = "snippets/relate-wasm-1db7a39dcbf9937d/js/opfs.js"
SRI = {"relate.js": "sha384-drffXTT8sX9hGShKCWEB05N1zBo3delWZ0KBFqYJoUx9yon7TldOJwPyRtWdBIc/",
       "relate_bg.wasm": "sha384-uGfgsjAXT/d7q/amuqw56mn7Bs9icE8cTfsSoG+k2JrHNbUMBOOxe3lF3p0/iJVP",
       OPFS: "sha384-bqicKw8V5m6x7U+ghC2KKSMwGyKIIr5x5BRsU/Bs+vTgcXj2JeGp3avVt7IqZiGa"}
TEMPLATE = Path(__file__).with_name("web.html")


def examples(path: Path) -> list[dict]:
    """The numbered queries of examples.cypher as {title, query}."""
    if not path.exists():
        return []
    out, title, lines = [], None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"//\s*\d+\.\s*(.+?)\s*$", line)
        if m:
            title, lines = m.group(1).rstrip("."), []
        elif title and line.strip() and not line.strip().startswith("//"):
            lines.append(line.strip())
            if line.rstrip().endswith(";"):
                out.append({"title": title, "query": " ".join(lines).rstrip(";")})
                title, lines = None, []
    return out


def page(ex: Exports, title: str, start: str | None = None, examples_file: Path | None = None,
         name: str = "your company") -> str:
    data = {
        "relate": RELATE_WASM,
        "load": [[q, p] for q, p in graph.load_statements(ex)],
        "questions": {k: v.strip() for k, v in graph.QUESTIONS.items()},
        "remove": graph.REMOVE,
        "people": [{k: p[k] for k in ("email", "name", "team", "status")} for p in ex.people],
        "examples": examples(examples_file) if examples_file else [],
        "start": start,
        "name": name,
    }
    # No "<", ">" or "&" in the embedded JSON at all, so no value can open a comment or close the tag.
    blob = (json.dumps(data, separators=(",", ":"), ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    html = TEMPLATE.read_text(encoding="utf-8")
    for k, v in {"__TITLE__": _esc(title), "__RELATE__": RELATE_WASM, "__CDN__": CDN, "__OPFS__": OPFS,
                 "__SRI_JS__": SRI["relate.js"], "__SRI_WASM__": SRI["relate_bg.wasm"], "__SRI_OPFS__": SRI[OPFS]}.items():
        html = html.replace(k, v)
    return html.replace("__DATA__", blob)


def _esc(s: str) -> str:
    from html import escape
    return escape(s)
