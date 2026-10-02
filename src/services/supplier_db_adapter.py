"""AgentCore Platform v1.0 - RET-C2-586 supplier/PO DB connector adapter.

The deployable endpoint must obtain supplier/PO records
from the declared live structured DB, not silently fall back to an empty
in-memory list. This adapter builds a read-only supplier DB client, acquiring
its access token exclusively through the bound InvocationContext secret
provider (S-3: never os.environ, never stored in State).

Production is mandatory: when no client is configured the request path raises
`SupplierDBConfigError` so an unavailable data source becomes an explicit
ERROR rather than a false-negative "No at-risk SKUs found" success. A stub
client is available only when explicitly opted in via the
`RET_C2_586_ALLOW_STUB_SUPPLIER_DB` environment flag (tests / local demos).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_SECRET_KEY = "SUPPLIER_DB_TOKEN"
_STUB_ENV_FLAG = "RET_C2_586_ALLOW_STUB_SUPPLIER_DB"
_DEFAULT_TIMEOUT_S = 10.0


class SupplierDBConfigError(RuntimeError):
    """Raised when the supplier DB connector is not configured for production."""


class SupplierDBRequestError(RuntimeError):
    """Raised when a live supplier DB query fails (timeout / transport error)."""


class SupplierDBClient:
    """Read-only supplier/PO DB client.

    `query(query_scope) -> list[dict]` returns matching PO/delivery records.
    A successful query returning zero rows is a legitimate empty result and is
    distinct from a connector failure (which raises SupplierDBRequestError).
    """

    def __init__(self, token: str, base_url: str, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        # Credential is held only for the lifetime of the request path; never
        # written to State and never logged.
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    def query(self, query_scope: dict[str, Any]) -> list[dict[str, Any]]:
        """Read-only POST to the supplier/PO DB, returning matching records.

        A successful query returning zero rows is a legitimate empty result;
        a transport/HTTP/decode failure raises SupplierDBRequestError so the
        caller surfaces an explicit ERROR rather than a false-negative success.
        """
        body = json.dumps({"query_scope": query_scope}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/query",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            raise SupplierDBRequestError(f"supplier DB query failed: {exc}") from exc
        records = payload.get("records", payload) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise SupplierDBRequestError("supplier DB returned a non-list payload")
        return records


class _StubSupplierDBClient:
    """Opt-in stub: returns caller-injected records for tests/local demos."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self._records = records or []

    def query(self, query_scope: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self._records)


def build_supplier_db_client(
    secrets: Any,
    *,
    allow_stub: bool = False,
    stub_records: list[dict[str, Any]] | None = None,
) -> Any:
    """Build the supplier DB client from the bound secret provider.

    - Production (default): read `SUPPLIER_DB_TOKEN` + `SUPPLIER_DB_URL` via
      `secrets.require()`. A missing secret raises `SupplierDBConfigError`
      instead of silently serving an empty result.
    - Stub: only when `allow_stub=True` AND the env flag is set. Returns a
      client backed by caller-supplied records.
    """
    if allow_stub and os.environ.get(_STUB_ENV_FLAG):
        return _StubSupplierDBClient(records=stub_records)

    if secrets is None:
        raise SupplierDBConfigError("supplier DB secret provider is not bound; cannot build a production client")

    try:
        token = secrets.require(_SECRET_KEY)
        base_url = secrets.require("SUPPLIER_DB_URL")
    except Exception as exc:  # noqa: BLE001 - normalise into a config error
        raise SupplierDBConfigError(f"supplier DB connector missing required secret: {exc}") from exc

    if not token or not base_url:
        raise SupplierDBConfigError("supplier DB token/URL are empty")

    return SupplierDBClient(token=token, base_url=base_url)
