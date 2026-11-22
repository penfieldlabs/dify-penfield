# Privacy Policy: Penfield plugin for Dify

This policy covers the Penfield plugin for Dify ("the plugin"), published by Penfield. It explains what data the plugin handles and where that data goes. How Penfield itself stores and processes your data is covered by Penfield's privacy policy at https://www.penfield.app/privacy, and its terms at https://www.penfield.app/terms.

## What the plugin sends, and to whom

The plugin sends data to one service only: the Penfield API at `https://api.penfield.app`, over HTTPS. It does not send data to any other third party, and it does not include analytics or tracking.

Depending on the tool you or your agent call, the plugin sends:

- **Your Penfield API key**, only to `POST /api/v2/auth/token`, to obtain a short-lived access token.
- **Memory content** you choose to store or update: the text, importance and tags. This content can include anything your workflow or agent passes to the tool, including personal data, so decide what your app stores.
- **Search queries** and filters (tags, dates, source) for Recall and Semantic search.
- **Memory ids** and relationship details for Get memory, Connect memories, Disconnect memories and Explore graph.
- **A time window** and optional dates for Reflect.
- **Checkpoint names and descriptions** for Save context, Restore context and List contexts.
- **File paths and file content** for the file tools.

## What the plugin stores

The plugin itself stores nothing on disk. It keeps the short-lived Penfield access token, the refresh token Penfield issues with it to renew it, and a one-way fingerprint of the API key used to look them up, in memory for as long as the plugin process runs. Your API key is stored by Dify as an encrypted tool credential, not by the plugin.

Memories, relationships and documents are stored by Penfield in your Penfield account, scoped to your tenant, as described in Penfield's privacy policy. You can view and delete them in the Penfield portal at https://portal.penfield.app.

## Logging

The plugin does not log your API key, access tokens, memory content or queries. Error messages returned to Dify contain the Penfield error code and message, never credentials.

## Data collection by Penfield as plugin publisher

Penfield does not collect usage data, telemetry or personal data through this plugin beyond the API requests listed above, which are needed to provide the service you signed up for.

## Contact

Questions about this policy: support@penfield.app
