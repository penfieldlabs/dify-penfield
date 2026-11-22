# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penfield
"""Write tools/<name>.yaml and tools/<name>.py for the 17 Penfield tools.

The tool list is defined once, here, and the files are generated from it.
Run from the plugin root:  python scripts/gen_tools.py
tests/test_plugin.py fails when the files on disk differ from this list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from penfield_client import RELATIONSHIP_TYPES  # noqa: E402
from penfield_ops import REFLECT_WINDOWS  # noqa: E402

UUID_EXAMPLE = "550e8400-e29b-41d4-a716-446655440000"
TAG_RULE = (
    "Up to 10 tags, comma-separated. Penfield stores a tag in lower case with spaces as "
    "hyphens and keeps only a-z, 0-9 and hyphens; a tag must keep 2 to 50 characters."
)


def p(name, ptype, en, zh, human_en, human_zh, llm, *, required=False, **extra):
    """One tool parameter."""
    spec = {
        "name": name,
        "type": ptype,
        "required": required,
        "form": "llm",
        "label": {"en_US": en, "zh_Hans": zh},
        "human_description": {"en_US": human_en, "zh_Hans": human_zh},
        "llm_description": llm,
    }
    spec.update(extra)
    return spec


def options(values, labels=None):
    labels = labels or {}
    return [
        {"value": v, "label": {"en_US": labels.get(v, v), "zh_Hans": labels.get(v, v)}}
        for v in values
    ]


QUERY = p(
    "query",
    "string",
    "Query",
    "查询",
    "What to search for.",
    "要搜索的内容。",
    "What to search for, in natural language. Up to 4,000 characters.",
    required=True,
)
LIMIT_10 = p(
    "limit",
    "number",
    "Max results",
    "最大结果数",
    "Maximum number of results, 1 to 100.",
    "最大结果数量，1 到 100。",
    "Maximum number of results, 1 to 100. Default 10.",
    default=10,
    min=1,
    max=100,
)
START_DATE = p(
    "start_date",
    "string",
    "From date",
    "开始日期",
    "Only memories created on or after this date (ISO 8601, e.g. 2026-01-31).",
    "仅包含在此日期或之后创建的记忆（ISO 8601，例如 2026-01-31）。",
    "Optional ISO 8601 date, for example 2026-01-31. Leave empty if unused.",
)
END_DATE = p(
    "end_date",
    "string",
    "To date",
    "结束日期",
    "Only memories created on or before this date (ISO 8601).",
    "仅包含在此日期或之前创建的记忆（ISO 8601）。",
    "Optional ISO 8601 date. Leave empty if unused.",
)
TAGS_WRITE = p(
    "tags",
    "string",
    "Tags",
    "标签",
    "Comma-separated tags, up to 10.",
    "逗号分隔的标签，最多 10 个。",
    TAG_RULE + " Leave empty for none.",
)
IMPORTANCE = p(
    "importance",
    "number",
    "Importance",
    "重要性",
    "How important this memory is, from 0 to 1.",
    "该记忆的重要程度，0 到 1。",
    "Optional importance from 0.0 to 1.0. Leave empty unless it matters.",
    min=0,
    max=1,
)
FROM_MEMORY = p(
    "from_memory",
    "string",
    "From memory",
    "起始记忆",
    "Id (UUID) of the source memory.",
    "源记忆的 ID（UUID）。",
    "UUID of the source memory.",
    required=True,
)
TO_MEMORY = p(
    "to_memory",
    "string",
    "To memory",
    "目标记忆",
    "Id (UUID) of the target memory.",
    "目标记忆的 ID（UUID）。",
    "UUID of the target memory.",
    required=True,
)
PATH = p(
    "path",
    "string",
    "Path",
    "路径",
    "Full path of the file, starting with /, e.g. /project/notes.md.",
    "文件的完整路径，以 / 开头，例如 /project/notes.md。",
    "Full path starting with / and ending with a file name, for example /project/notes.md.",
    required=True,
)

TOOLS = [
    {
        "name": "awaken",
        "label": ("Awaken", "唤醒"),
        "human": (
            "Load the personality briefing and instructions the user set up in Penfield.",
            "加载用户在 Penfield 中设置的人格简报和指令。",
        ),
        "llm": (
            "Load the user's personality briefing and standing instructions from Penfield. "
            "Call it at the start of a new conversation, before anything else. Takes no "
            "parameters."
        ),
        "params": [],
    },
    {
        "name": "store",
        "label": ("Store memory", "存储记忆"),
        "human": (
            "Save a new memory to Penfield. Its type is detected from the content.",
            "将新记忆保存到 Penfield。记忆类型会根据内容自动识别。",
        ),
        "llm": (
            "Store a new long-term memory: a decision, preference, fact, correction or "
            "insight worth keeping across sessions. Write complete thoughts with their "
            "context. The memory type is detected from the content. After storing, use "
            "connect to link it to related memories. Leave optional fields empty if not "
            "needed. Never store secrets such as passwords or API keys."
        ),
        "params": [
            p(
                "content",
                "string",
                "Content",
                "内容",
                "The memory to save, up to 10,000 characters.",
                "要保存的记忆，最多 10,000 个字符。",
                "The memory text, up to 10,000 characters.",
                required=True,
            ),
            TAGS_WRITE,
            IMPORTANCE,
        ],
    },
    {
        "name": "recall",
        "label": ("Recall", "回忆"),
        "human": (
            "Find memories and uploaded documents with hybrid keyword, meaning and graph search.",
            "使用关键词、语义和图谱混合搜索查找记忆和已上传的文档。",
        ),
        "llm": (
            "Hybrid search (keywords, meaning and graph links) across the user's memories "
            "and uploaded documents. The main tool for retrieving earlier context, "
            "decisions or facts, and the one to use for names, ids and exact phrases. "
            "Leave optional filters empty if not filtering."
        ),
        "params": [
            QUERY,
            LIMIT_10,
            p(
                "source_type",
                "select",
                "Search in",
                "搜索范围",
                "Memories, uploaded documents, or both.",
                "记忆、已上传文档或两者。",
                "'memory' or 'document'. Leave empty to search both.",
                options=options(
                    ("memory", "document"), {"memory": "Memories", "document": "Documents"}
                ),
            ),
            p(
                "tags",
                "string",
                "Tags",
                "标签",
                "Only results with any of these comma-separated tags.",
                "仅返回包含这些标签（逗号分隔）之一的结果。",
                "Optional comma-separated tags; results with any of them are returned.",
            ),
            START_DATE,
            END_DATE,
        ],
    },
    {
        "name": "search",
        "label": ("Semantic search", "语义搜索"),
        "human": (
            "Find memories by meaning rather than by shared words.",
            "按含义而非相同词语查找记忆。",
        ),
        "llm": (
            "Semantic search: ranks memories by meaning far more than by shared words. "
            "Use it when you do not know how a memory was worded, or when recall finds "
            "nothing useful. Takes a query and a limit only."
        ),
        "params": [QUERY, LIMIT_10],
    },
    {
        "name": "fetch",
        "label": ("Get memory", "获取记忆"),
        "human": ("Get one memory by its id.", "根据 ID 获取一条记忆。"),
        "llm": (
            "Get one memory in full by its UUID, for example an id returned by store, "
            "recall or search."
        ),
        "params": [
            p(
                "id",
                "string",
                "Memory id",
                "记忆 ID",
                "Id (UUID) of the memory.",
                "记忆的 ID（UUID）。",
                f"UUID of the memory, for example {UUID_EXAMPLE}.",
                required=True,
            ),
        ],
    },
    {
        "name": "update_memory",
        "label": ("Update memory", "更新记忆"),
        "human": (
            "Change a memory's content, importance or tags.",
            "修改记忆的内容、重要性或标签。",
        ),
        "llm": (
            "Update an existing memory's content, importance or tags. Only the fields "
            "given are changed; tags given replace the existing tags. Leave fields you do "
            "not want to change empty."
        ),
        "params": [
            p(
                "memory_id",
                "string",
                "Memory id",
                "记忆 ID",
                "Id (UUID) of the memory to update.",
                "要更新的记忆 ID（UUID）。",
                "UUID of the memory to update.",
                required=True,
            ),
            p(
                "content",
                "string",
                "New content",
                "新内容",
                "Replacement content, up to 10,000 characters.",
                "替换内容，最多 10,000 个字符。",
                "New content, up to 10,000 characters. Leave empty to keep it.",
            ),
            IMPORTANCE,
            TAGS_WRITE,
        ],
    },
    {
        "name": "connect",
        "label": ("Connect memories", "连接记忆"),
        "human": (
            "Link two memories with a typed relationship in the knowledge graph.",
            "在知识图谱中用指定类型的关系连接两条记忆。",
        ),
        "llm": (
            "Create a typed relationship between two memories to build the knowledge "
            "graph. Use it right after store to link the new memory to related ones. Both "
            "ids must be memory UUIDs. Leave strength empty if not setting it."
        ),
        "params": [
            FROM_MEMORY,
            TO_MEMORY,
            p(
                "relationship_type",
                "select",
                "Relationship",
                "关系类型",
                "How the first memory relates to the second.",
                "第一条记忆与第二条记忆的关系。",
                "How from_memory relates to to_memory, for example supports, contradicts, "
                "supersedes, part_of, causes or references.",
                required=True,
                options=options(RELATIONSHIP_TYPES),
            ),
            p(
                "strength",
                "number",
                "Strength",
                "强度",
                "How strong the relationship is, from 0 to 1.",
                "关系强度，0 到 1。",
                "Optional strength from 0.0 to 1.0.",
                min=0,
                max=1,
            ),
        ],
    },
    {
        "name": "disconnect",
        "label": ("Disconnect memories", "断开记忆"),
        "human": (
            "Remove the relationship between two memories.",
            "删除两条记忆之间的关系。",
        ),
        "llm": (
            "Remove the relationship between two memories. Both ids are required. Needs a "
            "Penfield API key with the delete permission."
        ),
        "params": [FROM_MEMORY, TO_MEMORY],
    },
    {
        "name": "explore",
        "label": ("Explore graph", "探索图谱"),
        "human": (
            "Follow the knowledge graph out from a memory.",
            "从一条记忆出发遍历知识图谱。",
        ),
        "llm": (
            "Traverse the knowledge graph from a starting memory and return the connected "
            "memories and how they relate. Leave relationship_types empty to follow every "
            "type."
        ),
        "params": [
            p(
                "start_memory",
                "string",
                "Start memory",
                "起始记忆",
                "Id (UUID) of the memory to start from.",
                "起始记忆的 ID（UUID）。",
                "UUID of the memory to start from.",
                required=True,
            ),
            p(
                "max_depth",
                "number",
                "Max depth",
                "最大深度",
                "How many steps to follow, 1 to 10.",
                "遍历的最大步数，1 到 10。",
                "How many relationship steps to follow, 1 to 10. Default 3.",
                default=3,
                min=1,
                max=10,
            ),
            p(
                "relationship_types",
                "string",
                "Relationship types",
                "关系类型",
                "Only follow these comma-separated relationship types.",
                "仅遍历这些关系类型（逗号分隔）。",
                "Optional comma-separated relationship types to follow, for example "
                "supports,causes. Leave empty for all.",
            ),
        ],
    },
    {
        "name": "reflect",
        "label": ("Reflect", "反思"),
        "human": (
            "Find themes and patterns in recent memories.",
            "发现近期记忆中的主题和模式。",
        ),
        "llm": (
            "Analyze memory patterns over a time window and return themes, active topics "
            "and insights. Use it to answer 'what have I been working on?'. Leave optional "
            "fields empty if not filtering."
        ),
        "params": [
            p(
                "time_window",
                "select",
                "Time window",
                "时间范围",
                "Which memories to analyze.",
                "要分析的记忆范围。",
                "'recent' is the last 50 memories; 'today', 'week' and 'month' look back "
                "that far; '1d', '7d', '30d' and '90d' count days back from now. Default "
                "recent.",
                default="recent",
                options=options(REFLECT_WINDOWS),
            ),
            p(
                "include_documents",
                "boolean",
                "Include documents",
                "包含文档",
                "Also analyze uploaded documents.",
                "同时分析已上传的文档。",
                "Also analyze chunks of uploaded documents. Default false.",
                default=False,
            ),
            START_DATE,
            END_DATE,
        ],
    },
    {
        "name": "save_context",
        "label": ("Save context", "保存上下文"),
        "human": (
            "Save a named checkpoint of the current work for a later session or another agent.",
            "为后续会话或其他智能体保存当前工作的命名检查点。",
        ),
        "llm": (
            "Create a checkpoint of the current work for a handoff to another session or "
            "agent. The description is the handoff narrative (what was done, what was "
            "found, open questions, next steps) and also the search that selects which "
            "memories get linked; a description under three words links the 20 most "
            "recent memories instead. Link specific memories with 'memory_id: UUID' "
            "(reliable) or \"memory: 'a phrase'\" (best effort); the result lists any "
            "reference that did not resolve. Names must be unique, so include a date or "
            "session identifier."
        ),
        "params": [
            p(
                "name",
                "string",
                "Name",
                "名称",
                "Checkpoint name, unique in your Penfield account.",
                "检查点名称，在 Penfield 账户中唯一。",
                "Checkpoint name, unique across the account, up to 200 characters.",
                required=True,
            ),
            p(
                "description",
                "string",
                "Description",
                "描述",
                "What was done and what comes next.",
                "已完成的工作和后续步骤。",
                "The handoff narrative. Can reference memories with memory_id: UUID.",
            ),
        ],
    },
    {
        "name": "restore_context",
        "label": ("Restore context", "恢复上下文"),
        "human": (
            "Load a saved checkpoint and the memories saved with it.",
            "加载已保存的检查点及其关联的记忆。",
        ),
        "llm": (
            "Restore a saved checkpoint by its exact name or id. Returns the handoff "
            "description and the memories saved with it. The name 'awakening' loads the "
            "personality briefing instead."
        ),
        "params": [
            p(
                "name",
                "string",
                "Name",
                "名称",
                "Exact checkpoint name or id.",
                "检查点的准确名称或 ID。",
                "Exact checkpoint name (case-sensitive) or its id.",
                required=True,
            ),
            p(
                "limit",
                "number",
                "Max memories",
                "最大记忆数",
                "Maximum number of memories to load, 1 to 100.",
                "最多加载的记忆数量，1 到 100。",
                "Maximum number of memories to restore, 1 to 100. Default 20.",
                default=20,
                min=1,
                max=100,
            ),
        ],
    },
    {
        "name": "list_contexts",
        "label": ("List contexts", "列出上下文"),
        "human": ("List saved checkpoints, newest first.", "列出已保存的检查点，最新的在前。"),
        "llm": (
            "List saved checkpoints, newest first, to find a handoff point to restore. "
            "Leave optional fields empty if not filtering."
        ),
        "params": [
            p(
                "limit",
                "number",
                "Max results",
                "最大结果数",
                "Maximum number to list, 1 to 100.",
                "最多列出的数量，1 到 100。",
                "Maximum number of checkpoints, 1 to 100. Default 20.",
                default=20,
                min=1,
                max=100,
            ),
            p(
                "offset",
                "number",
                "Skip",
                "跳过",
                "How many to skip, for paging.",
                "分页时跳过的数量。",
                "How many matching checkpoints to skip. Default 0.",
                default=0,
                min=0,
            ),
            p(
                "name_pattern",
                "string",
                "Name contains",
                "名称包含",
                "Only checkpoints whose name contains this text.",
                "仅列出名称包含此文本的检查点。",
                "Optional case-insensitive text the name must contain.",
            ),
            p(
                "include_descriptions",
                "boolean",
                "Include descriptions",
                "包含描述",
                "Also return each checkpoint's description.",
                "同时返回每个检查点的描述。",
                "Include each checkpoint's description. Default false.",
                default=False,
            ),
        ],
    },
    {
        "name": "save_artifact",
        "label": ("Save file", "保存文件"),
        "human": (
            "Save a text file (notes, code, a document) to Penfield file storage.",
            "将文本文件（笔记、代码、文档）保存到 Penfield 文件存储。",
        ),
        "llm": (
            "Save a text file such as notes, code or a document to Penfield file storage. "
            "Files are not returned by recall or search; use store for searchable "
            "memories. A path that already exists is refused: delete it first or choose a "
            "new path."
        ),
        "params": [
            PATH,
            p(
                "content",
                "string",
                "Content",
                "内容",
                "The file content, up to 1 MB.",
                "文件内容，最大 1 MB。",
                "The file content, up to 1 MB.",
                required=True,
            ),
        ],
    },
    {
        "name": "retrieve_artifact",
        "label": ("Get file", "获取文件"),
        "human": ("Read a file from Penfield file storage.", "从 Penfield 文件存储读取文件。"),
        "llm": "Get a stored file by its path. Returns the full content.",
        "params": [PATH],
    },
    {
        "name": "list_artifacts",
        "label": ("List files", "列出文件"),
        "human": (
            "List the folders and files in a Penfield storage folder.",
            "列出 Penfield 存储文件夹中的文件夹和文件。",
        ),
        "llm": (
            "List the folders and files directly inside a folder (not recursive). Leave "
            "path_prefix empty to list the root."
        ),
        "params": [
            p(
                "path_prefix",
                "string",
                "Folder",
                "文件夹",
                "Folder to list, e.g. /project/. Default /.",
                "要列出的文件夹，例如 /project/。默认为 /。",
                "Folder path starting with /, for example /project/. Default /.",
                default="/",
            ),
            p(
                "name_pattern",
                "string",
                "Name contains",
                "名称包含",
                "Only names containing this text.",
                "仅列出名称包含此文本的项目。",
                "Optional case-insensitive text that file and folder names must contain.",
            ),
        ],
    },
    {
        "name": "delete_artifact",
        "label": ("Delete file", "删除文件"),
        "human": (
            "Permanently delete a file from Penfield file storage.",
            "从 Penfield 文件存储中永久删除文件。",
        ),
        "llm": (
            "Permanently delete a stored file by its path. Needs a Penfield API key with "
            "the delete permission."
        ),
        "params": [PATH],
    },
]


def tool_yaml(tool: dict) -> dict:
    return {
        "identity": {
            "name": tool["name"],
            "author": "penfieldlabs",
            "label": {"en_US": tool["label"][0], "zh_Hans": tool["label"][1]},
        },
        "description": {
            "human": {"en_US": tool["human"][0], "zh_Hans": tool["human"][1]},
            "llm": tool["llm"],
        },
        "parameters": tool["params"],
        "extra": {"python": {"source": f"tools/{tool['name']}.py"}},
    }


def class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) + "Tool"


def tool_py(tool: dict) -> str:
    return f'''# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penfield
# Generated by scripts/gen_tools.py. Edit that file, not this one.
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._common import run


class {class_name(tool["name"])}(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        yield from run(self, "{tool["name"]}", tool_parameters)
'''


HEADER = "# Generated by scripts/gen_tools.py. Edit that file, not this one.\n"


def render_yaml(tool: dict) -> str:
    return HEADER + yaml.safe_dump(tool_yaml(tool), sort_keys=False, allow_unicode=True, width=100)


def expected_files() -> dict[str, str]:
    files = {}
    for tool in TOOLS:
        files[f"tools/{tool['name']}.yaml"] = render_yaml(tool)
        files[f"tools/{tool['name']}.py"] = tool_py(tool)
    return files


def main() -> None:
    for rel, text in expected_files().items():
        (ROOT / rel).write_text(text, encoding="utf-8")
    print(f"wrote {len(TOOLS)} tools")


if __name__ == "__main__":
    main()
