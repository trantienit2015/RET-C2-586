# RET-C2-586 - The stub must not be selectable on production.
#
# The server derives allow_stub from APP_ENV (non-production only) AND the
# adapter's own flag. A production / unset APP_ENV must fail closed even if the
# adapter stub flag is set.

import importlib

import pytest

pytest.importorskip("fastapi")
# server.py imports shared.secrets, which pulls pydantic_settings/dotenv from the
# framework wheel. Those are present in the CI wheel image but not in a borrowed
# local venv — skip locally, run for real on CI.
pytest.importorskip("pydantic_settings")
pytest.importorskip("dotenv")

from src.services.supplier_db_adapter import SupplierDBConfigError


def _reload_server():
    import src.api.server as server
    return importlib.reload(server)


def test_production_env_cannot_instantiate_stub(monkeypatch):
    # APP_ENV=production (default) + only the adapter stub flag set → must still
    # fail closed: no secrets bound, so build raises rather than serving a stub.
    monkeypatch.setenv("RET_C2_586_ALLOW_STUB_SUPPLIER_DB", "1")
    monkeypatch.delenv("APP_ENV", raising=False)  # defaults to production
    with pytest.raises(SupplierDBConfigError):
        _reload_server()


def test_unknown_env_also_fails_closed(monkeypatch):
    monkeypatch.setenv("RET_C2_586_ALLOW_STUB_SUPPLIER_DB", "1")
    monkeypatch.setenv("APP_ENV", "staging")  # not in {test,local,dev}
    with pytest.raises(SupplierDBConfigError):
        _reload_server()


def test_test_env_with_flag_uses_stub(monkeypatch):
    # Both gates open (APP_ENV=test AND the flag) → stub is allowed.
    monkeypatch.setenv("RET_C2_586_ALLOW_STUB_SUPPLIER_DB", "1")
    monkeypatch.setenv("APP_ENV", "test")
    server = _reload_server()
    assert server.agent is not None  # constructed without live secrets
