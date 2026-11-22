# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penfield
"""Runs a Penfield operation for a Dify tool.

Every tool file holds one small ``Tool`` subclass that calls ``run``. Dify
loads exactly one ``Tool`` subclass per file, so the shared code lives here
as plain functions rather than in a base class.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from penfield_client import PenfieldClient, PenfieldError
from penfield_ops import OPERATIONS


def client_from(runtime: Any) -> PenfieldClient:
    return PenfieldClient(runtime.credentials.get("penfield_api_key", ""))


def run(
    tool: Tool, operation: str, params: dict[str, Any]
) -> Generator[ToolInvokeMessage, None, None]:
    """Call ``operation`` and yield a readable text message and the JSON result.

    The text is what an agent reads; the JSON is what a workflow wires into
    later nodes. A failed call yields the same pair: the text says what went
    wrong, and the JSON is ``{"success": false, "error", "error_code",
    "status"}``.
    """
    try:
        result = OPERATIONS[operation](client_from(tool.runtime), dict(params or {}))
    except PenfieldError as exc:
        # The node completes with a result that says what went wrong, rather
        # than failing: an agent reads the text and can correct itself, and
        # a workflow branches on ``success``.
        yield tool.create_text_message(f"Penfield error: {exc}")
        yield tool.create_json_message(
            {"success": False, "error": str(exc), "error_code": exc.code, "status": exc.status}
        )
        return
    yield tool.create_text_message(render(operation, result))
    yield tool.create_json_message(result)


def render(operation: str, result: dict[str, Any]) -> str:
    """The text message: the JSON result as a string, or a readable error.

    Dify hands an agent both the text and the JSON of a tool call, and leaves
    the JSON out when it equals the text. Sending the JSON as the text means
    an agent reads each result once, in the same form Penfield's MCP server
    returns, while a workflow still gets the object in ``json``.
    """
    if result.get("success") is False:
        code = f" ({result['error_code']})" if result.get("error_code") else ""
        return f"Penfield: {result.get('error', 'the request failed')}{code}"
    return json.dumps(result, ensure_ascii=False)
