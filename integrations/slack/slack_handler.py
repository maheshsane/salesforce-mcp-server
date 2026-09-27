#!/usr/bin/env python3
"""
The /ask-sf Slack handler. Built on the raw slack_sdk SocketModeClient
(see sf_agent.py's history / git log for why -- slack_bolt's own
request-routing had a bug that survived switching adapters).

The actual answering engine (the MCP tool-use loop, schema conversion,
error unwrapping) now lives in sf_agent.py, shared with the Case Alert
workflow -- extracted the moment a second consumer needed the exact
same logic, so there's one proven implementation instead of two
copies that could quietly drift apart.

Slack expects an ack within 3 seconds; the real Claude+MCP round trip
won't finish that fast, so every request is acked immediately, then
the real work happens in a background thread.
"""

import asyncio
import os
import threading
import time

from dotenv import load_dotenv

load_dotenv()

from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from sf_agent import answer_question, unwrap_exception

SYSTEM_PROMPT = (
    "You are answering a question asked live in Slack, using the connected "
    "Salesforce tools to get real, current data -- never guess or make up "
    "figures. Format it in Slack's own markup: *bold* with single asterisks "
    "(not double), \u2022 for bullet points, no markdown headers. If the "
    "question genuinely can't be answered with the available tools, say so "
    "plainly.\n\n"
    "Always give the fuller, detailed picture -- specific account or deal "
    "highlights, notable risks worth flagging, relevant cross-references "
    "(like a related CSM or PreSales owner) -- even when the question "
    "itself is narrowly phrased (e.g. 'by stage' or 'the total'). Don't "
    "narrow the response down to just a bare number or a stage breakdown "
    "with nothing else just because the question was scoped tightly; give "
    "the same depth you'd give if asked to summarize the person or topic "
    "more broadly. Still keep it readable as a chat reply, not a wall of "
    "text -- detailed doesn't mean exhaustive.\n\n"
    "Note on filtering by person: this org has only one real Salesforce "
    "user, so the standard Opportunity Owner field is always the same "
    "person and never useful for filtering. Sales rep, PreSales owner, "
    "and CSM identity are tracked in separate custom text fields instead "
    "(Sales_Rep_Name__c and PreSales_Owner_Name__c on Opportunity, "
    "CSM_Owner_Name__c on Account). If you need to ask the user who they "
    "are to filter results, ask for their name as it appears as Sales Rep, "
    "PreSales Owner, or CSM -- never suggest 'Opportunity Owner', since "
    "that field can't actually distinguish between people here."
)

web_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
socket_client = SocketModeClient(app_token=os.environ["SLACK_APP_TOKEN"], web_client=web_client)


def handle_ask_sf_in_background(channel_id: str, question: str, thread_ts: str):
    """Runs on a background thread, well after the 3-second ack window
    -- does the real work, then posts the real answer as a threaded
    reply under the "looking that up" message."""
    try:
        answer = asyncio.run(answer_question(question, SYSTEM_PROMPT))
    except Exception as e:
        real_error = unwrap_exception(e)
        print(f"[/ask-sf error] {real_error}")
        answer = f"Something went wrong answering that: {real_error}"

    # Defense in depth: Slack's API rejects chat.postMessage outright if
    # text is empty ('no_text' error). Never let an empty string reach
    # this call, regardless of which code path produced it.
    if not answer or not answer.strip():
        answer = "Sorry, I couldn't generate an answer to that -- try rephrasing or asking something narrower."

    web_client.chat_postMessage(channel=channel_id, text=answer, thread_ts=thread_ts)


def handle(client: SocketModeClient, req: SocketModeRequest):
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    if req.type != "slash_commands" or req.payload.get("command") != "/ask-sf":
        return  # not ours, ignore

    channel_id = req.payload["channel_id"]
    question = req.payload.get("text", "").strip()

    if not question:
        web_client.chat_postMessage(
            channel=channel_id,
            text="Ask me something, e.g. `/ask-sf what's my pipeline by stage?`",
        )
        return

    # Slash commands don't echo the typed question into the channel on
    # their own -- include it here, and thread the real answer under it.
    ack_response = web_client.chat_postMessage(
        channel=channel_id, text=f"*{question}*\n\U0001F914 Looking that up..."
    )
    threading.Thread(
        target=handle_ask_sf_in_background,
        args=(channel_id, question, ack_response["ts"]),
        daemon=True,
    ).start()


socket_client.socket_mode_request_listeners.append(handle)


def main():
    print("SF Copilot is connecting to Slack over Socket Mode...")
    socket_client.connect()
    print("Connected. Waiting for /ask-sf ...")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
