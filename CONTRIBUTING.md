# Adding a new tool

Every tool lives in `tools/<domain>.py`, one file per function (sales,
presales, marketing, customer_success, marketing_cloud, core). Adding a
new tool — or a whole new domain — never requires touching auth,
`sf_client.py`, or the other domain files.

## Adding a tool to an existing domain

Open the relevant file in `tools/` and add a function:

```python
@mcp.tool()
def sf_get_something(some_filter: str = "") -> str:
    """
    One clear paragraph: what this returns, and when to use it instead
    of a nearby tool. This docstring is what Claude reads to decide
    whether to call it — be specific about the objects and fields
    involved.
    """
    soql = f"SELECT Id, Name FROM SomeObject WHERE Field = '{some_filter}'"
    return json.dumps(sf_client.query(soql), indent=2)
```

That's it — importing the module in `server.py` registers it.

## Adding a whole new domain

1. Create `tools/your_domain.py`:
   ```python
   import json
   import sf_client
   from mcp_instance import mcp

   @mcp.tool()
   def sf_your_tool(...) -> str:
       """..."""
       ...
   ```
2. Add one line to `server.py`: `from tools import your_domain`.
3. Add it to the tool table in `README.md`.

## Conventions worth keeping

- **Prefix by system, not by team.** `sf_` for core Salesforce
  (Sales/Service Cloud), `mc_` for Marketing Cloud — they're genuinely
  separate APIs and auth, and the prefix makes that visible to anyone
  reading the tool list.
- **Read tools return data. Nothing else.** If a tool mutates
  Salesforce (creates/updates/deletes a record), say **MUTATING** as
  the first word of its docstring, like `sf_log_activity_note` does.
  MCP hosts and the people using them treat those calls with more
  caution, and they should be able to tell which tools are which
  without reading the code.
- **Prefer standard fields over org-specific ones.** Custom fields
  (`__c`) vary org to org, so a tool built on one org's custom
  "health score" field won't work for anyone else who clones this
  repo. Where you do need a custom field, say so in the docstring and
  point people at `sf_describe_object` to find their org's equivalent,
  the way `sf_get_at_risk_accounts` does.
- **SOQL injection.** Every filter value here gets f-string'd directly
  into SOQL, matching the simplicity of the rest of the codebase — this
  is a local, single-user tool where the "attacker" would be the user's
  own Claude session. If you extend this into anything multi-tenant or
  network-exposed, escape or parameterize filter values before they
  reach `sf_client.query`.
- **Pagination and limits.** `sf_client.query` already follows
  Salesforce's `nextRecordsUrl` for you — don't re-implement paging.
  Do keep a sane `LIMIT` in new queries so a broad filter can't return
  an unbounded result set into the conversation.
