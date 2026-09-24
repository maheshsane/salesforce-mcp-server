#!/usr/bin/env python3
"""
Salesforce Cloud MCP Server — remote/cloud entrypoint.

Same tools, same auth modules, same clients as server.py (the local
variant) — the only thing that differs is the transport: this one
speaks MCP over Streamable HTTP so it can run as an always-on web
service instead of a subprocess Claude Desktop launches on your laptop.

Run it directly for local testing:
    python3 server_remote.py
Or via the Dockerfile for any of the cloud deployment paths in
README.md (Cloud Run, App Runner, Container Apps).

NOTE ON THE MCP SDK CALL BELOW: this targets mcp SDK v2 (see
requirements.txt's `mcp>=2.0.0,<3.0.0` pin) — `mcp_instance.py` builds
`mcp` via `from mcp.server import MCPServer`, v2's renamed successor to
v1's `FastMCP`. Per the SDK's own v1-to-v2 migration guide,
`streamable_http_app()` and `session_manager` kept their names and
behavior across that rename, so the `hasattr()` checks below work
against either an `MCPServer` (v2) or `FastMCP` (v1, if you've pinned
this project back to that line) instance without caring which built
the object — that's deliberate, not a workaround.

A CONFIRMED GOTCHA, fixed below: mounting `mcp.streamable_http_app()`
under a bare `Mount("/", ...)` fails at the first real request with
`RuntimeError: Task group is not initialized. Make sure to use run().`
— Starlette doesn't propagate a mounted sub-app's lifespan on its own,
so the session manager's task group never starts. This was confirmed
and fixed in the SDK's own docs (modelcontextprotocol/python-sdk
issues #1467 / #1484), and remains required, unchanged, under v2: wire
an explicit `lifespan` on the parent Starlette app that runs
`mcp.session_manager.run()`, exactly as the v2 migration guide's own
example does.
"""

import contextlib
import os

from dotenv import load_dotenv

load_dotenv()

from mcp_instance import mcp

# Import order doesn't matter — each import's only effect is registering
# that module's @mcp.tool() functions. Same tool set as the local variant.
from tools import core          # noqa: F401
from tools import sales         # noqa: F401
from tools import presales      # noqa: F401
from tools import marketing     # noqa: F401
from tools import support       # noqa: F401
from tools import customer_success  # noqa: F401
from tools import data_cloud        # noqa: F401
from tools import marketing_cloud   # noqa: F401

SHARED_SECRET = os.environ.get("MCP_SHARED_SECRET")


class BearerAuthMiddleware:
    """
    Minimum-viable access control: requires a matching bearer token on
    every HTTP request except /health. This is a floor, not a ceiling —
    on a real deployment, also restrict access at the network/IAM layer
    (see README's per-cloud sections). If MCP_SHARED_SECRET isn't set,
    this middleware is a no-op, which is only appropriate for local
    testing behind a firewall — never for a publicly reachable URL.

    Only inspects scope["type"] == "http"; "lifespan" scope events pass
    straight through untouched, so this sits safely outside the
    session-manager lifespan wiring in _build_app() below.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not SHARED_SECRET or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()
        if auth_header != f"Bearer {SHARED_SECRET}":
            from starlette.responses import PlainTextResponse
            response = PlainTextResponse("Unauthorized", status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _build_app():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount, Route

    async def health(_request):
        return PlainTextResponse("ok")

    if hasattr(mcp, "streamable_http_app"):
        mcp_app = mcp.streamable_http_app()

        # Required — see the module docstring's "CONFIRMED GOTCHA" note.
        # Without this, every real request 500s with "Task group is not
        # initialized" because the session manager's task group never starts.
        @contextlib.asynccontextmanager
        async def lifespan(_app):
            async with mcp.session_manager.run():
                yield

        app = Starlette(
            routes=[
                Route("/health", health),
                Mount("/", app=mcp_app),
            ],
            lifespan=lifespan,
        )
    elif hasattr(mcp, "sse_app"):
        # SSE is the legacy transport (superseded by Streamable HTTP) and
        # doesn't have the same session-manager lifespan requirement, so no
        # extra wiring needed here — but prefer streamable_http_app() above
        # if your SDK version has it.
        app = Starlette(routes=[
            Route("/health", health),
            Mount("/", app=mcp.sse_app()),
        ])
    else:
        raise RuntimeError(
            "Installed mcp SDK doesn't expose streamable_http_app() or sse_app(). "
            "Run `pip show mcp` and check the SDK's docs for the current HTTP-serving "
            "API, then update _build_app() in server_remote.py."
        )

    return BearerAuthMiddleware(app)


if __name__ == "__main__":
    import uvicorn

    if not SHARED_SECRET:
        print(
            "WARNING: MCP_SHARED_SECRET is not set. This server will accept "
            "unauthenticated requests. Fine for local testing; set it before "
            "deploying anywhere network-reachable.",
        )

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(_build_app(), host="0.0.0.0", port=port)
