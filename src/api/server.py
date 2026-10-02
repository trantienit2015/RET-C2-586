"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import logging
import os
import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Request
from langgraph.checkpoint.memory import MemorySaver
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from pydantic import BaseModel
from shared.secrets import factory as secrets_factory

from src.services.azure_llm import (
    REQUIRED_SECRET_KEYS,
    LazyAzureOpenAILLM,
    client_available,
)
from src.graph.graph import Graph
from src.services.supplier_db_adapter import build_supplier_db_client

app = FastAPI(title="Agent")

_secrets = secrets_factory(namespace="ret", agent_name="ret-c2-586")

# Build the live supplier DB client from the bound secret provider.
# Production is mandatory — a missing SUPPLIER_DB_TOKEN/URL raises
# SupplierDBConfigError at startup rather than silently serving empty results.
#
# The stub is a TWO-part production boundary: (1) the deployment environment must
# be explicitly non-production (APP_ENV in {test, local, dev}), AND (2) the
# adapter's own RET_C2_586_ALLOW_STUB_SUPPLIER_DB flag must be set. `allow_stub`
# is NOT hard-coded True — a production APP_ENV (or unset) fails closed, so a
# single stray env var can no longer select the empty-record stub on production.
_APP_ENV = os.environ.get("APP_ENV", "production").lower()
_stub_allowed_env = _APP_ENV in ("test", "local", "dev")

with bound_secrets(_secrets):
    _supplier_db_client = build_supplier_db_client(_secrets, allow_stub=_stub_allowed_env)

# Runtime config, same config_dir / "config.yaml" convention AgentRegistry uses;
# an absent file is tolerated and yields {}. The injected client is overlaid on
# top so both the runtime parameters and the adapter reach the graph.
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {} if _CONFIG_PATH.exists() else {}
_config["supplier_db_client"] = _supplier_db_client

# Deployment-provided LLM. The connection values are resolved per call from the
# request-bound secret provider, never from the process environment, so nothing is
# resolved while the graph is being built. The adapter is injected only when the
# provider client exists in this runtime and the values are already resolvable here;
# otherwise the key stays unset and the graph keeps its deterministic path.
_azure_ready = client_available() and all(_secrets.get(key) for key in REQUIRED_SECRET_KEYS)
if _azure_ready:
    _config = {**_config, "llm": LazyAzureOpenAILLM()}
else:
    logging.getLogger(__name__).warning(
        "Azure OpenAI is not available in this runtime — agent boots without an LLM client."
    )

agent = Graph(config=_config)
# memory_enabled / hitl.enabled need a checkpointer, or memory and interrupt()
# silently no-op on this path.
_hitl_enabled = agent.config.get("hitl", {}).get("enabled", False)
_needs_checkpointer = agent.config.get("memory_enabled") or _hitl_enabled
agent.compile(checkpointer=MemorySaver() if _needs_checkpointer else None)
agent.provision_secrets(_secrets)


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


def _bearer_matches(supplied: str, expected: str) -> bool:
    """Constant-time bearer comparison that is safe for non-ASCII header input."""
    return secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode())


def _resolve_standalone_trust(
    current: TrustLevel, authorization: str, invoke_auth_token: str | None, internal_runner_token: str | None
) -> TrustLevel:
    """Authenticate standalone callers without allowing external-token elevation.

    The STG runner credential is a distinct, CI-generated deployment credential. It is
    considered only for an anonymous caller and maps exactly to INTERNAL; the external
    token stays at VERIFIED_EXTERNAL. Middleware-established trust is never changed.
    """
    if current is not TrustLevel.ANONYMOUS:
        return current
    if internal_runner_token and _bearer_matches(authorization, internal_runner_token):
        return TrustLevel.INTERNAL
    if invoke_auth_token and _bearer_matches(authorization, invoke_auth_token):
        return TrustLevel.VERIFIED_EXTERNAL
    if internal_runner_token or invoke_auth_token:
        raise HTTPException(status_code=401, detail="Token is invalid or expired.")
    return TrustLevel.ANONYMOUS


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> dict[str, Any]:
    # This adapter is the entry-point auth boundary (the standalone equivalent of the
    # platform auth middleware). Both values are deployment-level caller credentials, not
    # agent secrets: no InvocationContext exists before this boundary, so ctx.secrets
    # cannot apply.
    trust = _resolve_standalone_trust(
        getattr(request.state, "trust_level", TrustLevel.ANONYMOUS),
        request.headers.get("authorization", ""),
        os.environ.get("INVOKE_AUTH_TOKEN"),
        os.environ.get("STG_INTERNAL_RUNNER_TOKEN"),
    )
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        result = agent.invoke(req.input, ctx=ctx)
    # The graph contract returns a mapping. Verify it at the boundary instead of
    # declaring the shape and trusting it: an unexpected result would otherwise
    # reach the caller as a malformed body.
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="Agent returned an unexpected result shape.")
    return result


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "agent": "ret-c2-586"}
