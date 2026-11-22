# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penfield
"""The 17 Penfield tools, over the Penfield REST API.

Each operation takes a ``PenfieldClient`` and the tool's arguments, already
checked and converted, and returns a JSON-ready dict. Tool and parameter
names are those of Penfield's MCP server, so an agent moving between
clients sees the same tools.

The behaviour follows Penfield's reference REST client (hermes-penfield):
the same search weights, tag rules and checkpoint format, so a checkpoint
saved here restores in every other Penfield client and the other way
round.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from penfield_client import RELATIONSHIP_TYPES, PenfieldClient, PenfieldError

# --- limits and enums ------------------------------------------------------

CONTENT_MAX_CHARS = 10_000
QUERY_MAX_CHARS = 4_000
TAGS_PER_MEMORY = 10
TAG_MIN_CHARS = 2
TAG_MAX_CHARS = 50

#: Longest memory content a search or restore result carries per memory.
#: Longer content is cut and flagged; ``fetch`` returns the whole memory.
RESULT_CONTENT_MAX_CHARS = 2_000

DOCUMENT_SOURCE_TYPE = "document_upload"
MEMORY_SOURCE_TYPES = ("direct_input", "conversation", "api_import", "checkpoint")

#: ``search`` ranks by meaning; ``recall`` keeps the API's default balance.
SEMANTIC_SEARCH_WEIGHTS = {"bm25_weight": 0.1, "vector_weight": 0.7, "graph_weight": 0.2}

REFLECT_WINDOWS = ("recent", "today", "week", "month", "1d", "7d", "30d", "90d")
_DAY_WINDOWS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}

# Checkpoints (the format of Penfield's MCP server).
_LINK_SCORE = 0.25
_MIN_QUERY_WORDS = 3
_RECENT_FALLBACK = 20
_PAGE_SIZE = 100
_MAX_CHECKPOINT_PAGES = 100
_FETCH_WORKERS = 5

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_MEMORY_ID_REF = re.compile(r"memory_id:\s*([0-9a-f-]{36})", re.IGNORECASE)
_MEMORY_PHRASE_REF = re.compile(r"""memory:\s*["']([^"']+)["']""", re.IGNORECASE)


# --- argument helpers -------------------------------------------------------


def text_arg(args: dict[str, Any], name: str, *, required: bool = False, max_chars: int = 0) -> str:
    value = args.get(name)
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise PenfieldError(f"{name} is required.")
    if max_chars and len(text) > max_chars:
        raise PenfieldError(f"{name} must be at most {max_chars:,} characters.")
    return text


def uuid_arg(args: dict[str, Any], name: str) -> str:
    text = text_arg(args, name, required=True)
    if not UUID_RE.match(text):
        raise PenfieldError(f"{name} must be a memory id (a UUID), got {text[:60]!r}.")
    return text.lower()


def int_arg(args: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = args.get(name)
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PenfieldError(f"{name} must be a whole number.") from exc
    if number != int(number):
        raise PenfieldError(f"{name} must be a whole number.")
    if not low <= number <= high:
        raise PenfieldError(f"{name} must be between {low} and {high}.")
    return int(number)


def unit_arg(args: dict[str, Any], name: str) -> float | None:
    """A number from 0 to 1, or None when not given."""
    value = args.get(name)
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PenfieldError(f"{name} must be a number between 0 and 1.") from exc
    if not 0.0 <= number <= 1.0:
        raise PenfieldError(f"{name} must be between 0 and 1.")
    return number


def bool_arg(args: dict[str, Any], name: str, default: bool = False) -> bool:
    value = args.get(name)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    raise PenfieldError(f"{name} must be true or false.")


def list_arg(args: dict[str, Any], name: str) -> list[str]:
    """A list given as a list or as comma-separated text. Blank items are dropped."""
    value = args.get(name)
    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def date_arg(args: dict[str, Any], name: str) -> str | None:
    text = text_arg(args, name)
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PenfieldError(f"{name} must be an ISO 8601 date, for example 2026-01-31.") from exc
    return text


# --- tags -------------------------------------------------------------------


def stored_tag(tag: str) -> str:
    """Return ``tag`` the way Penfield stores it."""
    text = re.sub(r"[ _]", "-", tag.strip().lower())
    text = re.sub(r"[^a-z0-9-]", "", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def tag_problem(tag: str) -> str:
    """Say why Penfield would not store ``tag``, or return "" if it would."""
    kept = len(stored_tag(tag))
    if kept > TAG_MAX_CHARS:
        return f"is too long: {kept} characters, and a tag can have at most {TAG_MAX_CHARS}"
    if kept < TAG_MIN_CHARS:
        return (
            "would not be stored: a tag keeps its letters a to z, its digits and its "
            f"hyphens, and at least {TAG_MIN_CHARS} characters have to remain"
        )
    return ""


def tags_arg(args: dict[str, Any]) -> list[str]:
    """Tags for a write, refusing what Penfield would not store."""
    tags = list_arg(args, "tags")
    for position, tag in enumerate(tags, 1):
        problem = tag_problem(tag)
        if problem:
            shown = tag if len(tag) <= 20 else f"{tag[:20]}..."
            raise PenfieldError(f"tag {position} ({shown!r}) {problem}.")
    if len(tags) > TAGS_PER_MEMORY:
        raise PenfieldError(
            f"{len(tags)} tags were given and a memory can have at most {TAGS_PER_MEMORY}."
        )
    return tags


# --- shaping ----------------------------------------------------------------


def memory_view(memory: dict[str, Any], *, max_chars: int = 0) -> dict[str, Any]:
    """One memory for the agent or the workflow, without internals.

    Accepts a search hit (fields under ``metadata``) and a memory record
    (fields at the top level). With ``max_chars``, longer content is cut
    and flagged.
    """
    raw_meta = memory.get("metadata")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    is_document = memory.get("source_type") == DOCUMENT_SOURCE_TYPE
    content = str(
        (memory.get("content_with_context") if is_document else None)
        or memory.get("content")
        or memory.get("snippet")
        or ""
    )

    def field(name: str) -> Any:
        value = memory.get(name)
        return value if value is not None else meta.get(name)

    view: dict[str, Any] = {
        "id": memory.get("id"),
        "content": content[:max_chars] if max_chars else content,
        "memory_type": field("memory_type"),
        "importance": field("importance"),
        "tags": field("tags") or [],
        "created_at": field("created_at"),
    }
    if field("updated_at") is not None:
        view["updated_at"] = field("updated_at")
    if is_document:
        view["source_type"] = "document"
        for key in ("filename", "document_id", "chunk_index", "total_chunks"):
            if field(key) is not None:
                view[key] = field(key)
    if "score" in memory:
        view["score"] = memory["score"]
    if max_chars and len(content) > max_chars:
        view["truncated"] = True
    return view


def _items(response: Any) -> list[dict[str, Any]]:
    items = response.get("items", []) if isinstance(response, dict) else []
    return [it for it in items if isinstance(it, dict)]


# --- the tools --------------------------------------------------------------


def awaken(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    data = client.request("GET", "/personality/awakening")
    briefing = data.get("briefing", "") if isinstance(data, dict) else ""
    return {"briefing": briefing}


def store(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "content": text_arg(args, "content", required=True, max_chars=CONTENT_MAX_CHARS)
    }
    tags = tags_arg(args)
    if tags:
        body["tags"] = tags
    importance = unit_arg(args, "importance")
    if importance is not None:
        body["importance"] = importance
    created = client.request("POST", "/memories", json=body)
    return {"stored": True, "memory": memory_view(created if isinstance(created, dict) else {})}


def _source_types(source_type: str) -> list[str]:
    if source_type == "document":
        return [DOCUMENT_SOURCE_TYPE]
    if source_type == "memory":
        return list(MEMORY_SOURCE_TYPES)
    raise PenfieldError(
        "source_type must be 'memory' or 'document'; leave it empty to search both."
    )


def _search(
    client: PenfieldClient, body: dict[str, Any], wanted: int, memories_only: bool
) -> list[dict[str, Any]]:
    items = _items(client.request("POST", "/search/hybrid", json=body))
    if not items and memories_only:
        # An empty answer can also mean the API stopped accepting one of the
        # source types sent. Search again without them, leaving documents out.
        retry = {k: v for k, v in body.items() if k != "source_types"}
        retry["limit"] = min(wanted * 3, _PAGE_SIZE)
        found = _items(client.request("POST", "/search/hybrid", json=retry))
        items = [it for it in found if it.get("source_type") != DOCUMENT_SOURCE_TYPE][:wanted]
    return items


def recall(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    query = text_arg(args, "query", required=True, max_chars=QUERY_MAX_CHARS)
    limit = int_arg(args, "limit", 10, 1, 100)
    body: dict[str, Any] = {"query": query, "limit": limit}
    source_type = text_arg(args, "source_type").lower()
    if source_type:
        body["source_types"] = _source_types(source_type)
    tags = list_arg(args, "tags")
    if tags:
        body["tags"] = ",".join(tags)
    for key in ("start_date", "end_date"):
        value = date_arg(args, key)
        if value:
            body[key] = value
    items = _search(client, body, limit, source_type == "memory")
    results = [memory_view(it, max_chars=RESULT_CONTENT_MAX_CHARS) for it in items]
    return {"query": query, "count": len(results), "results": results}


def search(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    query = text_arg(args, "query", required=True, max_chars=QUERY_MAX_CHARS)
    limit = int_arg(args, "limit", 10, 1, 100)
    body = {"query": query, "limit": limit, **SEMANTIC_SEARCH_WEIGHTS}
    items = _search(client, body, limit, False)
    results = [memory_view(it, max_chars=RESULT_CONTENT_MAX_CHARS) for it in items]
    return {"query": query, "count": len(results), "results": results}


def fetch(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    memory_id = uuid_arg(args, "id")
    found = client.request("GET", f"/memories/{memory_id}")
    return {"memory": memory_view(found if isinstance(found, dict) else {})}


def update_memory(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    memory_id = uuid_arg(args, "memory_id")
    body: dict[str, Any] = {}
    content = text_arg(args, "content", max_chars=CONTENT_MAX_CHARS)
    if content:
        body["content"] = content
    importance = unit_arg(args, "importance")
    if importance is not None:
        body["importance"] = importance
    tags = tags_arg(args)
    if tags:
        body["tags"] = tags
    if not body:
        raise PenfieldError("Give at least one of content, importance or tags to change.")
    updated = client.request("PUT", f"/memories/{memory_id}", json=body)
    return {"updated": True, "memory": memory_view(updated if isinstance(updated, dict) else {})}


def connect(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    from_id = uuid_arg(args, "from_memory")
    to_id = uuid_arg(args, "to_memory")
    if from_id == to_id:
        raise PenfieldError("from_memory and to_memory must be different memories.")
    rtype = text_arg(args, "relationship_type", required=True)
    if rtype not in RELATIONSHIP_TYPES:
        raise PenfieldError(
            f"relationship_type {rtype!r} is not one of the {len(RELATIONSHIP_TYPES)} "
            f"types: {', '.join(RELATIONSHIP_TYPES)}."
        )
    body: dict[str, Any] = {"from_id": from_id, "to_id": to_id, "relationship_type": rtype}
    strength = unit_arg(args, "strength")
    if strength is not None:
        body["strength"] = strength
    created = client.request("POST", "/relationships", json=body)
    return {"connected": True, "relationship": created}


def disconnect(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    from_id = uuid_arg(args, "from_memory")
    to_id = uuid_arg(args, "to_memory")
    client.request("DELETE", "/relationships/between", params={"from_id": from_id, "to_id": to_id})
    return {"disconnected": True, "from_memory": from_id, "to_memory": to_id}


def explore(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "start_memory_id": uuid_arg(args, "start_memory"),
        "max_depth": int_arg(args, "max_depth", 3, 1, 10),
    }
    types = list_arg(args, "relationship_types")
    unknown = [t for t in types if t not in RELATIONSHIP_TYPES]
    if unknown:
        raise PenfieldError(f"Unknown relationship type: {', '.join(unknown)}.")
    if types:
        body["relationship_types"] = types
    data = client.request("POST", "/relationships/traverse", json=body)
    return data if isinstance(data, dict) else {"result": data}


def reflect(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    window = text_arg(args, "time_window") or "recent"
    if window not in REFLECT_WINDOWS:
        raise PenfieldError(f"time_window must be one of {', '.join(REFLECT_WINDOWS)}.")
    body: dict[str, Any] = {}
    if args.get("include_documents") not in (None, ""):
        body["include_documents"] = bool_arg(args, "include_documents")
    for key in ("start_date", "end_date"):
        value = date_arg(args, key)
        if value:
            body[key] = value
    if window in _DAY_WINDOWS:
        # Explicit dates override any window, so only fill in a missing start.
        if "start_date" not in body and "end_date" not in body:
            since = datetime.now(UTC) - timedelta(days=_DAY_WINDOWS[window])
            body["start_date"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        body["time_window"] = window
    data = client.request("POST", "/analysis/reflect", json=body)
    return data if isinstance(data, dict) else {"result": data}


def _artifact_path(args: dict[str, Any], name: str = "path") -> str:
    path = text_arg(args, name, required=True)
    if not path.startswith("/"):
        raise PenfieldError(f"{name} must start with /, for example /project/notes.md.")
    return path


def save_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    path = _artifact_path(args)
    if path.endswith("/"):
        raise PenfieldError("path must end with a file name, for example /project/notes.md.")
    content = args.get("content")
    if content is None or str(content) == "":
        raise PenfieldError("content is required.")
    data = client.request("POST", "/artifacts", json={"path": path, "content": str(content)})
    return {"saved": True, "path": path, "artifact": data}


def retrieve_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    path = _artifact_path(args)
    data = client.request("GET", "/artifacts", params={"path": path})
    return data if isinstance(data, dict) else {"path": path, "content": data}


def list_artifacts(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    prefix = text_arg(args, "path_prefix") or "/"
    if not prefix.startswith("/"):
        raise PenfieldError("path_prefix must start with /.")
    params: dict[str, Any] = {"prefix": prefix}
    pattern = text_arg(args, "name_pattern")
    if pattern:
        params["name_pattern"] = pattern
    data = client.request("GET", "/artifacts/list", params=params)
    return data if isinstance(data, dict) else {"items": data}


def delete_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    path = _artifact_path(args)
    client.request("DELETE", "/artifacts", params={"path": path})
    return {"deleted": True, "path": path}


# --- context checkpoints ----------------------------------------------------


def _checkpoint_payload(item: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(item.get("content") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iter_checkpoints(client: PenfieldClient) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield ``(memory, payload)`` for every checkpoint, newest first."""
    for page in range(1, _MAX_CHECKPOINT_PAGES + 1):
        resp = client.request(
            "GET",
            "/memories",
            params={"memory_type": "checkpoint", "per_page": _PAGE_SIZE, "page": page},
        )
        items = _items(resp)
        for item in items:
            yield item, _checkpoint_payload(item)
        pagination = resp.get("pagination", {}) if isinstance(resp, dict) else {}
        if not items or not pagination.get("has_next", len(items) == _PAGE_SIZE):
            return


def _matching_ids(client: PenfieldClient, query: str, *, limit: int) -> list[str]:
    found = client.request(
        "POST",
        "/search/hybrid",
        json={"query": query, "limit": limit, "min_score_threshold": _LINK_SCORE},
    )
    return [
        str(it["id"])
        for it in _items(found)
        if it.get("id") and isinstance(it.get("score"), int | float) and it["score"] >= _LINK_SCORE
    ]


def _fetch_memories(client: PenfieldClient, memory_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch memories by id, keeping the given order. Missing ones are left out."""

    def one(memory_id: str) -> dict[str, Any] | None:
        try:
            found = client.request("GET", f"/memories/{memory_id}")
        except PenfieldError:
            return None
        return found if isinstance(found, dict) else None

    if not memory_ids:
        return []
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(memory_ids))) as pool:
        return [m for m in pool.map(one, memory_ids) if m is not None]


def save_context(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    name = text_arg(args, "name", required=True, max_chars=200)
    description = text_arg(args, "description", max_chars=CONTENT_MAX_CHARS)

    for _item, saved in _iter_checkpoints(client):
        if saved.get("checkpoint_name") == name:
            return {
                "success": False,
                "error": f"Context name '{name}' already exists. Use a unique name.",
                "error_code": "DUPLICATE_NAME",
            }

    # The memories the description is about; a description too short to
    # search on, or a failed search, links the most recent memories.
    memory_ids: list[str] = []
    searched = False
    if len(description.split()) >= _MIN_QUERY_WORDS:
        try:
            memory_ids = _matching_ids(client, description[:200], limit=30)
            searched = True
        except PenfieldError:
            searched = False
    if not searched:
        recent = client.request(
            "GET", "/memories", params={"sort": "-created_at", "per_page": _RECENT_FALLBACK}
        )
        memory_ids = [str(it["id"]) for it in _items(recent) if it.get("id")]

    # The references written into the description.
    uuid_refs = [u.lower() for u in _MEMORY_ID_REF.findall(description)]
    phrase_refs = _MEMORY_PHRASE_REF.findall(description)
    seen = set(memory_ids)
    referenced: list[str] = []
    unresolved: list[str] = []
    unique_uuids = list(dict.fromkeys(uuid_refs))
    existing = {str(m.get("id")) for m in _fetch_memories(client, unique_uuids)}
    for memory_id in unique_uuids:
        if memory_id not in existing:
            unresolved.append(f"memory_id: {memory_id}")
        elif memory_id not in seen:
            seen.add(memory_id)
            referenced.append(memory_id)
    for phrase in dict.fromkeys(phrase_refs):
        try:
            match = _matching_ids(client, phrase, limit=1)
        except PenfieldError:
            match = []
        if not match:
            unresolved.append(f"memory: '{phrase}'")
        elif match[0] not in seen:
            seen.add(match[0])
            referenced.append(match[0])
    extracted = len(set(uuid_refs)) + len(set(phrase_refs))

    content = json.dumps(
        {
            "checkpoint_name": name,
            "description": description,
            "memory_count": len(memory_ids),
            "memory_ids": memory_ids,
            "referenced_memories": referenced,
        }
    )
    name_tag = [] if tag_problem(name) else [name]
    created = client.request(
        "POST",
        "/memories",
        json={
            "content": content,
            "memory_type": "checkpoint",
            "importance": 0.9,
            "tags": ["checkpoint", "context", *name_tag],
            # Penfield's MCP server looks here to see whether a name is taken.
            # No source_type: Penfield sets it, as for the MCP server's own
            # checkpoints. Matches hermes-penfield 0.3.3.
            "metadata": {"checkpoint_name": name},
        },
    )
    return {
        "success": True,
        "message": f"Context '{name}' saved",
        "context_id": created.get("id") if isinstance(created, dict) else None,
        "name": name,
        "memories_included": len(memory_ids),
        "memory_ids": memory_ids,
        "references_extracted": extracted,
        "references_resolved": extracted - len(unresolved),
        "unresolved_references": unresolved,
        "referenced_memories": referenced,
    }


def list_contexts(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    limit = int_arg(args, "limit", 20, 1, 100)
    offset = int_arg(args, "offset", 0, 0, 1_000_000)
    pattern = text_arg(args, "name_pattern").lower()
    with_descriptions = bool_arg(args, "include_descriptions")
    matches = []
    for item, saved in _iter_checkpoints(client):
        name = str(saved.get("checkpoint_name") or str(item.get("content", ""))[:100])
        if pattern and pattern not in name.lower():
            continue
        context = {
            "id": item.get("id"),
            "name": name,
            "memory_count": saved.get("memory_count", 0),
            "created": item.get("created_at"),
        }
        if with_descriptions:
            context["description"] = saved.get("description", item.get("content", ""))
        matches.append(context)
    page = matches[offset : offset + limit]
    return {
        "contexts": page,
        "total": len(matches),
        "limit": limit,
        "offset": offset,
        "has_more": len(matches) > offset + len(page),
    }


def _find_checkpoint(client: PenfieldClient, name: str) -> dict[str, Any] | None:
    if UUID_RE.match(name):
        try:
            found = client.request("GET", f"/memories/{name}")
        except PenfieldError as exc:
            if exc.status != 404:
                raise
            found = None
        if isinstance(found, dict) and found.get("memory_type") == "checkpoint":
            return found
    for item, saved in _iter_checkpoints(client):
        if saved.get("checkpoint_name") == name:
            return item
    return None


def restore_context(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    name = text_arg(args, "name", required=True)
    limit = int_arg(args, "limit", 20, 1, 100)
    if name.lower() == "awakening":
        return {"name": "awakening", **awaken(client, {})}
    checkpoint = _find_checkpoint(client, name)
    if checkpoint is None:
        return {"success": False, "error": f"No checkpoint found with name '{name}'"}
    saved = _checkpoint_payload(checkpoint)
    saved_ids = [str(m) for m in saved.get("memory_ids") or []]
    referenced = [str(m) for m in saved.get("referenced_memories") or []]
    all_ids = list(dict.fromkeys(saved_ids + referenced))
    memories = [
        memory_view(m, max_chars=RESULT_CONTENT_MAX_CHARS)
        for m in _fetch_memories(client, all_ids[:limit])
    ]
    return {
        "success": True,
        "context_id": checkpoint.get("id"),
        "name": saved.get("checkpoint_name", name),
        "description": saved.get("description", checkpoint.get("content", "")),
        "memories": memories,
        "count": len(memories),
        "saved_count": len(all_ids),
        "truncated": len(all_ids) > limit,
    }


OPERATIONS = {
    "awaken": awaken,
    "store": store,
    "recall": recall,
    "search": search,
    "fetch": fetch,
    "update_memory": update_memory,
    "connect": connect,
    "disconnect": disconnect,
    "explore": explore,
    "reflect": reflect,
    "save_context": save_context,
    "restore_context": restore_context,
    "list_contexts": list_contexts,
    "save_artifact": save_artifact,
    "retrieve_artifact": retrieve_artifact,
    "list_artifacts": list_artifacts,
    "delete_artifact": delete_artifact,
}
