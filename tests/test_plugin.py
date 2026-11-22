"""Unit tests for the Penfield Dify plugin. HTTP is faked; no network."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import penfield_client  # noqa: E402
from penfield_client import PenfieldClient, PenfieldError  # noqa: E402

KEY = "tm_test_ak_secret123"
MEM_ID = "550e8400-e29b-41d4-a716-446655440000"
MEM_ID2 = "660f9500-f30c-52e5-b827-557766551111"


class FakeAPI:
    """Records calls and answers like the Penfield API."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.token_n = 0
        self.routes: dict[tuple[str, str], Any] = {}
        self.fail_next_401 = False
        self.refresh_ok = True

    def _resp(self, status: int, body: Any, method: str, url: str) -> httpx.Response:
        return httpx.Response(
            status,
            content=json.dumps(body).encode() if body is not None else b"",
            headers={"content-type": "application/json"},
            request=httpx.Request(method, url),
        )

    def tokens(self, expires_in: int = 86400) -> dict[str, Any]:
        self.token_n += 1
        return {
            "status": "success",
            "data": {
                "access_token": f"access-{self.token_n}",
                "refresh_token": f"refresh-{self.token_n}",
                "expires_in": expires_in,
                "token_type": "Bearer",
                "tenant_id": "pf_t",
            },
        }

    def post(self, url, headers=None, json=None, timeout=None, **kw):
        self.calls.append({"method": "POST", "url": url, "headers": headers or {}, "json": json})
        if url.endswith("/auth/token"):
            if headers.get("Authorization") != f"Bearer {KEY}":
                return self._resp(
                    401,
                    {
                        "status": "error",
                        "error": {"code": "AUTH_UNAUTHORIZED", "message": "Invalid API key"},
                    },
                    "POST",
                    url,
                )
            return self._resp(200, self.tokens(), "POST", url)
        if url.endswith("/auth/refresh"):
            if not self.refresh_ok:
                return self._resp(
                    401, {"status": "error", "error": {"code": "AUTH_TOKEN_INVALID"}}, "POST", url
                )
            return self._resp(200, self.tokens(), "POST", url)
        raise AssertionError(f"unexpected post {url}")

    def request(self, method, url, json=None, params=None, headers=None, timeout=None, **kw):
        self.calls.append(
            {"method": method, "url": url, "headers": headers or {}, "json": json, "params": params}
        )
        if self.fail_next_401:
            self.fail_next_401 = False
            return self._resp(
                401, {"status": "error", "error": {"code": "AUTH_UNAUTHORIZED"}}, method, url
            )
        path = url.split("/api/v2", 1)[1]
        handler = self.routes.get((method, path))
        if handler is None:
            return self._resp(
                404,
                {"status": "error", "error": {"code": "RES_NOT_FOUND", "message": "Not found"}},
                method,
                url,
            )
        status, body = handler(json, params) if callable(handler) else handler
        return self._resp(status, body, method, url)


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    penfield_client._cache.clear()
    monkeypatch.setattr(penfield_client.httpx, "post", fake.post)
    monkeypatch.setattr(penfield_client.httpx, "request", fake.request)
    return fake


def run_tool(tool_cls, params: dict[str, Any]) -> list[Any]:
    tool = tool_cls.__new__(tool_cls)
    tool.runtime = SimpleNamespace(credentials={"penfield_api_key": KEY})
    tool.create_text_message = lambda text: ("text", text)
    tool.create_json_message = lambda obj: ("json", obj)
    return list(tool._invoke(params))


def ok(data: Any) -> tuple[int, dict[str, Any]]:
    return 200, {"status": "success", "data": data}


# --------------------------------------------------------------------- auth


def test_key_exchanged_once_and_cached(api):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    c = PenfieldClient(KEY)
    c.request("GET", "/auth/verify")
    c.request("GET", "/auth/verify")
    exchanges = [x for x in api.calls if x["url"].endswith("/auth/token")]
    assert len(exchanges) == 1
    gets = [x for x in api.calls if x["method"] == "GET"]
    assert all(g["headers"]["Authorization"] == "Bearer access-1" for g in gets)


def test_api_key_never_sent_to_data_endpoints(api):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    PenfieldClient(KEY).request("GET", "/auth/verify")
    for call in api.calls:
        if not call["url"].endswith("/auth/token"):
            assert KEY not in json.dumps(call["headers"])


def test_invalid_key_gives_clear_error(api):
    with pytest.raises(PenfieldError) as exc:
        PenfieldClient("tm_wrong_ak_x").request("GET", "/auth/verify")
    assert exc.value.status == 401
    assert "rejected the API key" in str(exc.value)
    assert "tm_wrong" not in str(exc.value)


def test_empty_key_rejected():
    with pytest.raises(PenfieldError):
        PenfieldClient("   ")


def test_expired_token_is_refreshed(api):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    c = PenfieldClient(KEY)
    c.request("GET", "/auth/verify")
    penfield_client._cache[c._fp].expires_at = time.time() + 10  # inside skew
    c.request("GET", "/auth/verify")
    assert any(x["url"].endswith("/auth/refresh") for x in api.calls)
    assert api.calls[-1]["headers"]["Authorization"] == "Bearer access-2"


def test_failed_refresh_falls_back_to_key_exchange(api):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    api.refresh_ok = False
    c = PenfieldClient(KEY)
    c.request("GET", "/auth/verify")
    penfield_client._cache[c._fp].expires_at = time.time()
    c.request("GET", "/auth/verify")
    assert len([x for x in api.calls if x["url"].endswith("/auth/token")]) == 2


def test_401_retries_once_with_new_token(api):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    c = PenfieldClient(KEY)
    c.request("GET", "/auth/verify")
    api.fail_next_401 = True
    assert c.request("GET", "/auth/verify") == {"valid": True}


def test_rate_limit_message(api, monkeypatch):
    api.routes[("GET", "/memories/" + MEM_ID)] = (
        429,
        {"status": "error", "error": {"code": "RATE"}},
    )
    with pytest.raises(PenfieldError) as exc:
        PenfieldClient(KEY).request("GET", f"/memories/{MEM_ID}")
    assert "rate limit" in str(exc.value).lower()


def test_field_errors_surface(api):
    api.routes[("POST", "/memories")] = (
        422,
        {
            "status": "error",
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid input",
                "field_errors": {"content": ["too long"]},
            },
        },
    )
    with pytest.raises(PenfieldError) as exc:
        PenfieldClient(KEY).request("POST", "/memories", json={"content": "x"})
    assert "content: too long" in str(exc.value)


def _raise(exc_type):
    def boom(*args, **kwargs):
        url = args[1] if len(args) > 1 else args[0]
        raise exc_type("nope", request=httpx.Request("POST", url))

    return boom


@pytest.mark.parametrize(
    ("exc_type", "needle"),
    [
        (httpx.ConnectError, "Could not reach Penfield: ConnectError"),
        (httpx.ConnectTimeout, "Penfield did not respond in time. Try again."),
        (httpx.ReadTimeout, "Penfield did not respond in time. Try again."),
    ],
)
def test_network_failure_during_key_exchange(api, monkeypatch, exc_type, needle):
    """No session yet: the failure happens in _exchange, before any request."""
    monkeypatch.setattr(penfield_client.httpx, "post", _raise(exc_type))
    with pytest.raises(PenfieldError) as exc:
        PenfieldClient(KEY).request("GET", "/auth/verify")
    assert str(exc.value) == needle or needle in str(exc.value)
    assert not [c for c in api.calls if c["method"] == "GET"]
    # Through a tool: one text and one JSON result, no exception.
    out = call("fetch", {"id": MEM_ID})
    assert out[0] == ("text", f"Penfield error: {exc.value}")
    assert out[1][1]["success"] is False and out[1][1]["status"] is None


@pytest.mark.parametrize("exc_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_network_failure_during_refresh(api, monkeypatch, exc_type):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    c = PenfieldClient(KEY)
    c.request("GET", "/auth/verify")
    penfield_client._cache[c._fp].expires_at = time.time()  # needs renewing
    monkeypatch.setattr(penfield_client.httpx, "post", _raise(exc_type))
    with pytest.raises(PenfieldError) as exc:
        c.request("GET", "/auth/verify")
    assert "Penfield" in str(exc.value)
    out = call("fetch", {"id": MEM_ID})
    assert out[0][1].startswith("Penfield error: ") and out[1][1]["success"] is False


def _session_then_fail(api, monkeypatch, exc_type):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    PenfieldClient(KEY).request("GET", "/auth/verify")
    monkeypatch.setattr(penfield_client.httpx, "request", _raise(exc_type))


@pytest.mark.parametrize(
    ("path", "what"),
    [("/memories", "memory"), ("/relationships", "relationship"), ("/artifacts", "file")],
)
def test_write_that_times_out_says_outcome_unknown(api, monkeypatch, path, what):
    _session_then_fail(api, monkeypatch, httpx.ReadTimeout)
    with pytest.raises(PenfieldError) as exc:
        PenfieldClient(KEY).request("POST", path, json={"x": 1})
    assert str(exc.value) == (
        f"Penfield did not answer, so it is not known whether the {what} was saved. "
        "Check before trying again, so that it is not saved twice."
    )


def test_store_that_times_out_says_outcome_unknown(api, monkeypatch):
    _session_then_fail(api, monkeypatch, httpx.ReadTimeout)
    out = call("store", {"content": "x y"})
    assert "not known whether the memory was saved" in out[0][1]
    assert out[1][1]["success"] is False


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/memories"),
        ("POST", "/search/hybrid"),
        ("PUT", f"/memories/{MEM_ID}"),
        ("DELETE", "/artifacts"),
    ],
)
def test_read_or_repeatable_call_that_times_out_says_try_again(api, monkeypatch, method, path):
    _session_then_fail(api, monkeypatch, httpx.ReadTimeout)
    with pytest.raises(PenfieldError) as exc:
        PenfieldClient(KEY).request(method, path)
    assert str(exc.value) == "Penfield did not respond in time. Try again."


@pytest.mark.parametrize("exc_type", [httpx.ConnectError, httpx.ConnectTimeout])
def test_write_that_never_left_says_try_again(api, monkeypatch, exc_type):
    """A connection that never opened cannot have saved anything."""
    _session_then_fail(api, monkeypatch, exc_type)
    with pytest.raises(PenfieldError) as exc:
        PenfieldClient(KEY).request("POST", "/memories", json={"content": "x"})
    assert "not known" not in str(exc.value)


def test_slow_sign_in_does_not_block_other_keys(api, monkeypatch):
    import threading

    other_key = "tm_other_ak_secret456"
    stalled = threading.Event()
    release = threading.Event()
    real_post = api.post

    def post(url, headers=None, **kw):
        if headers and headers.get("Authorization") == f"Bearer {other_key}":
            stalled.set()
            release.wait(5)
            headers = {**headers, "Authorization": f"Bearer {KEY}"}  # let it succeed
        return real_post(url, headers=headers, **kw)

    monkeypatch.setattr(penfield_client.httpx, "post", post)
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    slow = threading.Thread(target=lambda: PenfieldClient(other_key).request("GET", "/auth/verify"))
    slow.start()
    assert stalled.wait(5)
    done = threading.Event()
    fast = threading.Thread(
        target=lambda: (PenfieldClient(KEY).request("GET", "/auth/verify"), done.set())
    )
    fast.start()
    try:
        assert done.wait(3), "a stalled sign-in for one key blocked another key"
    finally:
        release.set()
        slow.join(5)
        fast.join(5)


def test_concurrent_calls_sign_in_once(api, monkeypatch):
    import threading

    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    threads = [
        threading.Thread(target=lambda: PenfieldClient(KEY).request("GET", "/auth/verify"))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len([x for x in api.calls if x["url"].endswith("/auth/token")]) == 1


def test_only_penfield_host_is_called(api):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    PenfieldClient(KEY).request("GET", "/auth/verify")
    assert all(c["url"].startswith("https://api.penfield.app/api/v2/") for c in api.calls)


# --------------------------------------------------------------- provider


def test_provider_validates(api):
    from dify_plugin.errors.tool import ToolProviderCredentialValidationError

    from provider.penfield import PenfieldProvider

    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    PenfieldProvider()._validate_credentials({"penfield_api_key": KEY})
    with pytest.raises(ToolProviderCredentialValidationError):
        PenfieldProvider()._validate_credentials({"penfield_api_key": "tm_bad_ak_1"})
    with pytest.raises(ToolProviderCredentialValidationError):
        PenfieldProvider()._validate_credentials({})


# ------------------------------------------------------------------ tools

import importlib  # noqa: E402

import penfield_ops  # noqa: E402

MCP_TOOLS = {
    "awaken": [],
    "store": ["content", "tags", "importance"],
    "recall": ["query", "source_type", "tags", "start_date", "end_date", "limit"],
    "search": ["query", "limit"],
    "fetch": ["id"],
    "update_memory": ["memory_id", "content", "importance", "tags"],
    "connect": ["from_memory", "to_memory", "relationship_type", "strength"],
    "disconnect": ["from_memory", "to_memory"],
    "explore": ["start_memory", "max_depth", "relationship_types"],
    "reflect": ["time_window", "include_documents", "start_date", "end_date"],
    "save_context": ["name", "description"],
    "restore_context": ["name", "limit"],
    "list_contexts": ["limit", "offset", "name_pattern", "include_descriptions"],
    "save_artifact": ["content", "path"],
    "retrieve_artifact": ["path"],
    "list_artifacts": ["path_prefix", "name_pattern"],
    "delete_artifact": ["path"],
}
"""Penfield's MCP tools and their parameters (``include_inactive`` aside, which
the REST search does not take). The plugin offers exactly these."""


def tool(name: str):
    module = importlib.import_module(f"tools.{name}")
    from dify_plugin import Tool

    classes = [
        v
        for v in vars(module).values()
        if isinstance(v, type) and v is not Tool and issubclass(v, Tool)
    ]
    assert len(classes) == 1, name
    return classes[0]


def call(name: str, params: dict[str, Any]) -> list[Any]:
    return run_tool(tool(name), params)


def result(name: str, params: dict[str, Any]) -> dict[str, Any]:
    out = call(name, params)
    assert out[0][0] == "text" and out[-1][0] == "json", out
    return out[-1][1]


def error(name: str, params: dict[str, Any]) -> str:
    """A refused call: the agent's text, and JSON a workflow can branch on."""
    out = call(name, params)
    assert [kind for kind, _ in out] == ["text", "json"], out
    assert out[0][1].startswith("Penfield error: "), out
    data = out[1][1]
    assert data["success"] is False and set(data) == {"success", "error", "error_code", "status"}
    assert out[0][1] == f"Penfield error: {data['error']}"
    return out[0][1]


MEMORY = {
    "id": MEM_ID,
    "content": "Tim prefers MIT for plugins",
    "memory_type": "fact",
    "importance": 0.7,
    "confidence": 0.8,
    "tags": ["licensing"],
    "source_type": "direct_input",
    "user_id": "internal",
    "embedding_status": "pending",
    "created_at": "2026-11-21T00:00:00Z",
    "updated_at": "2026-11-21T00:00:00Z",
}


def last(api, method: str, path: str) -> dict[str, Any]:
    calls = [c for c in api.calls if c["method"] == method and c["url"].endswith("/api/v2" + path)]
    assert calls, (method, path, [c["url"] for c in api.calls])
    return calls[-1]


def test_awaken(api):
    api.routes[("GET", "/personality/awakening")] = ok({"briefing": "=== AWAKENING BRIEFING ==="})
    out = call("awaken", {})
    assert out[0] == ("text", '{"briefing": "=== AWAKENING BRIEFING ==="}')
    assert out[1] == ("json", {"briefing": "=== AWAKENING BRIEFING ==="})


def test_store(api):
    api.routes[("POST", "/memories")] = (201, {"status": "success", "data": MEMORY})
    data = result(
        "store", {"content": "  Tim prefers MIT  ", "tags": "licensing, plugins", "importance": 0.7}
    )
    sent = last(api, "POST", "/memories")["json"]
    assert sent == {
        "content": "Tim prefers MIT",
        "tags": ["licensing", "plugins"],
        "importance": 0.7,
    }
    assert data["stored"] is True and data["memory"]["id"] == MEM_ID
    assert "user_id" not in data["memory"] and "embedding_status" not in data["memory"]


def test_store_sends_only_what_was_given(api):
    api.routes[("POST", "/memories")] = (201, {"status": "success", "data": MEMORY})
    result("store", {"content": "x y", "tags": "", "importance": None})
    assert last(api, "POST", "/memories")["json"] == {"content": "x y"}


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({}, "content is required"),
        ({"content": "x" * 10_001}, "at most 10,000"),
        ({"content": "x", "importance": 2}, "between 0 and 1"),
        ({"content": "x", "importance": "high"}, "number between 0 and 1"),
        ({"content": "x", "tags": "ok,!"}, "tag 2 ('!') would not be stored"),
        ({"content": "x", "tags": "a" * 51}, "too long"),
        ({"content": "x", "tags": ",".join(f"t{i}" for i in range(11))}, "at most 10"),
    ],
)
def test_store_validation(api, params, needle):
    assert needle in error("store", params)
    assert not [c for c in api.calls if c["url"].endswith("/memories")]


HIT = {
    "id": MEM_ID,
    "content": "Tim prefers MIT",
    "snippet": "Tim prefers",
    "score": 0.81,
    "score_breakdown": {"final_score": 0.81},
    "metadata": {"memory_type": "fact", "importance": 0.7},
    "source_type": "direct_input",
    "created_at": "2026-11-21T00:00:00Z",
    "tags": ["licensing"],
    "content_with_context": None,
}
DOC_HIT = {
    "id": MEM_ID2,
    "content": "chunk",
    "content_with_context": "before chunk after",
    "score": 0.5,
    "metadata": {"memory_type": "reference"},
    "source_type": "document_upload",
    "chunk_index": 2,
    "total_chunks": 9,
    "created_at": "2026-11-20T00:00:00Z",
    "tags": [],
}


def test_recall(api):
    api.routes[("POST", "/search/hybrid")] = ok({"items": [HIT, DOC_HIT]})
    out = call(
        "recall",
        {
            "query": "license",
            "limit": 5,
            "tags": "licensing,plugins",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31T23:59:59Z",
        },
    )
    sent = last(api, "POST", "/search/hybrid")["json"]
    assert sent == {
        "query": "license",
        "limit": 5,
        "tags": "licensing,plugins",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31T23:59:59Z",
    }
    text, data = out[0][1], out[1][1]
    assert json.loads(text) == data
    assert data["count"] == 2
    first, doc = data["results"]
    assert first["memory_type"] == "fact" and first["importance"] == 0.7
    assert doc["content"] == "before chunk after" and doc["source_type"] == "document"
    assert doc["chunk_index"] == 2


def test_recall_source_types(api):
    api.routes[("POST", "/search/hybrid")] = ok({"items": [HIT]})
    result("recall", {"query": "q", "source_type": "document"})
    assert last(api, "POST", "/search/hybrid")["json"]["source_types"] == ["document_upload"]
    result("recall", {"query": "q", "source_type": "memory"})
    assert last(api, "POST", "/search/hybrid")["json"]["source_types"] == [
        "direct_input",
        "conversation",
        "api_import",
        "checkpoint",
    ]
    assert "source_type must be" in error("recall", {"query": "q", "source_type": "web"})


def test_recall_memory_filter_falls_back(api):
    """An empty answer with the memory filter is retried without it."""
    seen = []

    def handler(body, params):
        seen.append(body)
        return ok({"items": [] if "source_types" in body else [DOC_HIT, HIT]})

    api.routes[("POST", "/search/hybrid")] = handler
    data = result("recall", {"query": "q", "source_type": "memory", "limit": 4})
    assert len(seen) == 2 and seen[1]["limit"] == 12 and "source_types" not in seen[1]
    assert [r["id"] for r in data["results"]] == [MEM_ID]


def test_recall_caps_long_content(api):
    long_hit = {**HIT, "content": "x" * 2500}
    api.routes[("POST", "/search/hybrid")] = ok({"items": [long_hit]})
    data = result("recall", {"query": "q"})
    assert len(data["results"][0]["content"]) == 2000 and data["results"][0]["truncated"]
    assert '"truncated": true' in call("recall", {"query": "q"})[0][1]


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({}, "query is required"),
        ({"query": "x" * 4001}, "at most 4,000"),
        ({"query": "q", "limit": 0}, "between 1 and 100"),
        ({"query": "q", "limit": 2.5}, "whole number"),
        ({"query": "q", "start_date": "last week"}, "ISO 8601"),
    ],
)
def test_recall_validation(api, params, needle):
    assert needle in error("recall", params)


def test_recall_empty(api):
    api.routes[("POST", "/search/hybrid")] = ok({"items": []})
    out = call("recall", {"query": "nothing"})
    assert json.loads(out[0][1]) == {"query": "nothing", "count": 0, "results": []}
    assert last(api, "POST", "/search/hybrid")["json"] == {"query": "nothing", "limit": 10}


def test_search_ranks_by_meaning(api):
    api.routes[("POST", "/search/hybrid")] = ok({"items": [HIT]})
    data = result("search", {"query": "licensing choices", "limit": 3})
    sent = last(api, "POST", "/search/hybrid")["json"]
    assert sent == {
        "query": "licensing choices",
        "limit": 3,
        "bm25_weight": 0.1,
        "vector_weight": 0.7,
        "graph_weight": 0.2,
    }
    assert abs(sum(v for k, v in sent.items() if k.endswith("_weight")) - 1.0) < 1e-9
    assert data["count"] == 1


def test_fetch(api):
    api.routes[("GET", f"/memories/{MEM_ID}")] = ok(MEMORY)
    data = result("fetch", {"id": MEM_ID.upper()})
    assert data["memory"]["content"] == MEMORY["content"]
    assert "Not found" in error("fetch", {"id": MEM_ID2})


@pytest.mark.parametrize("bad", ["", "abc", MEM_ID + "0", "../memories"])
def test_ids_must_be_uuids(api, bad):
    assert "UUID" in error("fetch", {"id": bad}) or "required" in error("fetch", {"id": bad})
    assert not [c for c in api.calls if "/memories/" in c["url"]]


def test_update_memory(api):
    api.routes[("PUT", f"/memories/{MEM_ID}")] = ok({**MEMORY, "importance": 0.9})
    data = result("update_memory", {"memory_id": MEM_ID, "importance": 0.9, "tags": "a1,b2"})
    assert last(api, "PUT", f"/memories/{MEM_ID}")["json"] == {
        "importance": 0.9,
        "tags": ["a1", "b2"],
    }
    assert data["updated"] and data["memory"]["importance"] == 0.9
    assert "at least one of" in error("update_memory", {"memory_id": MEM_ID})


def test_connect(api):
    rel = {"id": "r1", "from_id": MEM_ID, "to_id": MEM_ID2, "relationship_type": "supports"}
    api.routes[("POST", "/relationships")] = (201, {"status": "success", "data": rel})
    data = result(
        "connect",
        {
            "from_memory": MEM_ID,
            "to_memory": MEM_ID2,
            "relationship_type": "supports",
            "strength": 0.6,
        },
    )
    assert last(api, "POST", "/relationships")["json"] == {
        "from_id": MEM_ID,
        "to_id": MEM_ID2,
        "relationship_type": "supports",
        "strength": 0.6,
    }
    assert data == {"connected": True, "relationship": rel}


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        (
            {"from_memory": MEM_ID, "to_memory": MEM_ID, "relationship_type": "supports"},
            "different",
        ),
        (
            {"from_memory": MEM_ID, "to_memory": MEM_ID2, "relationship_type": "likes"},
            "not one of the 24",
        ),
        ({"from_memory": MEM_ID, "to_memory": MEM_ID2}, "relationship_type is required"),
        (
            {
                "from_memory": MEM_ID,
                "to_memory": MEM_ID2,
                "relationship_type": "supports",
                "strength": 3,
            },
            "between 0 and 1",
        ),
    ],
)
def test_connect_validation(api, params, needle):
    assert needle in error("connect", params)


def test_disconnect(api):
    api.routes[("DELETE", "/relationships/between")] = (204, None)
    data = result("disconnect", {"from_memory": MEM_ID, "to_memory": MEM_ID2})
    assert last(api, "DELETE", "/relationships/between")["params"] == {
        "from_id": MEM_ID,
        "to_id": MEM_ID2,
    }
    assert data == {"disconnected": True, "from_memory": MEM_ID, "to_memory": MEM_ID2}


def test_disconnect_needs_delete_permission(api):
    api.routes[("DELETE", "/relationships/between")] = (
        403,
        {
            "status": "error",
            "error": {"code": "AUTH_INSUFFICIENT_PERMISSIONS", "message": "Missing scope: delete"},
        },
    )
    msg = error("disconnect", {"from_memory": MEM_ID, "to_memory": MEM_ID2})
    assert "Missing scope: delete" in msg and "403" in msg


def test_explore(api):
    api.routes[("POST", "/relationships/traverse")] = ok({"paths": [], "max_depth_reached": 0})
    data = result(
        "explore",
        {"start_memory": MEM_ID, "max_depth": 2, "relationship_types": "supports, causes"},
    )
    assert last(api, "POST", "/relationships/traverse")["json"] == {
        "start_memory_id": MEM_ID,
        "max_depth": 2,
        "relationship_types": ["supports", "causes"],
    }
    assert data["paths"] == []
    result("explore", {"start_memory": MEM_ID})
    assert last(api, "POST", "/relationships/traverse")["json"] == {
        "start_memory_id": MEM_ID,
        "max_depth": 3,
    }
    assert "Unknown relationship type: likes" in error(
        "explore", {"start_memory": MEM_ID, "relationship_types": "likes"}
    )
    assert "between 1 and 10" in error("explore", {"start_memory": MEM_ID, "max_depth": 11})


def test_reflect_windows(api):
    api.routes[("POST", "/analysis/reflect")] = ok({"themes": []})
    result("reflect", {})
    assert last(api, "POST", "/analysis/reflect")["json"] == {"time_window": "recent"}
    result("reflect", {"time_window": "week", "include_documents": True})
    assert last(api, "POST", "/analysis/reflect")["json"] == {
        "time_window": "week",
        "include_documents": True,
    }
    result("reflect", {"time_window": "7d"})
    sent = last(api, "POST", "/analysis/reflect")["json"]
    assert set(sent) == {"start_date"} and sent["start_date"].endswith("Z")
    result("reflect", {"time_window": "30d", "start_date": "2026-01-01"})
    assert last(api, "POST", "/analysis/reflect")["json"] == {"start_date": "2026-01-01"}
    assert "time_window must be one of" in error("reflect", {"time_window": "year"})


def test_artifacts(api):
    saved = {"success": True, "path": "/p/notes.md", "artifact_id": "a1", "size": 5}
    api.routes[("POST", "/artifacts")] = (201, {"status": "success", "data": saved})
    api.routes[("GET", "/artifacts")] = ok(
        {"success": True, "content": "# Hi", "path": "/p/notes.md"}
    )
    api.routes[("GET", "/artifacts/list")] = ok(
        {"success": True, "path": "/p/", "folders": [], "files": []}
    )
    api.routes[("DELETE", "/artifacts")] = ok({"message": "deleted"})

    assert result("save_artifact", {"path": "/p/notes.md", "content": "# Hi"})["saved"]
    assert last(api, "POST", "/artifacts")["json"] == {"path": "/p/notes.md", "content": "# Hi"}

    out = call("retrieve_artifact", {"path": "/p/notes.md"})
    assert json.loads(out[0][1])["content"] == "# Hi"
    assert last(api, "GET", "/artifacts")["params"] == {"path": "/p/notes.md"}

    result("list_artifacts", {})
    assert last(api, "GET", "/artifacts/list")["params"] == {"prefix": "/"}
    result("list_artifacts", {"path_prefix": "/p/", "name_pattern": "note"})
    assert last(api, "GET", "/artifacts/list")["params"] == {
        "prefix": "/p/",
        "name_pattern": "note",
    }

    assert result("delete_artifact", {"path": "/p/notes.md"}) == {
        "deleted": True,
        "path": "/p/notes.md",
    }
    assert last(api, "DELETE", "/artifacts")["params"] == {"path": "/p/notes.md"}


@pytest.mark.parametrize(
    ("name", "params", "needle"),
    [
        ("save_artifact", {"path": "notes.md", "content": "x"}, "must start with /"),
        ("save_artifact", {"path": "/p/", "content": "x"}, "end with a file name"),
        ("save_artifact", {"path": "/p/a.md"}, "content is required"),
        ("retrieve_artifact", {}, "path is required"),
        ("list_artifacts", {"path_prefix": "p"}, "must start with /"),
        ("delete_artifact", {"path": "x"}, "must start with /"),
    ],
)
def test_artifact_validation(api, name, params, needle):
    assert needle in error(name, params)


def test_artifact_conflict_is_explained(api):
    api.routes[("POST", "/artifacts")] = (
        409,
        {
            "status": "error",
            "error": {"code": "RES_CONFLICT", "message": "Artifact already exists"},
        },
    )
    assert "already exists" in error("save_artifact", {"path": "/a.md", "content": "x"})


# ------------------------------------------------------------- checkpoints


class Store:
    """Memories and checkpoints kept in memory, served through FakeAPI."""

    def __init__(self, api) -> None:
        self.memories: dict[str, dict[str, Any]] = {}
        self.n = 0
        self.created: list[dict[str, Any]] = []
        api.routes[("GET", "/memories")] = self.list
        api.routes[("POST", "/memories")] = self.create
        api.routes[("POST", "/search/hybrid")] = self.search
        self.api = api
        self.search_items: list[dict[str, Any]] = []
        self.search_bodies: list[dict[str, Any]] = []

    def add(self, memory_id: str, content: str = "m", memory_type: str = "fact") -> None:
        self.memories[memory_id] = {
            "id": memory_id,
            "content": content,
            "memory_type": memory_type,
            "tags": [],
        }
        self.api.routes[("GET", f"/memories/{memory_id}")] = ok(self.memories[memory_id])

    def checkpoint(
        self, name: str, ids: list[str] | None = None, refs: list[str] | None = None
    ) -> str:
        self.n += 1
        cid = f"00000000-0000-4000-8000-{self.n:012d}"
        content = json.dumps(
            {
                "checkpoint_name": name,
                "description": f"about {name}",
                "memory_count": len(ids or []),
                "memory_ids": ids or [],
                "referenced_memories": refs or [],
            }
        )
        self.memories[cid] = {
            "id": cid,
            "content": content,
            "memory_type": "checkpoint",
            "created_at": f"2026-11-{self.n:02d}",
        }
        self.api.routes[("GET", f"/memories/{cid}")] = ok(self.memories[cid])
        return cid

    def list(self, body, params):
        params = params or {}
        items = list(self.memories.values())
        if params.get("memory_type"):
            items = [m for m in items if m["memory_type"] == params["memory_type"]]
        items.reverse()  # newest first
        per_page = int(params.get("per_page", 20))
        page = int(params.get("page", 1))
        chunk = items[(page - 1) * per_page : page * per_page]
        return ok(
            {
                "items": chunk,
                "pagination": {
                    "page": page,
                    "has_next": page * per_page < len(items),
                    "total": len(items),
                },
            }
        )

    def create(self, body, params):
        self.created.append(body)
        new_id = f"11111111-0000-4000-8000-{len(self.created):012d}"
        return 201, {"status": "success", "data": {"id": new_id, **body}}

    def search(self, body, params):
        self.search_bodies.append(body)
        return ok({"items": self.search_items})


A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"
C = "cccccccc-0000-4000-8000-000000000003"


def test_save_context_matches_mcp_format(api):
    store = Store(api)
    store.add(A)
    store.add(C)
    store.search_items = [{"id": A, "score": 0.9}, {"id": B, "score": 0.2}]
    data = result(
        "save_context",
        {
            "name": "Auth bug 2026-11-21",
            "description": f"Found the auth bug. See memory_id: {C} and memory_id: {B.upper()}",
        },
    )
    assert store.search_bodies[0] == {
        "query": f"Found the auth bug. See memory_id: {C} and memory_id: {B.upper()}"[:200],
        "limit": 30,
        "min_score_threshold": 0.25,
    }
    body = store.created[0]
    assert body["memory_type"] == "checkpoint" and body["importance"] == 0.9
    assert "source_type" not in body, "Penfield sets the source itself, as for MCP checkpoints"
    assert body["metadata"] == {"checkpoint_name": "Auth bug 2026-11-21"}
    assert body["tags"] == ["checkpoint", "context", "Auth bug 2026-11-21"]
    saved = json.loads(body["content"])
    assert list(saved) == [
        "checkpoint_name",
        "description",
        "memory_count",
        "memory_ids",
        "referenced_memories",
    ]
    assert saved["memory_ids"] == [A] and saved["memory_count"] == 1
    assert saved["referenced_memories"] == [C]
    assert (
        data["success"] and data["references_extracted"] == 2 and data["references_resolved"] == 1
    )
    assert data["unresolved_references"] == [f"memory_id: {B}"]


def test_save_context_short_description_links_recent(api):
    store = Store(api)
    store.add(A)
    store.add(B)
    data = result("save_context", {"name": "wip", "description": "wip"})
    assert not store.search_bodies
    assert json.loads(store.created[0]["content"])["memory_ids"] == [B, A]
    assert data["memories_included"] == 2


def test_save_context_resolves_phrases(api):
    store = Store(api)
    store.search_items = [{"id": A, "score": 0.6}]
    data = result("save_context", {"name": "n1", "description": "x y z memory: 'the auth fix'"})
    assert data["references_resolved"] == 1 and data["referenced_memories"] == []  # already linked
    assert store.search_bodies[-1] == {
        "query": "the auth fix",
        "limit": 1,
        "min_score_threshold": 0.25,
    }


def test_save_context_refuses_duplicate_name(api):
    store = Store(api)
    store.checkpoint("taken")
    out = call("save_context", {"name": "taken", "description": "one two three"})
    assert out[1][1]["error_code"] == "DUPLICATE_NAME"
    assert "already exists" in out[0][1] and not store.created


def test_save_context_name_that_cannot_be_a_tag(api):
    store = Store(api)
    result("save_context", {"name": "!!", "description": "a"})
    assert store.created[0]["tags"] == ["checkpoint", "context"]


def test_restore_context(api):
    store = Store(api)
    store.add(A, "first")
    store.add(B, "second")
    gone = "dddddddd-0000-4000-8000-000000000004"
    cid = store.checkpoint("handoff", ids=[A, gone], refs=[B])
    out = call("restore_context", {"name": "handoff"})
    data = out[1][1]
    assert data["context_id"] == cid and data["description"] == "about handoff"
    assert [m["id"] for m in data["memories"]] == [A, B]
    assert data["saved_count"] == 3 and data["count"] == 2 and not data["truncated"]
    assert json.loads(out[0][1]) == data
    assert result("restore_context", {"name": cid})["context_id"] == cid
    limited = result("restore_context", {"name": "handoff", "limit": 1})
    assert limited["count"] == 1 and limited["truncated"]


def test_restore_context_is_exact_and_reports_missing(api):
    store = Store(api)
    store.checkpoint("Handoff")
    out = call("restore_context", {"name": "handoff"})
    assert out[1][1] == {"success": False, "error": "No checkpoint found with name 'handoff'"}


def test_restore_context_by_id_ignores_other_memories(api):
    store = Store(api)
    store.add(A)
    assert result("restore_context", {"name": A})["success"] is False


def test_restore_awakening(api):
    Store(api)
    api.routes[("GET", "/personality/awakening")] = ok({"briefing": "brief"})
    out = call("restore_context", {"name": "Awakening"})
    assert json.loads(out[0][1]) == {"name": "awakening", "briefing": "brief"}


def test_list_contexts(api):
    store = Store(api)
    for i in range(3):
        store.checkpoint(f"sprint {i}")
    store.checkpoint("Release")
    store.add(A)
    data = result("list_contexts", {"limit": 2})
    assert [c["name"] for c in data["contexts"]] == ["Release", "sprint 2"]
    assert data["total"] == 4 and data["has_more"]
    assert "description" not in data["contexts"][0]
    data = result(
        "list_contexts", {"name_pattern": "SPRINT", "offset": 1, "include_descriptions": True}
    )
    assert [c["name"] for c in data["contexts"]] == ["sprint 1", "sprint 0"]
    assert data["total"] == 3 and data["contexts"][0]["description"] == "about sprint 1"


def test_list_contexts_walks_every_page(api):
    store = Store(api)
    for i in range(205):
        store.checkpoint(f"c{i}")
    data = result("list_contexts", {"name_pattern": "c0"})
    assert data["total"] == 1
    pages = [c["params"]["page"] for c in api.calls if c["url"].endswith("/memories")]
    assert pages == [1, 2, 3]


def test_tool_errors_never_leak_the_key(api):
    msgs = [error("store", {"content": "x", "importance": 5})]
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    for m in msgs:
        assert KEY not in m


# --------------------------------------------------------------- packaging


def test_every_tool_yaml_points_at_existing_source():
    import yaml

    provider = yaml.safe_load((ROOT / "provider/penfield.yaml").read_text())
    for rel in provider["tools"]:
        spec = yaml.safe_load((ROOT / rel).read_text())
        assert (ROOT / spec["extra"]["python"]["source"]).is_file()
        assert spec["identity"]["name"] == Path(rel).stem
        assert spec["description"]["llm"]
        for param in spec["parameters"]:
            assert param["label"]["en_US"] and param["human_description"]["en_US"]
            if param["form"] == "llm":
                assert param.get("llm_description"), (rel, param["name"])


def test_versions_match():
    import yaml

    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    assert penfield_client.USER_AGENT.endswith("/" + manifest["version"])


def test_non_json_success_is_reported(api):
    api.routes[("GET", "/auth/verify")] = ok({"valid": True})
    c = PenfieldClient(KEY)
    c.request("GET", "/auth/verify")
    orig = api._resp

    def html(status, body, method, url):
        r = orig(status, body, method, url)
        return httpx.Response(200, content=b"<html>", request=r.request)

    api._resp = html
    with pytest.raises(PenfieldError) as exc:
        c.request("GET", "/auth/verify")
    assert "not JSON" in str(exc.value)


def test_malformed_token_response(api, monkeypatch):
    def bad_post(url, **kw):
        return httpx.Response(
            200, content=b'{"status":"success"}', request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(penfield_client.httpx, "post", bad_post)
    with pytest.raises(PenfieldError) as exc:
        PenfieldClient(KEY).request("GET", "/auth/verify")
    assert "unexpected sign-in response" in str(exc.value)


def test_plugin_boots_like_marketplace_ci(tmp_path):
    """Start the plugin the way the Marketplace CI does. This loads every YAML
    through the SDK's own schema models and resolves every tool class."""
    import os
    import socket
    import subprocess
    import time as _time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "INSTALL_METHOD": "serverless", "SERVERLESS_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "main"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = _time.time() + 30
        lines = []
        while _time.time() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            lines.append(line)
            if "Serving Flask app" in line:
                break
        assert any("Serving Flask app" in x for x in lines), "".join(lines)[-3000:]
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _dify_agent_observation(messages: list[Any]) -> str:
    """What Dify 1.20 shows an agent for a tool call (ToolEngine.tool_response_to_str)."""

    def norm(text: str) -> str:
        try:
            return json.dumps(
                json.loads(text), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        except ValueError:
            return text

    texts = [m for kind, m in messages if kind == "text"]
    jsons = [json.dumps(m, ensure_ascii=False) for kind, m in messages if kind == "json"]
    seen = {norm(t) for t in texts}
    return "".join(texts + [j for j in jsons if norm(j) not in seen])


@pytest.mark.parametrize("operation", ["store", "recall", "awaken", "save_artifact"])
def test_agent_reads_each_result_once(api, operation):
    api.routes[("POST", "/memories")] = (201, {"status": "success", "data": MEMORY})
    api.routes[("POST", "/search/hybrid")] = ok({"items": [HIT, DOC_HIT]})
    api.routes[("GET", "/personality/awakening")] = ok({"briefing": "brief\nline two"})
    api.routes[("POST", "/artifacts")] = (201, {"status": "success", "data": {"path": "/a.md"}})
    params = {
        "store": {"content": "x y"},
        "recall": {"query": "q"},
        "awaken": {},
        "save_artifact": {"path": "/a.md", "content": "x"},
    }[operation]
    out = call(operation, params)
    text, data = out[0][1], out[1][1]
    assert json.loads(text) == data
    assert _dify_agent_observation(out) == text


def test_agent_reads_errors_once_with_their_detail(api):
    out = call("store", {"content": "x", "tags": "ok,!"})
    observation = _dify_agent_observation(out)
    assert observation.startswith("Penfield error: tag 2") and '"success": false' in observation


#: Tool provider tags that Dify 1.7.0, the declared minimum, accepts. Later
#: versions added more (such as "rag"); a tag outside this list makes 1.7.0
#: refuse to install the plugin.
DIFY_1_7_PROVIDER_TAGS = {
    "search",
    "image",
    "videos",
    "weather",
    "finance",
    "design",
    "travel",
    "social",
    "news",
    "medical",
    "productivity",
    "education",
    "business",
    "entertainment",
    "utilities",
    "other",
}


def test_provider_tags_install_on_the_minimum_dify_version():
    import yaml

    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    assert manifest["meta"]["minimum_dify_version"] == "1.7.0"
    provider = yaml.safe_load((ROOT / "provider/penfield.yaml").read_text())
    assert set(provider["identity"]["tags"]) <= DIFY_1_7_PROVIDER_TAGS


def test_tools_match_penfield_mcp():
    import yaml

    provider = yaml.safe_load((ROOT / "provider/penfield.yaml").read_text())
    names = [Path(rel).stem for rel in provider["tools"]]
    assert names == list(MCP_TOOLS)
    assert set(penfield_ops.OPERATIONS) == set(MCP_TOOLS)
    for name, params in MCP_TOOLS.items():
        spec = yaml.safe_load((ROOT / f"tools/{name}.yaml").read_text())
        assert sorted(p["name"] for p in spec["parameters"]) == sorted(params), name


def test_generated_tool_files_are_current():
    sys.path.insert(0, str(ROOT / "scripts"))
    import gen_tools

    for rel, text in gen_tools.expected_files().items():
        assert (ROOT / rel).read_text(encoding="utf-8") == text, f"{rel}: run scripts/gen_tools.py"
    on_disk = {p.name for p in (ROOT / "tools").glob("*.yaml")}
    assert on_disk == {f"{n}.yaml" for n in MCP_TOOLS}
