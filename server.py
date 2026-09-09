#!/usr/bin/env python3
"""
Salesforce Cloud MCP Server — entrypoint.

Each tool lives in tools/<domain>.py (sales, presales, marketing,
customer_success, marketing_cloud, core) and registers itself against
the shared FastMCP instance in mcp_instance.py just by being imported.
This file's only job is: load config, import every tool module, run.

To add a new tool: write a new tools/<name>.py that does
`from mcp_instance import mcp` and defines `@mcp.tool()` functions, then
add one import line below. See CONTRIBUTING.md.

Setup (once per org): see README.md. In short —
  python3 setup_salesforce_auth.py        # Sales/Service Cloud login
  python3 setup_marketing_cloud_auth.py   # Marketing Cloud credentials (optional)
Then point Claude Desktop's config at this file and restart it.
"""

from dotenv import load_dotenv

load_dotenv()

from mcp_instance import mcp

# Import order doesn't matter — each import's only effect is registering
# that module's @mcp.tool() functions.
from tools import core          # sf_query, sf_describe_object, sf_list_objects, sf_get_open_opportunities
from tools import sales         # pipeline, forecast, leads, top deals, activity logging
from tools import presales      # opportunity detail, products/quote lines, account 360
from tools import marketing     # campaigns, campaign ROI, campaign members, lead source
from tools import customer_success  # open cases, renewals, at-risk accounts, account health
from tools import marketing_cloud   # Marketing Cloud data extensions & journeys (optional)

if __name__ == "__main__":
    mcp.run()
