# What This Actually Gives You, Once It's Connected to a Live Org

This document answers three questions: what each team can actually do
once this is running against your real Salesforce data, what you need
to edit before that's true (every org's schema differs), and what you
personally need to have set up in Claude to use it day to day.

Everything below assumes the setup in `README.md` is done —
`sf_query`/`sf_describe_object` always work against any org; the
convenience tools below work well in orgs that use mostly standard
fields, and need the edits in Part 2 wherever an org has customized
its schema.

---

## Part 1 — What each team can do

### Sales

Once connected, a rep or sales manager can ask Claude things like:

- **"Summarize my pipeline by stage."** → `sf_get_pipeline_summary` pulls every open deal, groups it by stage, and totals the amount — the number you'd otherwise build a report for.
- **"What are my 10 biggest open deals?"** → `sf_get_top_deals` ranks by amount, with owner and close date, so a manager can ask this about the whole team just by not filtering to themselves.
- **"What's likely to close this month?"** → `sf_get_forecast` returns everything closing in the window you ask for.
- **"Which new leads came in from webinars this week, and are any of them still unworked?"** → `sf_get_leads` filters by source and status.
- **"Log a note on the Acme deal — they want a second demo before signing."** → `sf_log_activity_note` writes that back to Salesforce as a Task, so it shows up for anyone else looking at the deal.
- **"Update the Acme opportunity to Negotiation stage."** → `sf_update_opportunity` changes just the fields you name (stage, amount, close date, next step) — everything else on the record is left untouched.
- **"That lead looks like a duplicate, delete it."** → `sf_delete_lead` removes it (recoverable from Salesforce's Recycle Bin for about 15 days if it turns out to be wrong).

**What this replaces:** pulling a report, exporting to a spreadsheet, or tabbing over to Salesforce mid-conversation to check a number — or to make a small update. The rep stays in the conversation they're already having.

**Where it falls short:** there's no forecast *category* rollup (Salesforce's Commit/Best Case/Pipeline classification) because that's a metadata concept, not a plain field — `sf_get_forecast` gives you the raw deals in the window and lets Claude do the summarizing instead.

### PreSales

- **"Give me everything on the Acme Corp deal before my call tomorrow."** → `sf_get_opportunity_detail` returns stage, next steps, and — critically — who's actually involved on the customer side (`OpportunityContactRole`), so you're not walking in blind on who's the champion versus who's evaluating.
- **"What's actually being quoted?"** → `sf_get_opportunity_products` lists the line items — product, quantity, price — so your demo matches what's on the table instead of a generic walkthrough.
- **"Give me the full picture on this account before the QBR."** → `sf_get_account_360` is the one to reach for when you want everything in one shot: account details, every open deal, every open case, every contact, and the last 10 logged activities. This is the tool built specifically so you don't have to make five separate asks.
- **"I just talked to their new VP of Ops — add her as a contact."** → `sf_create_contact` adds a new stakeholder to the account without switching tabs.
- **"They just changed roles — update their title on the account."** → `sf_update_contact` changes title, email, or phone, only what you name.

**What this replaces:** the 15 minutes before a call spent clicking through Salesforce tabs to reconstruct context that's scattered across the Opportunity, its related lists, and whatever Cases happen to be open.

**Where it falls short:** if your org tracks a formal PreSales workflow — POC scope, technical win criteria, a competitor field — those are almost always custom fields, and none of the built-in tools know about them. You'd ask Claude to `sf_describe_object("Opportunity")` once to find your org's field names, and from then on Claude can query them directly with `sf_query`.

### Marketing

- **"Which campaigns had the best ROI this quarter?"** → `sf_get_campaigns` returns every standard rollup Salesforce tracks — leads generated, opportunities generated, amount won — and `sf_get_campaign_performance` turns one campaign's numbers into cost-per-lead, cost-per-opportunity, and an ROI multiple, the math you'd otherwise do in a spreadsheet.
- **"Who actually showed up to the webinar, and did any of them convert?"** → `sf_get_campaign_members` lists everyone attached to a campaign with their member status (Sent, Responded, Attended — whatever your org's picklist calls it).
- **"Which lead source is converting best right now?"** → `sf_get_leads_by_source` groups recent leads by source with a conversion count per source — the top-of-funnel health check that usually takes a dashboard to answer.

**What this replaces:** a campaign-performance dashboard, or asking a RevOps person to pull the numbers. This works from Salesforce's native Campaign object — if your org's marketing performance data actually lives in Marketing Cloud instead (or in addition), see the Marketing Cloud tools below; they're a genuinely separate connection.

**Where it falls short:** these tools only see Campaigns and CampaignMembers *in core Salesforce*. If your team runs marketing almost entirely out of Marketing Cloud Journeys and Data Extensions, `sf_get_campaigns` will look thin — that's expected, not a bug — use `mc_list_data_extensions`/`mc_list_journeys` instead, once that connection is set up.

### Support

Support and Customer Success are different jobs even though they
share the Case object — Support answers "what's open right now,"
Customer Success answers "who's about to churn." Split into separate
tools deliberately for that reason.

- **"Pull all open cases by priority."** → `sf_get_open_cases`, the thing you'd otherwise check first thing every morning.
- **"Open a case for the outage they just reported."** → `sf_create_case` logs it directly, with a subject, description, and priority.
- **"Mark that case resolved and drop the priority."** → `sf_update_case` changes status/priority/description — only what you name, everything else on the record stays as-is.

**What this replaces:** the morning queue triage, and the habit of switching to Salesforce every time a case needs a status update mid-conversation.

**Where it falls short:** there's no SLA-timer or escalation-rule awareness — these tools see Case fields, not the automation built on top of them. If your org tracks time-to-first-response or breach status on a custom field, describe the Case object to find it and query directly.

### Customer Success

- **"What's renewing in the next 60 days?"** → `sf_get_renewals_due`.
- **"Which accounts are at risk?"** → `sf_get_at_risk_accounts` cross-references low-probability, near-term renewal Opportunities with open High/Critical cases on the same account, and ranks by deal size — the weekly prioritization exercise, done in one call instead of two reports stitched together by hand.
- **"Give me a fast health check on this account before I call them."** → `sf_get_account_health` — lighter than the full 360, good for a quick gut-check between calls.

**What this replaces:** the weekly manual process of cross-referencing a renewal report against an open-case report to figure out which accounts actually need attention this week — which is exactly the workflow this repo started as (see `CONTRIBUTING.md`'s history if you're curious).

**Where it falls short — this is the important one.** `sf_get_at_risk_accounts` only knows about *standard* Opportunity and Case fields: probability, close date, priority. If your org tracks a health score, an NPS number, or a renewal-risk flag on a *custom* field (very common in CS orgs — this repo's original version was built against exactly that kind of custom field at a real company), this tool won't see it. It'll still work, just on a thinner signal than what your team actually tracks day to day. The fix takes one extra step per org — see Part 2.

### Cross-functional

The honest answer to "who is this actually for" is: whoever needs the full picture, not just their function's slice of it. `sf_get_account_360` doesn't care whether you're in Sales, PreSales, Support, or CS — it returns the same account, opportunities, cases, contacts, and activity regardless of who asks. The value of one MCP server instead of five separate dashboards is that a CS manager prepping for a QBR and a support agent triaging a ticket are asking questions about the exact same account, and get the exact same underlying data, in one place, no login-switching required.

**One thing worth being explicit about, since the sections above are organized by function:** that organization is documentation, not access control. Every tool in this server is available in every conversation, regardless of which section above it's filed under. A Customer Success person can call `sf_create_case` (filed under Support) or `sf_update_contact` (filed under PreSales) just as easily as a support agent or an AE can — nothing gates a tool to "its" function. The sections exist to help you find the tool that matches what you're usually doing, not to restrict what you're allowed to ask for.

### Data 360 (formerly Data Cloud)

- **"Is this person in our VIP segment?"** → `dc_query` runs SQL against Data 360's unified profile objects — including segment membership, which turns out to be just a regular queryable object (a Data Model Object Data 360 auto-generates every time a segment publishes), not a separate feature you need new access for.
- **"Who dropped out of the loyalty segment since the last publish?"** → same tool, querying the segment's history object for records with `Delta_Type__c = 'Removed'`.

**What this replaces:** exporting a segment to check who's in it, or asking a Data 360 admin to run a query for you.

**Where it falls short:** there's no describe-style tool yet for finding exact Data 360 object/field names for your org — that's still a Data 360 Setup UI lookup. And this only reads; Data 360 itself doesn't support editing individual records the way Salesforce does (see Part 2's CRUD note below for why).

### Marketing Cloud (only if you've connected it)

- `mc_list_data_extensions` / `mc_query_data_extension` — read subscriber or engagement data straight out of a Data Extension.
- `mc_list_journeys` — see what automated journeys exist, so you can ask Claude to cross-reference "who's currently in a nurture journey" against "who has an open Opportunity in core Salesforce."
- **"Add this person to the newsletter Data Extension."** → `mc_upsert_data_extension_rows` inserts or updates rows — there's no matching delete tool, Marketing Cloud's REST API doesn't cleanly expose one for a single row (see `mc_client.py` for why).

This is a genuinely separate product with its own login and its own data model — it doesn't know anything about your Salesforce Accounts unless you explicitly ask Claude to join the two by hand (e.g. "list journeys, then check which of these email addresses also have Opportunities in Negotiation").

---

## What's read-only vs. writable, and why that's not symmetric

Salesforce (Sales/Service Cloud) supports genuine create/update/delete
— but only specific, narrow, named operations are exposed as tools
(update an Opportunity's stage, not "update anything"), deliberately,
so the blast radius of what Claude can change stays predictable.
Account, Opportunity, Case, and Contact *deletion* aren't exposed at
all — those are higher-stakes records than a Task or a duplicate
Lead. Marketing Cloud supports inserting/updating Data Extension rows
but not deleting a single row through any clean REST endpoint. Data
360 is read-only by the platform's own design — data arrives through
ingestion pipelines, not row edits. None of this is a gap to route
around; see `README.md`'s "What's read-only vs. writable" section for
the full reasoning, and test any mutating tool against a sandbox org
before production, same as everything else in Part 2 below.

---

## Part 2 — What to edit before you deploy this against a real org

Every one of these is a real gap between "this repo as written" and "this actually reflects how your org works." None of them are large edits — most are a `sf_describe_object` call and a one-line change to a query — but skipping this section means the tools quietly work on the wrong assumption instead of failing loudly.

1. **Check your org's actual picklist values.** `sf_get_open_cases(priority="High")` assumes your Case Priority picklist has a value called exactly `"High"`. Some orgs use `"P1"`, `"Urgent"`, or something else entirely. Run `sf_describe_object("Case")` once and look at the `picklist_values` for `Priority` and `Status` — same for `Opportunity.StageName`. Update the default filter values in `tools/sales.py`, `tools/customer_success.py` if your org's values differ from the generic ones baked in.

2. **Find your org's real risk/health fields.** If CS tracks health score, NPS, or a renewal-risk flag on a custom field (`Health_Score__c`, or similar), `sf_get_at_risk_accounts` won't see it as written. Either: (a) accept the standard-fields-only version as a rough proxy, or (b) add a small tool in `tools/customer_success.py` that queries your actual field — `CONTRIBUTING.md` walks through exactly this pattern.

3. **Confirm the OAuth user's permissions.** Whatever Salesforce user completes the browser login in `setup_salesforce_auth.py` is whose permissions every query runs as. If that user's profile or permission set doesn't have field-level access to, say, `Opportunity.Probability` or a custom field, the query won't error — it'll just silently omit that field or return null. Test with a user who has the access level your team actually needs, not just an admin account you happened to be logged in as.

4. **Check your Salesforce edition's API limits.** Every org has a daily API call cap (it scales with edition and license count). Each tool call here is one or a handful of API calls — for a small team's daily use this is a non-issue, but if you're running this against a production org that's already close to its limit from other integrations, check **Setup → System Overview → API Usage** before adding this on top.

5. **Decide on sandbox vs. production first.** Point `SF_LOGIN_URL` at `https://test.salesforce.com` and run the whole setup against a sandbox org before ever pointing this at production — especially before anyone uses `sf_log_activity_note`, the one tool that writes data.

6. **Marketing Cloud Business Units.** If your org uses multiple Business Units, you need `MC_ACCOUNT_ID` (the MID) set to the specific Business Unit you want, or you'll get data from whichever BU your API Integration package defaults to — which may not be the one you meant.

7. **If you're deploying the remote variant, decide on your connector auth model now — see Part 3.** This is the edit most likely to bite you if you skip it, because it's a claude.ai/Claude Desktop product decision, not a code change.

---

## Part 3 — What you'll actually need in Claude to use this

**Short answer: no, you don't need a Claude Project.** Projects are a separate feature — a way to group related conversations under shared instructions and files. They're optional and orthogonal to this server; you can use this MCP server in a Project's conversations or in a plain conversation, identically. What you *do* need depends on which variant you're running:

### Local variant (`server.py`)

You need Claude Desktop installed, and the config edit already described in `README.md` (pointing `claude_desktop_config.json` at the script). No connector, no Project, no claude.ai account settings involved — Claude Desktop launches the script itself as a subprocess. This only works on the machine Claude Desktop is running on.

### Remote variant (`server_remote.py`)

You need to add it as a **Custom Connector** — in claude.ai or Claude Desktop, under **Settings → Connectors → Add custom connector**, paste the deployed URL. This is available on every plan (Free, Pro, Max, Team, Enterprise) — Free is capped at one custom connector total. **On Team or Enterprise, an individual can't add it themselves: an organization Owner has to add it once under Organization Settings → Connectors, and then each person connects individually from there.**

Once added, you enable it per conversation from the "+" / tools menu — it isn't automatically on in every chat, and it isn't tied to a Project; the same toggle exists inside a Project's conversations too if you're using one.

**The part worth deciding deliberately: how this server authenticates to Claude's connector UI.** As of when I checked this, that UI supports three things:

- **OAuth** — the standard, fully-supported path. This server doesn't implement an OAuth *authorization* server for incoming Claude connections (only outgoing OAuth to Salesforce/Marketing Cloud) — building that is a real follow-on project, not a config edit, if you want this option.
- **No auth** — what happens if `MCP_SHARED_SECRET` is unset. Fine for a quick local test; genuinely not appropriate for anything reachable on the public internet.
- **Request headers (beta)** — lets you paste a fixed header like `Authorization: Bearer <your MCP_SHARED_SECRET>` directly into the connector's settings. This is the intended path for this server's current auth model, and it's officially documented — but it's in beta, may not be enabled on every account yet, and there are open reports of it occasionally falling back to a broken OAuth attempt instead of sending the header. If you hit that, it's a known product-side issue, not something wrong with your deployment.

**Claude Code** and direct **API access** (the Messages API's `mcp_servers` parameter, for anyone building their own app on top of this rather than using claude.ai's chat UI) both support custom headers natively today, with no beta flag involved — if the connector-UI beta gives you trouble, connecting via Claude Code first is the more reliable way to confirm the server itself is working before troubleshooting the claude.ai UI specifically.

**Bottom line before you roll this out to a team:** test the exact auth path you plan to use — header-beta or OAuth — with one real user before telling the rest of Sales/CS/Marketing to add it themselves. If header auth isn't reliable on your account yet, either wait, use it via Claude Code in the interim, or treat implementing real OAuth as part of the deployment, not an afterthought.
