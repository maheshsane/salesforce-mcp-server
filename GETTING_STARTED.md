# Getting Started — In Order

The other docs in this repo are organized by topic (`README.md` by
variant, `docs/FUNCTIONALITY_GUIDE.md` by team). This one is organized
by **when** — the order to actually do things in, whichever variant
you're deploying. Skipping ahead (especially to step 6) is the most
common way to have a bad first day with this.

## 1. Get a Salesforce sandbox, not production

If you don't already have one: **Setup → Sandboxes → New Sandbox**
(Developer edition is free and enough for this). Do steps 2–8 entirely
against the sandbox. Production comes later, once you trust the
setup — and only after you've decided how you feel about step 5's
one mutating tool.

## 2. Clone the repo and install dependencies

```bash
git clone https://github.com/maheshsane/salesforce-mcp-server.git
cd salesforce-mcp-server
python3 -m pip install -r requirements.txt
cp .env.example .env
```

## 3. Create the Connected App in Salesforce

**Setup → App Manager → New Connected App**, in the sandbox:
- Enable OAuth Settings
- Callback URL: `http://localhost:8765/callback`
- OAuth Scopes: `api`, `refresh_token`, `offline_access`
- Check **Require Proof Key for Code Exchange (PKCE)**
- Copy the **Consumer Key** into `SF_CLIENT_ID` in `.env`

This step is identical whether you end up deploying locally or to the
cloud — the browser login always happens on your machine first (see
`docs/FUNCTIONALITY_GUIDE.md` Part 3 if you want the reason).

## 4. Authenticate once

```bash
python3 setup_salesforce_auth.py
```
Log in, approve access, done. This writes `.salesforce_token.json` —
leave it where it is for now even if you're deploying remotely later;
you'll copy one value out of it in step 8.

## 5. Run the local variant and try it for real

```bash
# point Claude Desktop's config at server.py — see README.md's Setup section
```
Open Claude Desktop and actually try prompts from
`docs/FUNCTIONALITY_GUIDE.md` Part 1 for your team — pipeline
summary, account 360, at-risk accounts, whichever's relevant. This is
where you find out whether your org's picklist values and custom
fields match what the tools assume (README's Part 2 checklist tells
you what to look for and fix).

**Stop here if you only need this for yourself.** The local variant is
the finished product for a single user. Steps 6–9 are only for a
shared, always-on deployment.

## 6. Fix the schema gaps you just found

Before scaling this to a team: update the picklist values and, if your
org tracks risk on a custom field, add the small tool for it
(`docs/FUNCTIONALITY_GUIDE.md` Part 2, `CONTRIBUTING.md` for the
pattern). Do this now, once, rather than after several people are
already relying on a subtly wrong number.

## 7. Decide your connector auth path before you build the container

This determines what you set in step 8, so decide it now, not while
deploying: header-auth (beta, fastest) or OAuth (fully supported,
bigger lift) — `docs/FUNCTIONALITY_GUIDE.md` Part 3 has the honest
tradeoff. If you're not sure, test header-auth via Claude Code first
(no beta flag needed there) before committing to it for the team.

## 8. Move from sandbox to production, then deploy remotely

1. Repeat steps 3–4 against your **production** org (a separate
   Connected App, a separate login — sandbox and production
   Salesforce orgs are entirely separate systems).
2. Open the new `.salesforce_token.json` and copy the `refresh_token`
   value.
3. Generate a shared secret: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
4. Build the container: `docker build -t salesforce-mcp-server .`
5. Follow README.md's "Deploying to the cloud" section for your
   chosen provider — set `SF_CLIENT_ID`, `SF_REFRESH_TOKEN`, and
   `MCP_SHARED_SECRET` as platform secrets, then deploy.
6. `curl https://<your-url>/health` before doing anything else.

## 9. Connect it in Claude and test with one person first

Add it as a Custom Connector (README/FUNCTIONALITY_GUIDE have the
exact path) and run through the same prompts from step 5 against the
live deployment, as one real user, before telling your team it's
ready. Only after that: share it out — on Team/Enterprise, an Owner
adds it once centrally and everyone else just connects.
