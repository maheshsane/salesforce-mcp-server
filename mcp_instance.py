"""
Shared MCP server instance.

Every tool module in tools/ imports `mcp` from here and registers its
tools with `@mcp.tool()`. server.py imports the tool modules (for their
registration side effects) and then calls mcp.run(). This is the seam
to build on: a new domain module just needs `from mcp_instance import mcp`
and its own `@mcp.tool()` functions — nothing else changes.
"""

from mcp.server import MCPServer

mcp = MCPServer("salesforce-cloud")
