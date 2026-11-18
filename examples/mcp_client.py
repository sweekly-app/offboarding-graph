"""A minimal MCP client: start a server command, send initialize, tools/list and one
tools/call of offboarding_checklist, and print the replies readably.

    python examples/mcp_client.py dev.ruiz@example.com offgraph --store demo/offboarding.rl mcp

Any command that speaks MCP over stdin/stdout works after the email."""
import json
import subprocess
import sys

if len(sys.argv) < 3:
    sys.exit("usage: python examples/mcp_client.py <email> <server command…>\n"
             "  e.g. python examples/mcp_client.py dev.ruiz@example.com offgraph --store demo/offboarding.rl mcp")
email, cmd = sys.argv[1], sys.argv[2:]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")


def ask(i, method, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())["result"]


r = ask(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "example-client", "version": "1"}})
p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
p.stdin.flush()
print(f"initialize: server {r['serverInfo']['name']} {r['serverInfo']['version']}")
tools = ask(2, "tools/list")["tools"]
print(f"tools/list: {len(tools)} tools: " + ", ".join(t["name"] for t in tools))
print(f"tools/call offboarding_checklist {email}")
res = ask(3, "tools/call", {"name": "offboarding_checklist", "arguments": {"email": email}})
if res.get("isError"):
    print(res["content"][0]["text"])
else:
    steps = res["structuredContent"]["steps"]
    print(f"{len(steps)} steps; the first 3:")
    for i, s in enumerate(steps[:3], 1):
        print(f"{i}. {s}")
p.stdin.close()
p.wait()
