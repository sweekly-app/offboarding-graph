"""Run statements in Relate.

Default: the `relate-db` Python package (`import relate`), the engine running
in-process on a local store, with real Cypher parameters. Fallback: the
`relate` command-line binary (`RELATE_BIN` or PATH), fed literal statements.
Either way nothing leaves the machine.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import graph
from .data import Exports

try:  # the PyPI package is `relate-db`; its import name is `relate`
    import relate as _relate
except ImportError:  # pragma: no cover - depends on the machine
    _relate = None


def _binary() -> str | None:
    return os.environ.get("RELATE_BIN") or shutil.which("relate")


def backend() -> str:
    """'library', 'cli', or raise with the install hint."""
    if _relate is not None and not os.environ.get("RELATE_BIN"):
        return "library"
    if _binary():
        return "cli"
    raise RuntimeError("Relate is not installed: `pip install relate-db` (see README, Install)")


def available() -> bool:
    try:
        backend()
        return True
    except RuntimeError:
        return False


def load(store: Path, ex: Exports) -> None:
    """Replace the store's graph with the exports (a reload never duplicates)."""
    if backend() == "library":
        conn = _relate.connect(str(store))
        try:
            conn.autocommit = False
            conn.execute(graph.CLEAR)
            for cypher, params in graph.load_statements(ex):
                conn.execute(cypher, params)
            conn.commit()
        finally:
            conn.close()
        return
    script = graph.CLEAR + ";\n" + graph.load_script(ex)
    subprocess.run([_binary(), "-d", str(store)], input=script, text=True,
                   check=True, capture_output=True)


def run(store: Path, cypher: str, params: dict | None = None) -> list[dict]:
    """Run one read-only statement on an existing store; rows as plain dicts."""
    if not Path(store).exists():
        raise FileNotFoundError(f"no Relate store at {store}: run `offgraph load <folder>` first")
    if backend() == "library":
        conn = _relate.connect(str(store), read_only=True)
        try:
            return [dict(r) for r in conn.execute(cypher, params or {})]
        finally:
            conn.close()
    if params:
        raise ValueError("the CLI backend takes literal statements only")
    res = subprocess.run([_binary(), "--jsonl", "-d", str(store), cypher],
                         text=True, check=True, capture_output=True)
    return [json.loads(line) for line in res.stdout.splitlines() if line.strip()]


def rows(store: Path, name: str, email: str | None = None) -> list[dict]:
    """Answer one named question (graph.QUESTIONS)."""
    if backend() == "library":
        return run(store, graph.QUESTIONS[name].strip(), graph.params(name, email))
    return run(store, graph.query(name, email))
