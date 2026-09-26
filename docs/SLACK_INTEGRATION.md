# Interactive Slack access — `/ask-sf`

A slash command that answers real questions, using real data from
whichever Salesforce org you've already connected, right inside
Slack. Separate from the scheduled/automated side of things — this is
purely interactive: someone types a question, gets a real answer back
in the same channel.

Assumes you've already done steps 1–5 of `GETTING_STARTED.md` — this
builds on a working local `server.py`, it doesn't replace it.

## Why this needs Socket Mode, not a public URL

Slack slash commands normally need a public HTTPS endpoint Slack can
reach. That's the wrong fit here — this is meant to run on your own
machine (or eventually a small always-on server), not something with
a stable public address. **Socket Mode** solves this the other way
around: your own script opens an outbound connection *to* Slack, and
Slack delivers the command over that already-open connection. No
tunnel, no public URL, no changing addresses to keep re-registering.

The real tradeoff: something has to keep that connection open the
whole time you want the command to work. Unlike Claude Desktop, which
quietly keeps its own MCP connection alive in the background for as
long as the app is open, this needs its own visible, running process
— see "Running it" below.

## 1. Create the Slack App

1. **api.slack.com/apps** → **Create New App**
2. Choose **"Blank app"** — not "AI agent" or "Starter app". Those
   templates pre-wire event listeners and connector logic this
   doesn't need; blank gives you exactly the two scopes below and
   nothing else to strip out later.
3. Name it, pick your workspace, **Create App**

## 2. Add the two scopes it actually needs

1. Left sidebar → **OAuth & Permissions** → **Bot Token Scopes**
2. Add **`chat:write`** (posting messages) and **`commands`**
   (receiving the slash command)

## 3. Turn on Socket Mode and generate the App-Level Token

1. Left sidebar → **Socket Mode** → toggle **Enable Socket Mode** on
2. Left sidebar → **Basic Information** → **App-Level Tokens** →
   **Generate Token and Scopes**
3. Add the scope **`connections:write`**, generate, copy the token —
   starts with `xapp-`

## 4. Install the app and get the bot token

1. **OAuth & Permissions** → **Install to Workspace** → **Allow**
2. Copy the **Bot User OAuth Token** — starts with `xoxb-`

## 5. Register the slash command

1. Left sidebar → **Slash Commands** → **Create New Command**
2. Command: `/ask-sf` (or whatever you'd rather call it — see
   "Renaming the command" below if you change it)
3. Save. With Socket Mode already on, there's no Request URL field to
   fill in — that's expected, not a missing step.

## 6. Get a dedicated Anthropic API key

This calls the Claude API directly — separate from whatever access
Claude Desktop or claude.ai uses, and billed separately.
**console.anthropic.com → Settings → API Keys → Create Key.** Give it
its own name specific to this integration rather than reusing a key
from something else — if this repo's `.env` were ever exposed, a
dedicated key limits the blast radius to just this one thing.

## 7. Set the credentials

Add to `.env` (never commit this file — `.gitignore` already excludes
it, but double check with `git check-ignore -v .env` if you're ever
unsure):

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ANTHROPIC_API_KEY=sk-ant-...
```

`requirements.txt` already includes the two extra dependencies this
needs (`slack_sdk`, `anthropic`) — `pip install -r requirements.txt`
picks them up alongside everything else.

## 8. Invite the bot into whichever channels should have it

In Slack itself, `/invite @<your bot's name>` in each channel where
you want `/ask-sf` usable. It can't post into a channel it hasn't
been invited to, even with the right token and scopes.

## Running it

```bash
python3 integrations/slack/slack_handler.py
```

Expect:
```
SF Copilot is connecting to Slack over Socket Mode...
Connected. Waiting for /ask-sf ...
```

This has to keep running for the command to work — leave the
terminal open. If it stops (closed terminal, machine sleeps), the
command stops responding until it's started again. There's currently
no cloud-deployed version of this piece; it's built for local use, or
for deploying to a small always-on machine you manage yourself, not
as a drop-in replacement for `server_remote.py`'s deployment path.

## Renaming the command

The command name is set in two places, and both need to match:
Slack's own Slash Commands config (step 5 above), and the check
inside `handle()` in `slack_handler.py`:

```python
if req.type != "slash_commands" or req.payload.get("command") != "/ask-sf":
```

Change the string in both places if you want something other than
`/ask-sf`.

## Worth knowing before you rely on this for a team

- **Every answer reflects one Salesforce identity** — whoever
  completed the OAuth login in step 4 of `GETTING_STARTED.md`. This
  isn't per-person access; everyone using the slash command sees
  exactly what that one connected user can see. Fine for a small team
  sharing one view, not a substitute for real per-user permissions.
- **Response depth adapts to how specific the question is** by
  default — a bare name gets a fuller profile, a narrowly-scoped
  question gets a tighter answer. If you'd rather it behave
  consistently regardless of phrasing, that's a one-line change to
  `SYSTEM_PROMPT` in `slack_handler.py`.
