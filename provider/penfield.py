# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penfield
from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from penfield_client import PenfieldClient, PenfieldError


class PenfieldProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            result = PenfieldClient(credentials.get("penfield_api_key", "")).verify()
        except PenfieldError as exc:
            raise ToolProviderCredentialValidationError(str(exc)) from exc
        except Exception as exc:  # never echo the key
            raise ToolProviderCredentialValidationError(
                f"Could not validate the Penfield API key: {type(exc).__name__}"
            ) from exc
        if not (isinstance(result, dict) and result.get("valid")):
            raise ToolProviderCredentialValidationError("Penfield did not accept the API key.")
