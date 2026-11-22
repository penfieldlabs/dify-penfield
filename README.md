# Penfield for Dify

Penfield gives your Dify agents and workflows long-term memory. It stores what your AI learns as memories, links them into a knowledge graph, and finds them again with hybrid search (keywords, meaning and graph links together). The same memory is shared with every other AI app connected to your Penfield account, such as Claude, ChatGPT, Cursor or Codex.

- Source repository: https://github.com/penfieldlabs/dify-penfield
- Penfield documentation: https://docs.penfield.app
- Support: support@penfield.app

## Tools

The plugin offers the same 17 tools as Penfield's MCP server, under the same names, so an agent works the same way in Dify as in Claude, ChatGPT or Cursor.

| Tool | What it does |
| --- | --- |
| **Awaken** | Load the personality briefing and standing instructions set up in Penfield. |
| **Store memory** | Save a decision, preference, fact, correction or insight, with importance and tags. Its type is detected from the content. |
| **Recall** | Hybrid search (keywords, meaning and graph links) over memories and uploaded documents, with tag, date and source filters. Best for names, ids and exact phrases. |
| **Semantic search** | Search that ranks by meaning, for when you do not know how a memory was worded. |
| **Get memory** | Fetch one memory in full by its id. |
| **Update memory** | Change the content, importance or tags of a memory instead of storing a duplicate. |
| **Connect memories** | Link two memories with one of 24 relationship types, such as `supports`, `contradicts`, `updates` or `depends_on`. |
| **Disconnect memories** | Remove the link between two memories. |
| **Explore graph** | Follow the knowledge graph outward from a memory. |
| **Reflect** | Summarize memories over a time window into topics, clusters and patterns. |
| **Save context** | Save a named checkpoint of the current work: a handoff description plus the memories it is about. |
| **Restore context** | Load a checkpoint and its memories, to pick up where a session or another agent left off. |
| **List contexts** | List saved checkpoints, newest first. |
| **Save file** | Save a text file (notes, code, a document) to Penfield file storage. |
| **Get file** | Read a stored file. |
| **List files** | List the folders and files in a storage folder. |
| **Delete file** | Delete a stored file. |

Every tool returns its result twice: as a JSON object in `json`, for workflows, and as the same JSON in `text`. Workflows should read `json`. An agent reads the result in the same form Penfield's MCP server returns. Dify 1.20 leaves out a JSON that repeats the text, so an agent sees each result once there; Dify 1.7.0 shows it twice. When a call fails, for example because a tag breaks Penfield's rules, the key lacks a permission or Penfield cannot be reached, the tool still completes: the text starts with `Penfield error:` and the JSON is `{"success": false, "error": ..., "error_code": ..., "status": ...}`, so a workflow can branch on `success` and an agent can read what went wrong. Successful results never contain `"success": false`.

Checkpoints use the same format as Penfield's MCP server: a checkpoint saved in Dify restores in Claude or any other Penfield client, and the other way round.

## Setup

1. Create a Penfield account at [portal.penfield.app](https://portal.penfield.app/sign-up).
2. In the portal, open **API Keys** in the menu ([direct link](https://portal.penfield.app/api-keys)) and create a key. **Read** and **write** cover most tools; **Disconnect memories** and **Delete file** also need **delete**. A read-only key is a safe choice for an agent that should only look things up. The key starts with `tm_`.
3. In Dify, open **Plugins**, find **Penfield** in the Marketplace and install it.
4. Open **Tools > Penfield > Authorize**, paste the API key and save. Dify checks the key with Penfield before saving it.
5. Add Penfield tools to an Agent app, or use them as Tool nodes in a Chatflow or Workflow.

## Requirements and connection

- A Penfield account and API key.
- Dify 1.7.0 or later, Community Edition or Dify Cloud.
- The plugin connects only to `https://api.penfield.app` over HTTPS. Self-hosted Dify instances need outbound access to that host.

## Usage examples

**Agent memory.** Give an Agent app the Awaken, Recall and Store memory tools and add an instruction such as: "At the start of a conversation, call Awaken. Before answering, use Recall to check what you already know about the user's question. When the user shares a decision, preference or correction, use Store memory to save it." The agent then remembers across conversations, and across other AI apps connected to the same Penfield account.

**Workflow knowledge capture.** In a Workflow, pass the output of an LLM node into a Store memory node with tags such as `meeting-notes,project-x`. A later Workflow can use Recall with the same tags to pull those notes back.

**Building the graph.** After storing a new memory, call Recall to find related memories and Connect memories to link them, for example `updates` when a new decision replaces an old one. Explore graph then returns the whole chain from any point.

**Handoffs between sessions.** At the end of a long task, have the agent call Save context with a name such as `pricing-review-2026-11-21` and a description of what was done and what comes next. A later conversation, a different Dify app, or Claude can call Restore context with that name and continue.

## How the plugin authenticates

Your API key is stored by Dify as an encrypted credential. On each call the plugin exchanges it at `POST https://api.penfield.app/api/v2/auth/token` for a short-lived access token, keeps that token in memory only, and refreshes it before it expires. The API key is never logged or returned in tool output.

## Limits

- A memory holds up to 10,000 characters. Split longer text into several memories.
- Up to 10 tags per memory. Penfield stores tags in lower case with hyphens; a tag must keep 2 to 50 characters.
- Search returns up to 100 results per call. Long memories are shortened to 2,000 characters in search results; Get memory returns the whole memory.
- Checkpoint names are unique in a Penfield account.
- Files are up to 1 MB each. Saving to a path that already exists is refused; delete the file first.
- Deleting memories is not offered to agents. Delete memories in the Penfield portal.

## Privacy

See [PRIVACY.md](PRIVACY.md) and Penfield's privacy policy at https://www.penfield.app/privacy.

## License

MIT. See [LICENSE](LICENSE).
