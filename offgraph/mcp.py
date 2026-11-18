"""`offgraph mcp`: the five answers as tools for an AI assistant, over the Model
Context Protocol (JSON-RPC 2.0, one message per line on stdin/stdout).

Read-only: every tool opens the Relate store read-only, and no tool takes Cypher.
Standard library only; any MCP client that starts a local command can use it."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import IO

from . import __version__, engine

PROTOCOL = "2025-06-18"
EMAIL = {"type": "object", "properties": {"email": {"type": "string", "description": "the person's email"}},
         "required": ["email"]}
NONE = {"type": "object", "properties": {}}
TOOLS = [
    ("offboarding_checklist", "The offboarding steps for a person, in a safe order: transfer ownership, "
     "second admins, admin rights, paid seats, direct grants, disable.", EMAIL),
    ("who_takes_over", "Resources only this person owns, each with a suggested new owner and the rule "
     "that picked them.", EMAIL),
    ("access_paths", "Every resource the person can reach, and the chain of groups that leads there "
     "(or 'direct').", EMAIL),
    ("sole_admin_groups", "Groups this person administers where no other active admin remains.", EMAIL),
    ("leaver_seats", "Paid seats still held by people marked as left, with the monthly cost.", NONE),
]


def _answer(store: Path, tool: str, args: dict):
    from .cli import _checklist, _person, _reach  # one implementation for the CLI and the tools
    if tool == "leaver_seats":
        rows = engine.rows(store, "seats")
        return {"seats": rows, "total_monthly_usd": round(sum(float(r["monthly_usd"]) for r in rows), 2)}
    if not str(args.get("email") or "").strip():
        raise ValueError(f"{tool} needs an email argument")
    email = _person(store, str(args["email"]))
    if tool == "offboarding_checklist":
        return {"email": email, "steps": _checklist(store, email)}
    if tool == "who_takes_over":
        return {"email": email, "resources": engine.rows(store, "orphans", email)}
    if tool == "access_paths":
        return {"email": email, "resources": _reach(store, email)}
    if tool == "sole_admin_groups":
        return {"email": email, "groups": [r["group_name"] for r in engine.rows(store, "sole_admin", email)]}
    raise KeyError(tool)


def handle(store: Path, msg: dict) -> dict | None:
    """One JSON-RPC message in, the reply out (None for a notification)."""
    from .cli import NoSuchPerson
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None
    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "offgraph", "version": __version__},
                   "instructions": f"Offboarding answers from the Relate store {store}. Read-only."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d, "inputSchema": s,
                              "annotations": {"readOnlyHint": True}} for n, d, s in TOOLS]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name") if isinstance(params, dict) else None
        if not isinstance(params, dict) or not isinstance(params.get("arguments") or {}, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "params must be an object"}}
        if not isinstance(name, str) or name not in {n for n, _d, _s in TOOLS}:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            data = _answer(store, name, params.get("arguments") or {})
        except NoSuchPerson as e:
            return ok({"content": [{"type": "text", "text": f"no person with email {e.args[0]} in the store"}],
                       "isError": True})
        except Exception as e:  # engine and file errors go back to the assistant as a tool error
            return ok({"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
        return ok({"content": [{"type": "text", "text": json.dumps(data, indent=2)}], "structuredContent": data})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(store: Path, inp: IO[str] | None = None, out: IO[str] | None = None) -> int:
    # MCP messages are UTF-8 whatever the console's code page (Windows) says.
    inp = inp or io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    out = out or io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    if not Path(store).exists():
        raise FileNotFoundError(f"no Relate store at {store}: run `offgraph load <folder>` first")
    for line in inp:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(store, msg) if isinstance(msg, dict) else {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if reply is not None:
            out.write(json.dumps(reply) + "\n")
            out.flush()
    return 0
