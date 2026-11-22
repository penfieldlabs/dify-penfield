# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penfield
"""Minimal client for the Penfield REST API (v2).

The API key is exchanged for a short-lived access token at
``POST /api/v2/auth/token``. Every other call sends that access token.
Tokens are cached in memory per API key (keyed by a one-way fingerprint,
never by the key itself) and refreshed before they expire.

The host is fixed. The plugin never calls a URL supplied by the user or by
the model.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

API_BASE = "https://api.penfield.app/api/v2"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
USER_AGENT = "dify-penfield/0.1.0"
REFRESH_SKEW_SECONDS = 300.0


RELATIONSHIP_TYPES = (
    "supersedes",
    "updates",
    "evolution_of",
    "supports",
    "contradicts",
    "disputes",
    "parent_of",
    "child_of",
    "sibling_of",
    "composed_of",
    "part_of",
    "causes",
    "influenced_by",
    "prerequisite_for",
    "implements",
    "documents",
    "tests",
    "example_of",
    "responds_to",
    "references",
    "inspired_by",
    "follows",
    "precedes",
    "depends_on",
)


class PenfieldError(Exception):
    """An error returned by the Penfield API, safe to show to the user."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class _Token:
    access: str
    refresh: str | None
    expires_at: float

    def fresh(self) -> bool:
        return time.time() < self.expires_at - REFRESH_SKEW_SECONDS


# Tokens per API key, by fingerprint. ``_cache_lock`` guards the two dicts
# only and is never held during a request. Each key has its own lock, held
# while that key signs in or renews, so one slow sign-in does not hold up
# calls made with other keys, and one key never signs in twice at once.
_cache: dict[str, _Token] = {}
_key_locks: dict[str, threading.Lock] = {}
_cache_lock = threading.Lock()

#: Requests that create something new each time. If one of them times out
#: after it was sent, it may have been saved, so repeating it could save it
#: twice. Everything else is a read, or safe to repeat.
_WRITE_ONCE = {"/memories": "memory", "/relationships": "relationship", "/artifacts": "file"}

_NOT_ANSWERED = "Penfield did not respond in time. Try again."


def _key_lock(fingerprint: str) -> threading.Lock:
    with _cache_lock:
        return _key_locks.setdefault(fingerprint, threading.Lock())


def _cached(fingerprint: str) -> _Token | None:
    with _cache_lock:
        return _cache.get(fingerprint)


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _error_from(resp: httpx.Response) -> PenfieldError:
    code = None
    message = f"Penfield API returned HTTP {resp.status_code}"
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = err.get("code")
            if err.get("message"):
                message = f"{err['message']} (HTTP {resp.status_code}"
                message += f", {code})" if code else ")"
            field_errors = err.get("field_errors")
            if isinstance(field_errors, dict) and field_errors:
                parts = [f"{k}: {'; '.join(map(str, v))}" for k, v in field_errors.items()]
                message += " " + " | ".join(parts)
    except ValueError:
        pass
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After")
        message = "Penfield rate limit reached." + (
            f" Retry after {retry} seconds." if retry else ""
        )
    return PenfieldError(message, resp.status_code, code)


class PenfieldClient:
    """Calls the Penfield API on behalf of one API key."""

    def __init__(self, api_key: str, base_url: str = API_BASE):
        api_key = (api_key or "").strip()
        if not api_key:
            raise PenfieldError("A Penfield API key is required.")
        self._api_key = api_key
        self._fp = _fingerprint(api_key)
        self._base = base_url.rstrip("/")

    # -- auth -----------------------------------------------------------
    # The session helpers below update the shared cache and return nothing,
    # so no credential ever travels through a return value.
    def _save_session(self, resp: httpx.Response) -> None:
        try:
            data = resp.json()["data"]
            entry = _Token(
                access=str(data["access_token"]),
                refresh=data.get("refresh_token"),
                expires_at=time.time() + float(data.get("expires_in") or 3600),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PenfieldError("Penfield returned an unexpected sign-in response.") from exc
        with _cache_lock:
            _cache[self._fp] = entry

    def _auth_post(self, path: str, **kwargs: Any) -> httpx.Response:
        """POST to a sign-in endpoint. Network failures become PenfieldError."""
        try:
            return httpx.post(f"{self._base}{path}", timeout=TIMEOUT, **kwargs)
        except httpx.TimeoutException as exc:
            raise PenfieldError(_NOT_ANSWERED) from exc
        except httpx.HTTPError as exc:
            raise PenfieldError(f"Could not reach Penfield: {type(exc).__name__}") from exc

    def _exchange(self) -> None:
        resp = self._auth_post(
            "/auth/token",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        if resp.status_code != 200:
            err = _error_from(resp)
            if resp.status_code == 401:
                err = PenfieldError(
                    "Penfield rejected the API key. Check it in the Penfield portal "
                    "under API Keys.",
                    401,
                    err.code,
                )
            raise err
        self._save_session(resp)

    def _refresh(self, entry: _Token) -> None:
        if not entry.refresh:
            self._exchange()
            return
        resp = self._auth_post(
            "/auth/refresh",
            json={"refresh_token": entry.refresh},
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            # Rotation means a refresh token works once. If it was already
            # used, or rejected, start again from the API key.
            self._exchange()
            return
        self._save_session(resp)

    def _access_token(self, rejected: str | None = None) -> str:
        """Return a usable access token, signing in or renewing as needed.

        ``rejected`` is a token Penfield just refused. It is renewed only if
        it is still the cached one, so threads that hit the same 401 renew
        once between them.
        """
        with _key_lock(self._fp):
            entry = _cached(self._fp)
            if entry is None:
                self._exchange()
            elif (rejected is not None and entry.access == rejected) or not entry.fresh():
                self._refresh(entry)
            entry = _cached(self._fp)
            if entry is None:  # pragma: no cover - _save_session always stores one
                raise PenfieldError("Penfield returned an unexpected sign-in response.")
            return entry.access

    def verify(self) -> dict[str, Any]:
        """Exchange the key and confirm the token. Used to validate credentials."""
        with _cache_lock:
            _cache.pop(self._fp, None)
        return self.request("GET", "/auth/verify")

    # -- requests -------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        bearer: str | None = None
        for attempt in (0, 1):
            bearer = self._access_token(rejected=bearer if attempt == 1 else None)
            try:
                resp = httpx.request(
                    method,
                    f"{self._base}{path}",
                    json=json,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    headers={
                        "Authorization": f"Bearer {bearer}",
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                    timeout=TIMEOUT,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                # The request never reached Penfield, so repeating it is safe.
                if isinstance(exc, httpx.TimeoutException):
                    raise PenfieldError(_NOT_ANSWERED) from exc
                raise PenfieldError(f"Could not reach Penfield: {type(exc).__name__}") from exc
            except httpx.HTTPError as exc:
                what = _WRITE_ONCE.get(path) if method.upper() == "POST" else None
                if what:
                    raise PenfieldError(
                        f"Penfield did not answer, so it is not known whether the {what} was "
                        "saved. Check before trying again, so that it is not saved twice."
                    ) from exc
                if isinstance(exc, httpx.TimeoutException):
                    raise PenfieldError(_NOT_ANSWERED) from exc
                raise PenfieldError(f"Could not reach Penfield: {type(exc).__name__}") from exc
            if resp.status_code == 401 and attempt == 0:
                continue
            if resp.status_code >= 400:
                raise _error_from(resp)
            if resp.status_code == 204 or not resp.content:
                return None
            try:
                body = resp.json()
            except ValueError as exc:
                raise PenfieldError("Penfield returned a response that is not JSON.") from exc
            return body.get("data", body) if isinstance(body, dict) else body
        raise PenfieldError("Penfield rejected the access token.", 401)
