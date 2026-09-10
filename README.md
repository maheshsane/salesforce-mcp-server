# Salesforce Cloud MCP Server

A Model Context Protocol (MCP) server that connects Claude directly to a
live Salesforce org — Sales Cloud, Service Cloud, and (separately)
Marketing Cloud — so you can query accounts, pipeline, cases, campaigns,
and journeys in plain language, across Sales, PreSales, Marketing,
Support, and Customer Success: top of the funnel to bottom of the
funnel, one server.

Each user runs this locally against their **own** org. Nothing is hosted
centrally and no credentials ever leave your machine.

**New to this repo?** Start with `GETTING_STARTED.md` — the steps in
the order to actually do them in, sandbox through production.
`docs/FUNCTIONALITY_GUIDE.md` is the deeper reference once you're
underway: what Sales, PreSales, Marketing, and CS can each do once
it's live, what to edit for your org's specific schema before
rollout, and what you need in Claude to use it (short answer: not a
Project).

## Two ways to run this

| | Local (`server.py`) | Remote (`server_remote.py`) |
|---|---|---|
| Who it's for | One person, their own laptop | A team sharing one always-on connection |
| Transport | stdio (Claude Desktop launches it as a subprocess) | Streamable HTTP (a normal web service) |
| Where credentials live | `.salesforce_token.json` on your machine | A secret in your cloud provider's secret manager |
| Setup | `python3 setup_salesforce_auth.py`, point Claude Desktop's config at the file | Same auth step once, then deploy the container — see "Deploying to the cloud" |
| Needs your laptop running | Yes | No — it's just a URL |

Same tools, same `tools/` modules, same `sf_client.py`/`mc_client.py` either
way. The only thing that changes between the two is the transport layer
and where the Salesforce session lives — nothing about *what* the server
can do.

## Why two connections, not three

Sales Cloud, Service Cloud, and now **Data 360** (formerly Data Cloud)
all run through one core Salesforce REST API and one OAuth login —
that's `sf_client.py` for objects and `dc_client.py` for Data 360's SQL
queries, but the same session underneath, so Data 360 needs zero extra
setup beyond what `setup_salesforce_auth.py` already did.
**Marketing Cloud genuinely is a separate Salesforce product** with its
own subdomain, its own API, and its own server-to-server auth — that's
`mc_client.py`. You can set up Marketing Cloud, skip it, or add it
later, and Claude will only see its tools once it's configured.

## Architecture

Tools are organized one file per function in `tools/`, all sharing the
same three API clients (`sf_client.py`, `dc_client.py`, `mc_client.py`).
Adding a tool — or a whole new domain — never requires touching auth or
the other domains; see `CONTRIBUTING.md`.

```
Claude Desktop (MCP Client)                  Any MCP-over-HTTP client
        │  stdio, one subprocess                    │  HTTPS, one URL
        ▼                                            ▼
server.py                                    server_remote.py
(runs tools/*, mcp.run())              (runs tools/*, Streamable HTTP +
        │                                bearer-token check + /health)
        └──────────────┬─────────────────────────────┘
                        ▼
        ├── tools/core.py               ─┐
        ├── tools/sales.py               │  built on sf_client.py
        ├── tools/presales.py            │  (SOQL, describe,
        ├── tools/marketing.py           │   any object)
        ├── tools/support.py             │
        ├── tools/customer_success.py   ─┘
        │
        ├── tools/data_cloud.py         ──  dc_client.py
        │                                   (Data 360 SQL — same
        │                                    session as sf_client.py)
        │
        └── tools/marketing_cloud.py    ──  mc_client.py
                                             (Data Extensions, Journeys)
        │                                        │
        ▼                                        ▼
Sales/Service Cloud/Data 360                Marketing Cloud
 REST API, OAuth                             REST API, OAuth
 (Authorization Code                         (Client Credentials —
  + PKCE — browser                             server-to-server,
  login, per user)                             no browser)
```

## Try it without a live org first

`demo_server.py` exposes the same tool shapes against fictional local
data in `data/demo_salesforce.json` — no Salesforce credentials needed.
Point Claude Desktop's config at `demo_server.py` instead of `server.py`
to explore before connecting a real org.

## Setup

### 1. Install dependencies
```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

### 2. Connect Sales Cloud / Service Cloud

Create a Connected App in your org: **Setup → App Manager → New Connected App**
- Enable OAuth Settings
- Callback URL: `http://localhost:8765/callback`
- Selected OAuth Scopes: `api`, `refresh_token`, `offline_access`
- Check **Require Proof Key for Code Exchange (PKCE)**
- Save, then copy the **Consumer Key** into `SF_CLIENT_ID` in `.env`
  (leave `SF_CLIENT_SECRET` blank if you enabled PKCE without a secret)

Then run:
```bash
python3 setup_salesforce_auth.py
```
This opens a browser, you log in and approve access once, and a refresh
token is saved locally to `.salesforce_token.json` (gitignored — never
commit this). The server refreshes it silently after that.

### 3. Connect Marketing Cloud (optional)

Create an API Integration package: **Setup → Apps → Installed Packages →
New**, choose "API Integration" with a server-to-server grant. Copy the
Client ID, Client Secret, and your tenant subdomain into `.env` as
`MC_CLIENT_ID`, `MC_CLIENT_SECRET`, `MC_SUBDOMAIN` (and `MC_ACCOUNT_ID` if
you use Business Units).

Then run:
```bash
python3 setup_marketing_cloud_auth.py
```

### 4. Point Claude Desktop at this server

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent on your OS:
```json
{
  "mcpServers": {
    "salesforce-cloud": {
      "command": "python3",
      "args": ["/absolute/path/to/salesforce-mcp-server/server.py"]
    }
  }
}
```
Restart Claude Desktop. You should see the tool count increase.

## Deploying to the cloud (remote variant)

Before deploying anywhere, test `server_remote.py` locally first:
```bash
python3 server_remote.py
curl http://localhost:8080/health   # should return "ok"
```
If you see `RuntimeError: Task group is not initialized` on an actual
MCP request (not `/health`), that's a real, previously-confirmed SDK
gotcha with mounting `streamable_http_app()` — this repo already wires
the fix (an explicit `lifespan` that runs `mcp.session_manager.run()`
in `server_remote.py`), so seeing it would mean your installed `mcp`
package predates the `session_manager` attribute; run
`pip install --upgrade mcp` and retry.

Use this when you want a shared, always-on connection instead of
something that only works while your laptop is running. All three
clouds below follow the same shape:

1. **Authenticate locally, once.** Even for a cloud deployment, run
   `python3 setup_salesforce_auth.py` on your own machine first — it's
   the only step that needs a browser. Open the `.salesforce_token.json`
   it creates and copy the `refresh_token` value.
2. **Set secrets in the cloud, not in the image.** You'll set four
   things as platform secrets/env vars on whichever service you pick:
   `SF_CLIENT_ID`, `SF_REFRESH_TOKEN` (the value you just copied),
   `MCP_SHARED_SECRET` (any long random string — generate one with
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`),
   and, if you're using it, the `MC_*` Marketing Cloud variables.
3. **Build and push the container.**
   ```bash
   docker build -t salesforce-mcp-server .
   ```
4. **Deploy** (provider-specific steps below).
5. **Point your MCP client at `https://<your-service-url>/`**, sending
   `Authorization: Bearer <your MCP_SHARED_SECRET>` on every request.
   Claude.ai's custom connectors and Claude Desktop's remote server
   support both let you set a bearer token when adding an HTTP MCP
   server — check `docs.claude.com` for the current steps in whichever
   client you're connecting from, since that UI does change.

Salesforce refresh tokens don't rotate on each use by default, so a
static secret is fine for the common case. If your org's Connected App
policy does rotate them, you'll need to re-run step 1 periodically and
update the secret — a fully self-refreshing setup would need a small
database to persist rotated tokens, which is a reasonable next step if
you outgrow this (see `CONTRIBUTING.md`).

**On access control:** `MCP_SHARED_SECRET` is a floor, not a ceiling.
It stops a stranger who finds the URL from calling your Salesforce
data, but it's one static string, not real per-user authentication.
For anything beyond a small trusted team, also restrict access at the
network/IAM layer using whichever mechanism your cloud offers (below),
and treat this deployment as reachable only by people you'd trust with
direct Salesforce API access.

### AWS — App Runner

App Runner is the least infrastructure for a single always-on
container: it builds TLS termination and a public HTTPS URL in for you.

1. Push the image to ECR:
   ```bash
   aws ecr create-repository --repository-name salesforce-mcp-server
   aws ecr get-login-password | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
   docker tag salesforce-mcp-server:latest <account-id>.dkr.ecr.<region>.amazonaws.com/salesforce-mcp-server:latest
   docker push <account-id>.dkr.ecr.<region>.amazonaws.com/salesforce-mcp-server:latest
   ```
2. Store the secrets in AWS Secrets Manager (`aws secretsmanager create-secret ...`),
   one per variable, or one JSON secret with all of them.
3. Create the App Runner service, either via the console (Source: this
   ECR image) or `aws apprunner create-service`. Under
   Configuration → Environment variables, add non-secret vars
   (`PORT` is set for you) and reference each Secrets Manager secret
   for `SF_CLIENT_ID`, `SF_REFRESH_TOKEN`, `MCP_SHARED_SECRET`.
4. App Runner gives you an `https://<random>.<region>.awsapprunner.com`
   URL with TLS already handled.
5. For network-layer restriction beyond the bearer token: put App
   Runner behind a VPC Ingress Connection restricted to a VPN/VPC, or
   front it with API Gateway and an API key/usage plan.

*Already running ECS?* The same image works fine on **ECS Fargate**
behind an **Application Load Balancer** instead — same secrets, same
env vars, more setup (task definition, service, ALB target group,
security groups) in exchange for more control (VPC placement, existing
CI/CD, etc.).

### Google Cloud — Cloud Run

Cloud Run is the simplest of the three — one command, HTTPS and
scale-to-zero included.

1. Build and push with Cloud Build (no local Docker needed):
   ```bash
   gcloud builds submit --tag gcr.io/<project-id>/salesforce-mcp-server
   ```
2. Store secrets in Secret Manager:
   ```bash
   echo -n "<value>" | gcloud secrets create SF_REFRESH_TOKEN --data-file=-
   # repeat for SF_CLIENT_ID, MCP_SHARED_SECRET, and any MC_* variables
   ```
3. Deploy:
   ```bash
   gcloud run deploy salesforce-mcp-server \
     --image gcr.io/<project-id>/salesforce-mcp-server \
     --set-secrets SF_CLIENT_ID=SF_CLIENT_ID:latest,SF_REFRESH_TOKEN=SF_REFRESH_TOKEN:latest,MCP_SHARED_SECRET=MCP_SHARED_SECRET:latest \
     --no-allow-unauthenticated
   ```
4. `--no-allow-unauthenticated` means Cloud Run's own IAM sits in front
   of the bearer-token check — callers also need a Google-signed
   identity token (`gcloud auth print-identity-token`) or an
   `invoker`-role service account. If your MCP client can't send that,
   use `--allow-unauthenticated` instead and rely on `MCP_SHARED_SECRET`
   alone — the honest tradeoff between the two is convenience vs. a
   second layer of access control.

### Azure — Container Apps

Container Apps is Azure's closest match to Cloud Run/App Runner:
managed HTTPS, scale-to-zero, no cluster to run yourself.

1. Push the image to Azure Container Registry:
   ```bash
   az acr build --registry <your-registry> --image salesforce-mcp-server:latest .
   ```
2. Store secrets:
   ```bash
   az containerapp secret set --name salesforce-mcp-server \
     --resource-group <your-rg> \
     --secrets sf-refresh-token=<value> mcp-shared-secret=<value>
   ```
3. Create/update the app, wiring secrets to env vars:
   ```bash
   az containerapp create \
     --name salesforce-mcp-server --resource-group <your-rg> \
     --image <your-registry>.azurecr.io/salesforce-mcp-server:latest \
     --target-port 8080 --ingress external \
     --secrets sf-client-id=<value> sf-refresh-token=<value> mcp-shared-secret=<value> \
     --env-vars SF_CLIENT_ID=secretref:sf-client-id SF_REFRESH_TOKEN=secretref:sf-refresh-token MCP_SHARED_SECRET=secretref:mcp-shared-secret
   ```
4. For network-layer restriction: add an IP restriction under
   Networking, or put Easy Auth (Azure AD) in front for real
   per-user authentication instead of relying on the shared secret
   alone.

## Tools exposed

**Core (`tools/core.py`)** — the foundation everything else is built on

| Tool | Description |
|---|---|
| `sf_list_objects` | List every object this user can query |
| `sf_describe_object` | List fields and picklist values on an object |
| `sf_query` | Run any SOQL query against any object |
| `sf_get_open_opportunities` | Open pipeline with stage, amount, close date |

**Sales (`tools/sales.py`)**

| Tool | Description |
|---|---|
| `sf_get_pipeline_summary` | Open deal count and total amount, grouped by stage |
| `sf_get_top_deals` | Largest open deals by amount |
| `sf_get_forecast` | Open deals closing within N days |
| `sf_get_leads` | Leads filtered by status/source |
| `sf_log_activity_note` | **MUTATING** — logs a Task (call note, follow-up) on any record |
| `sf_update_opportunity` | **MUTATING** — updates stage, amount, close date, or next step |
| `sf_delete_task` | **MUTATING, DESTRUCTIVE** — deletes a Task (Recycle Bin, ~15 days recoverable) |
| `sf_delete_lead` | **MUTATING, DESTRUCTIVE** — deletes a Lead (Recycle Bin, ~15 days recoverable) |

**PreSales (`tools/presales.py`)**

| Tool | Description |
|---|---|
| `sf_get_opportunity_detail` | Full deal context: stage, next steps, contact roles |
| `sf_get_opportunity_products` | Products/line items quoted on a deal |
| `sf_get_account_360` | One-call full picture: account, opps, cases, contacts, recent activity |
| `sf_create_contact` | **MUTATING** — adds a new stakeholder/contact to an account |
| `sf_update_contact` | **MUTATING** — updates a contact's title, email, or phone |

**Marketing (`tools/marketing.py`)** — core Salesforce Campaigns

| Tool | Description |
|---|---|
| `sf_get_campaigns` | Campaigns with lead/opportunity/revenue rollups |
| `sf_get_campaign_performance` | Cost-per-lead, cost-per-opportunity, ROI multiple for one campaign |
| `sf_get_campaign_members` | Leads/Contacts on a campaign by member status |
| `sf_get_leads_by_source` | Lead volume and conversion, grouped by source |

**Support (`tools/support.py`)** — working the queue

| Tool | Description |
|---|---|
| `sf_get_open_cases` | Open support cases by priority |
| `sf_create_case` | **MUTATING** — opens a new support Case on an account |
| `sf_update_case` | **MUTATING** — resolves, escalates, or annotates an existing Case |

**Customer Success (`tools/customer_success.py`)** — working the relationship

| Tool | Description |
|---|---|
| `sf_get_renewals_due` | Open opportunities closing within N days |
| `sf_get_at_risk_accounts` | Best-effort risk view: low probability + open high-priority cases |
| `sf_get_account_health` | Fast health snapshot: case load, last activity, open opps |

**Data 360 (`tools/data_cloud.py`)** — same connection as core Salesforce, no extra setup

| Tool | Description |
|---|---|
| `dc_query` | ANSI SQL query over unified profile and data lake objects |

**Marketing Cloud (`tools/marketing_cloud.py`)** — separate product, separate connection

| Tool | Description |
|---|---|
| `mc_list_data_extensions` | List Data Extensions (subscriber/campaign tables) |
| `mc_query_data_extension` | Read rows from a Data Extension |
| `mc_list_journeys` | List Journey Builder journeys |
| `mc_upsert_data_extension_rows` | **MUTATING** — inserts/updates Data Extension rows (no row-delete tool exists — see below) |

A few of these (`sf_get_at_risk_accounts`, `sf_get_renewals_due`) only
know about standard Opportunity/Case fields. If your org tracks health
score, NPS, or renewal date on a custom field, use `sf_describe_object`
to find it and query it directly with `sf_query` — Claude can build
that SOQL for you. Want a tool this list doesn't have? See
`CONTRIBUTING.md` — it's a five-minute addition.

## What's read-only vs. writable, per system

This isn't symmetric across the three connections, and it's worth
understanding why before you assume a capability exists.

**Salesforce (Sales/Service Cloud): full CRUD is available at the
client layer** (`sf_client.py` has `query`, `describe_object`,
`create`, `update`, `delete`) — the REST API genuinely supports all
four. What's actually *exposed as a tool* is deliberately narrower:
specific, named operations (update an Opportunity's stage, create a
Case, delete a Task) rather than a generic "update any object" or
"delete any object" tool. A generic version would let Claude modify
or delete *any record in any object* the connected user can touch,
driven by how it interpreted a sentence — a materially bigger blast
radius than a handful of named, documented operations. Account,
Opportunity, Case, and Contact deletion are deliberately **not**
exposed as tools here; those are higher-stakes records than a Task or
a duplicate Lead, and adding delete for them is a decision to make
deliberately, not a default. See `CONTRIBUTING.md`'s mutating-tool
convention if you want to add one.

**Marketing Cloud: upsert exists, row-level delete doesn't.**
`mc_upsert_data_extension_rows` inserts or updates rows. There's
deliberately no matching delete tool — Marketing Cloud's REST API
doesn't expose one for individual rows; the only REST delete
available operates on an *entire Data Extension*, not a row, which is
a different and far more destructive operation. Row-level delete
exists only via the older SOAP API, which isn't implemented here.

**Data 360: read-only, and that's the platform's own design, not a
gap in this repo.** Data 360's Query/Connect API is read-only by
Salesforce's own design, aside from a separate Ingestion API for
loading data in bulk (not implemented here — that's a materially
different, heavier integration than editing a record). You don't
"update a row" in Data 360 the way you update a Salesforce Contact;
data arrives through ingestion pipelines, and segments/activations
have their own dedicated build/publish workflow. If you need to
*manage* Data 360 (build segments, trigger activations) rather than
query it, Salesforce's own hosted Data 360 MCP server is the right
tool for that job — see `dc_client.py`'s docstring for more on how
the two are complementary rather than overlapping.

## Example prompts

**Sales:** "Summarize my pipeline by stage, then show me the 10 biggest open deals."

**PreSales:** "Give me the full picture on the Acme Corp opportunity before my demo tomorrow — stage, contacts involved, and what products are on the quote."

**Marketing:** "Which campaigns from this quarter had the best ROI, and which lead sources are converting best in the last 90 days?"

**Support:** "What's open in the queue right now by priority? Open a new case for the outage they just reported and mark it Critical."

**Customer Success:** "Pull all open opportunities closing in the next 60 days with less than 50% probability, cross-referenced with open high-priority cases. Then log a follow-up task on the riskiest one."

**Cross-functional:** "Give me the account 360 for Acme Corp — I want cases, open deals, contacts, and recent activity in one shot before the QBR."

**Marketing Cloud:** "List our Marketing Cloud journeys and cross-reference with accounts that have Opportunities in Negotiation."

## Security notes

- `.env`, `.salesforce_token.json`, and `.mc_token.json` are all
  gitignored — double check before committing that neither shows up in
  `git status`. The `.dockerignore` excludes them from the container
  image too.
- Refresh tokens and MC client secrets are read only from environment
  variables or the local token cache — never hardcoded in source.
- Revoke access anytime from Salesforce Setup → Connected Apps OAuth
  Usage, or Marketing Cloud → Installed Packages. Revoking also
  invalidates any `SF_REFRESH_TOKEN` you've copied into a cloud secret.
- 9 of the 32 tools are **mutating** — see "What's read-only vs.
  writable, per system" above for the full list and the reasoning
  behind what's deliberately *not* exposed (generic update/delete,
  Account/Opportunity/Case/Contact deletion, Marketing Cloud row
  delete, any Data 360 write). If you add new tools (see
  `CONTRIBUTING.md`), keep that same split visible: mutating tools
  should say so plainly in their docstring, and destructive ones
  (delete) should say that too.
- The remote variant is a normal web service the moment you deploy it
  — anyone with the URL and the bearer token can call your Salesforce
  tools. `MCP_SHARED_SECRET` is required before you deploy anywhere
  network-reachable (`server_remote.py` prints a warning and still
  runs without it, which is only meant for local testing behind a
  firewall). Layer network/IAM restrictions on top per the cloud
  sections above for anything beyond a small trusted team.
