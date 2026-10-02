# RET-C2-586 - Connector contract tests for supplier_db_adapter.
#
# Proves the deployable request path acquires the supplier DB credential via the
# bound secret provider (never os.environ / never in State), that production is
# mandatory (a missing/empty secret raises rather than serving empty results),
# and that the stub is strictly opt-in.

import json

import pytest

from src.services.supplier_db_adapter import (
    SupplierDBClient,
    SupplierDBConfigError,
    build_supplier_db_client,
)

_STUB_FLAG = "RET_C2_586_ALLOW_STUB_SUPPLIER_DB"


class _Secrets:
    def __init__(self, values):
        self._values = values

    def require(self, key):
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]


def test_live_build_ok():
    secrets = _Secrets({"SUPPLIER_DB_TOKEN": "tok", "SUPPLIER_DB_URL": "https://db.example"})
    client = build_supplier_db_client(secrets, allow_stub=False)
    assert isinstance(client, SupplierDBClient)


def test_missing_secret_raises():
    secrets = _Secrets({"SUPPLIER_DB_URL": "https://db.example"})  # no token
    with pytest.raises(SupplierDBConfigError):
        build_supplier_db_client(secrets, allow_stub=False)


def test_empty_secret_raises():
    secrets = _Secrets({"SUPPLIER_DB_TOKEN": "", "SUPPLIER_DB_URL": ""})
    with pytest.raises(SupplierDBConfigError):
        build_supplier_db_client(secrets, allow_stub=False)


def test_no_provider_raises():
    with pytest.raises(SupplierDBConfigError):
        build_supplier_db_client(None, allow_stub=False)


def test_stub_requires_explicit_flag(monkeypatch):
    monkeypatch.delenv(_STUB_FLAG, raising=False)
    # allow_stub=True but flag unset → still production (must raise without secrets).
    with pytest.raises(SupplierDBConfigError):
        build_supplier_db_client(None, allow_stub=True)


def test_stub_opt_in_returns_records(monkeypatch):
    monkeypatch.setenv(_STUB_FLAG, "1")
    records = [{"sku_id": "SKU-9", "supplier_id": "SUP-A", "category": "frozen"}]
    client = build_supplier_db_client(None, allow_stub=True, stub_records=records)
    assert client.query({"supplier_ids": ["SUP-A"]}) == records


def test_token_held_privately_only():
    # The client holds the token on a private attribute only; no public
    # attribute exposes it (defence-in-depth against leaking into a State dict).
    secrets = _Secrets({"SUPPLIER_DB_TOKEN": "tok", "SUPPLIER_DB_URL": "https://db.example"})
    client = build_supplier_db_client(secrets, allow_stub=False)
    public_values = [v for k, v in vars(client).items() if not k.startswith("_")]
    assert "tok" not in public_values


# ---- live transport is implemented (not an unconditional raise) -------------

class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_live_query_returns_records(monkeypatch):
    from src.services import supplier_db_adapter as mod

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        return _FakeResp({"records": [{"sku_id": "SKU-1", "supplier_id": "SUP-A"}]})

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    client = mod.SupplierDBClient(token="tok", base_url="https://db.example")
    rows = client.query({"supplier_ids": ["SUP-A"]})
    assert rows == [{"sku_id": "SKU-1", "supplier_id": "SUP-A"}]
    assert captured["url"] == "https://db.example/query"
    assert captured["auth"] == "Bearer tok"


def test_live_query_transport_error_raises(monkeypatch):
    import urllib.error

    from src.services import supplier_db_adapter as mod

    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    client = mod.SupplierDBClient(token="tok", base_url="https://db.example")
    with pytest.raises(mod.SupplierDBRequestError):
        client.query({"supplier_ids": ["SUP-A"]})
