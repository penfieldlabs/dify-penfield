"""Live end-to-end test of all 17 tools against the real Penfield API.

Run only with a dedicated Penfield TEST account whose key has read, write
and delete permission:

    PENFIELD_TEST_API_KEY=... python tests/live_e2e.py

The test creates memories, a checkpoint and a file, all marked with a
unique run tag, and touches nothing else. It never prints existing account
content: account-wide results (awaken, reflect) are only checked for shape.
At the end it deletes exactly what it created and checks that the
account's memory, document and relationship counts are back where they
started.
"""

from __future__ import annotations

import importlib
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dify_plugin import Tool  # noqa: E402
from dify_plugin.errors.tool import ToolProviderCredentialValidationError  # noqa: E402

import penfield_client  # noqa: E402
from provider.penfield import PenfieldProvider  # noqa: E402

KEY = os.environ["PENFIELD_TEST_API_KEY"]
RUN = "dify-live-" + uuid.uuid4().hex[:8]
FOLDER = f"/{RUN}/"
results: list[tuple[str, bool, str]] = []
created_memories: list[str] = []
created_files: list[str] = []


def tool_class(name: str) -> type[Tool]:
    module = importlib.import_module(f"tools.{name}")
    return next(
        v
        for v in vars(module).values()
        if isinstance(v, type) and v is not Tool and issubclass(v, Tool)
    )


def run(name: str, params: dict) -> list[tuple[str, object]]:
    cls = tool_class(name)
    tool = cls.__new__(cls)
    tool.runtime = SimpleNamespace(credentials={"penfield_api_key": KEY})
    tool.create_text_message = lambda t: ("text", t)
    tool.create_json_message = lambda o: ("json", o)
    return list(tool._invoke(params))


def data(out) -> dict:
    return out[-1][1] if out and out[-1][0] == "json" else {}


def check(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(
        ("PASS " if cond else "FAIL ")
        + name
        + (f"  [{detail[:300]}]" if detail and not cond else "")
    )


def account_counts() -> tuple:
    """Count-only snapshot of the account. Never fetches content."""
    stats = penfield_client.PenfieldClient(KEY).request("GET", "/search/stats") or {}
    return tuple(stats.get(k) for k in ("total_memories", "total_documents", "total_relationships"))


def mcp_refuses_name(name: str) -> bool:
    """Ask Penfield's MCP server to save a checkpoint under ``name``.

    True when it answers DUPLICATE_NAME. If it does save one, it is deleted.
    """
    import json

    import httpx

    token = httpx.post(
        "https://api.penfield.app/api/v2/auth/token",
        headers={"Authorization": f"Bearer {KEY}", "User-Agent": "dify-penfield-live-test"},
        timeout=30,
    ).json()["data"]["access_token"]
    resp = httpx.post(
        "https://mcp.penfield.app/",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "save_context", "arguments": {"name": name, "description": "dup"}},
        },
        timeout=90,
    )
    refused = "DUPLICATE_NAME" in json.dumps(resp.json())
    if not refused:
        for ctx in run_data("list_contexts", {"name_pattern": name}).get("contexts", []):
            if ctx["id"] not in created_memories:
                created_memories.append(ctx["id"])
    return refused


def run_data(name: str, params: dict) -> dict:
    return data(run(name, params))


def wait_for(fn, tries: int = 12, delay: float = 5.0):
    out = None
    for _ in range(tries):
        ok, out = fn()
        if ok:
            return True, out
        time.sleep(delay)
    return False, out


def main() -> int:
    t0 = time.time()
    baseline = account_counts()
    try:
        exercise()
    finally:
        cleanup(baseline)
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} passed in {time.time() - t0:.1f}s  run tag {RUN}")
    return 0 if passed == len(results) else 1


def exercise() -> None:
    PenfieldProvider()._validate_credentials({"penfield_api_key": KEY})
    check("provider accepts the key", True)
    try:
        PenfieldProvider()._validate_credentials({"penfield_api_key": "tm_pf_x_ak_invalid"})
        check("provider rejects an invalid key", False)
    except ToolProviderCredentialValidationError as e:
        check("provider rejects an invalid key", "rejected" in str(e), str(e))

    out = run("awaken", {})
    check("awaken returns a briefing", isinstance(data(out).get("briefing"), str) and out[0][1])

    phrase = f"zebracorn {RUN}"
    out = run(
        "store",
        {
            "content": f"Decision: the {phrase} launch uses blue packaging.",
            "importance": 0.6,
            "tags": f"{RUN},packaging",
        },
    )
    a = data(out).get("memory", {})
    check("store A", a.get("id") and RUN in a.get("tags", []), out[0][1])
    out = run(
        "store",
        {"content": f"Correction: the {phrase} launch moved to green packaging.", "tags": RUN},
    )
    b = data(out).get("memory", {})
    check("store B", b.get("id"), out[0][1])
    created_memories.extend(m["id"] for m in (a, b) if m.get("id"))
    if len(created_memories) < 2:
        return

    out = run("fetch", {"id": a["id"]})
    check("fetch", data(out).get("memory", {}).get("id") == a["id"] and phrase in out[0][1])

    out = run(
        "update_memory",
        {"memory_id": a["id"], "importance": 0.9, "tags": f"{RUN},packaging,updated"},
    )
    m = data(out).get("memory", {})
    check("update_memory", m.get("importance") == 0.9 and "updated" in m.get("tags", []), out[0][1])

    out = run(
        "connect",
        {
            "from_memory": b["id"],
            "to_memory": a["id"],
            "relationship_type": "updates",
            "strength": 0.8,
        },
    )
    check("connect", data(out).get("connected"), out[0][1])

    out = run("explore", {"start_memory": b["id"], "max_depth": 1})
    ids = {n for path in data(out).get("paths", []) for n in path.get("nodes", [])}
    check("explore finds the linked memory", a["id"] in ids, out[0][1])

    def both_found(tool: str, params: dict):
        def attempt():
            res = run(tool, params)
            hits = {r["id"] for r in data(res).get("results", [])}
            return {a["id"], b["id"]} <= hits, res

        return attempt

    ok, out = wait_for(
        both_found("recall", {"query": phrase, "limit": 5, "tags": RUN, "source_type": "memory"})
    )
    check("recall finds both memories", ok, out[0][1] if out else "")
    out = run("recall", {"query": phrase, "source_type": "document", "tags": RUN})
    check("recall source_type=document excludes memories", data(out).get("count") == 0, out[0][1])
    ok, out = wait_for(
        both_found("search", {"query": f"what colour is the {phrase} packaging", "limit": 10})
    )
    # Unfiltered by tag, so report only a count: other memories may be in the results.
    check(
        "search (semantic) finds both memories",
        ok,
        f"{data(out).get('count') if out else 0} results",
    )

    out = run("reflect", {"time_window": "today"})
    check(
        "reflect returns an analysis",
        out[-1][0] == "json" and "total_memories_analyzed" in data(out).get("statistics", {}),
    )
    out = run("reflect", {"time_window": "7d"})
    check(
        "reflect day window",
        out[-1][0] == "json" and "total_memories_analyzed" in data(out).get("statistics", {}),
    )

    # checkpoints
    name = f"{RUN} handoff"
    out = run(
        "save_context",
        {
            "name": name,
            "description": f"Packaging for {phrase}: see memory_id: {a['id']}. Next: confirm green.",
        },
    )
    saved = data(out)
    if saved.get("context_id"):
        created_memories.append(saved["context_id"])
    check("save_context", saved.get("success") and saved.get("references_resolved") == 1, out[0][1])
    stored = penfield_client.PenfieldClient(KEY).request(
        "GET", f"/memories/{saved.get('context_id')}"
    )
    check(
        "checkpoint stored like the MCP server's own",
        stored.get("memory_type") == "checkpoint" and stored.get("source_type") == "direct_input",
        f"{stored.get('memory_type')} / {stored.get('source_type')}",
    )
    check("MCP server refuses the name this plugin used", mcp_refuses_name(name))
    out = run("save_context", {"name": name, "description": "again"})
    check(
        "save_context refuses a duplicate name",
        data(out).get("error_code") == "DUPLICATE_NAME",
        out[0][1],
    )
    out = run("list_contexts", {"name_pattern": RUN, "include_descriptions": True})
    listed = data(out)
    check(
        "list_contexts finds it",
        listed.get("total") == 1 and listed["contexts"][0]["name"] == name,
        out[0][1],
    )
    out = run("restore_context", {"name": name})
    restored = data(out)
    check(
        "restore_context returns the referenced memory",
        restored.get("success") and a["id"] in {m["id"] for m in restored.get("memories", [])},
        out[0][1],
    )
    out = run("restore_context", {"name": saved.get("context_id", "")})
    check(
        "restore_context by id", data(out).get("context_id") == saved.get("context_id"), out[0][1]
    )
    out = run("restore_context", {"name": f"{RUN} no such context"})
    check("restore_context unknown name", data(out).get("success") is False, out[0][1])

    # files
    path = f"{FOLDER}notes.md"
    out = run("save_artifact", {"path": path, "content": f"# {RUN}\nBlue, then green."})
    if data(out).get("saved"):
        created_files.append(path)
    check("save_artifact", data(out).get("saved"), out[0][1])
    out = run("save_artifact", {"path": path, "content": "again"})
    check("save_artifact refuses an existing path", "Penfield error" in out[0][1], out[0][1])
    out = run("retrieve_artifact", {"path": path})
    check("retrieve_artifact", data(out).get("content") == f"# {RUN}\nBlue, then green.", out[0][1])
    out = run("list_artifacts", {"path_prefix": FOLDER})
    names = [f.get("name") for f in data(out).get("files", [])]
    check("list_artifacts", names == ["notes.md"], out[0][1])
    out = run("list_artifacts", {"path_prefix": FOLDER, "name_pattern": "zzz"})
    check("list_artifacts name_pattern filters", data(out).get("files") == [], out[0][1])
    out = run("delete_artifact", {"path": path})
    if data(out).get("deleted"):
        created_files.remove(path)
    check("delete_artifact", data(out).get("deleted"), out[0][1])
    out = run("retrieve_artifact", {"path": path})
    check("deleted file is gone", "HTTP 404" in out[0][1], out[0][1])

    out = run("disconnect", {"from_memory": b["id"], "to_memory": a["id"]})
    check("disconnect", data(out).get("disconnected"), out[0][1])
    out = run("explore", {"start_memory": b["id"], "max_depth": 1})
    ids = {n for path in data(out).get("paths", []) for n in path.get("nodes", [])}
    check("explore after disconnect no longer reaches A", a["id"] not in ids, out[0][1])

    out = run("fetch", {"id": str(uuid.uuid4())})
    check("fetch unknown id gives a clean 404", "HTTP 404" in out[0][1], out[0][1])
    out = run("store", {"content": "x", "tags": "ok,!"})
    check("bad tag refused before any request", "would not be stored" in out[0][1], out[0][1])
    check(
        "a refused call also returns JSON with success false",
        [k for k, _ in out] == ["text", "json"] and data(out).get("success") is False,
        str(out),
    )

    before = dict(penfield_client._cache)
    run("fetch", {"id": a["id"]})
    check("access token cached between calls", before.keys() == penfield_client._cache.keys())


def cleanup(baseline: tuple) -> None:
    client = penfield_client.PenfieldClient(KEY)
    for path in created_files:
        try:
            client.request("DELETE", "/artifacts", params={"path": path})
        except penfield_client.PenfieldError as e:
            check(f"cleanup file {path}", False, str(e))
    for mid in created_memories:
        try:
            client.request("DELETE", f"/memories/{mid}")
            client.request("GET", f"/memories/{mid}")
            check(f"cleanup {mid[:8]} (still exists!)", False)
        except penfield_client.PenfieldError as e:
            check(f"cleanup {mid[:8]}", e.status == 404, str(e))
    ok, after = wait_for(lambda: ((c := account_counts()) == baseline, c), tries=8)
    check("account counts back to baseline", ok, f"{baseline} -> {after}")


if __name__ == "__main__":
    sys.exit(main())
