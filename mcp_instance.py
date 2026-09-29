"""
Shared MCP server instance.

Every tool module in tools/ imports `mcp` from here and registers its
tools with `@mcp.tool()`. server.py imports the tool modules (for their
registration side effects) and then calls mcp.run(). This is the seam
to build on: a new domain module just needs `from mcp_instance import mcp`
and its own `@mcp.tool()` functions — nothing else changes.
"""

from mcp.server import MCPServer

mcp = MCPServer(
    "salesforce-cloud",
    instructions=(
        "This org uses one shared Salesforce login, so every record's Owner field "
        "shows the same person. Rep and owner names for filtering live in two custom "
        "text fields on Opportunity instead: Sales_Rep_Name__c and "
        "PreSales_Owner_Name__c. When someone asks about a person's deals, filter on "
        "one of those fields, not on Owner or on a Salesforce User or Contact record."
    )
)