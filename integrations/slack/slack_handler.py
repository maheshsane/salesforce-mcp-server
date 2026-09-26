#!/usr/bin/env python3
"""
The /ask-sf Slack handler -- rewritten on the raw slack_sdk
SocketModeClient rather than slack_bolt's AsyncApp, after confirming
via a standalone diagnostic (diagnose_slack_payload.py) that the raw
connection delivers a perfectly correct payload (req.type ==
'slash_commands', every field present and right), while slack_bolt's
own internal request-translation layer was silently turning that into
'type': None before it ever reached our handler -- across two
different Bolt adapter implementations, ruling out an adapter-choice
issue. Rather than keep debugging a black box, this builds directly
on the layer already proven to work.

None of the actual answering logic changed from the first version:
same MCP tool-use loop, same error unwrapping. Only the Slack
connection/dispatch layer is different.

Slack still expects an ack within 3 seconds; the real Claude+MCP round
trip won't finish that fast, so every request is acked immediately,
then the real work happens in a background thread (SocketModeClient's
callback runs synchronously, not inside our asyncio loop, so a fresh
asyncio.run() per request in its own thread is the simplest correct
bridge into the async MCP/Claude code).

HONEST NOTE, same as before: the MCP tool-use loop, the schema
conversion, and the error unwrapping are unit-tested with realistic
fake data. The live Slack connection is now proven (via the
diagnostic). What's NOT yet proven live: the actual chat_postMessage
call succeeding, and the full real round trip through server.py and
the Anthropic API. Expect this run to be closer to working, not
guaranteed perfect on the first try.
"""

import asyncio
import os
import threading
import time

from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

MODEL = "claude-sonnet-5"
MAX_TOOL_ITERATIONS = 10  # safety cap so a stuck loop can't run forever

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

anthropic_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
web_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
socket_client = SocketModeClient(app_token=os.environ["SLACK_APP_TOKEN"], web_client=web_client)


def convert_mcp_tools_to_anthropic(mcp_tools):
    """MCP tool objects -> Anthropic's tool-use format. Pure, testable.
    Tries both input_schema (Python convention) and inputSchema
    (matching the JSON wire format) since it wasn't confirmed which
    one this installed SDK version actually uses until it broke live."""
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None),
        }
        for t in mcp_tools
    ]


def is_final_answer(response):
    """True once Claude has stopped requesting tool calls."""
    return response.stop_reason != "tool_use"


def extract_text(response):
    """Pulls the plain text out of a final (non-tool-use) response."""
    return "".join(block.text for block in response.content if block.type == "text")


def unwrap_exception(e: BaseException) -> str:
    """TaskGroup/ExceptionGroup failures wrap the real error inside
    .exceptions rather than raising it directly. Recurses to find the
    actual leaf error(s) instead of an unhelpful wrapper summary."""
    if hasattr(e, "exceptions"):
        return " | ".join(unwrap_exception(sub) for sub in e.exceptions)
    return f"{type(e).__name__}: {e}"


async def answer_question(question: str) -> str:
    """Spawns server.py, discovers its tools, and runs the Claude
    tool-use loop until there's a final text answer."""
    server_params = StdioServerParameters(command="python3", args=["server.py"])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_list = await session.list_tools()
            anthropic_tools = convert_mcp_tools_to_anthropic(tool_list.tools)

            messages = [{"role": "user", "content": question}]

            for _ in range(MAX_TOOL_ITERATIONS):
                response = anthropic_client.messages.create(
                    model=MODEL, max_tokens=4096, system=SYSTEM_PROMPT,
                    tools=anthropic_tools, messages=messages,
                )

                if response.stop_reason == "max_tokens":
                    # Hit the token ceiling before finishing -- possibly
                    # mid-tool-call, possibly mid-text. Use whatever
                    # partial text exists rather than silently returning
                    # nothing (which Slack rejects outright with
                    # "no_text" -- this is exactly what happened on the
                    # multi-account risk-ranking prompt that looked like
                    # a hang).
                    partial = extract_text(response)
                    if partial.strip():
                        return partial + "\n\n_(cut off -- try narrowing the question)_"
                    return ("That answer got too long and was cut off before "
                            "it could finish -- try asking about fewer accounts "
                            "at once, or a narrower question.")

                if is_final_answer(response):
                    return extract_text(response)

                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    result = await session.call_tool(block.name, block.input)
                    result_text = "".join(c.text for c in result.content if hasattr(c, "text"))
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id, "content": result_text,
                    })
                messages.append({"role": "user", "content": tool_results})

            return "Sorry, that took too many steps to answer -- try asking something more specific."


def handle_ask_sf_in_background(channel_id: str, question: str, thread_ts: str):
    """Runs on a background thread, well after the 3-second ack window
    -- does the real work, then posts the real answer as a threaded
    reply under the "looking that up" message, so the question and
    its answer stay visually grouped in the channel."""
    try:
        answer = asyncio.run(answer_question(question))
    except Exception as e:
        real_error = unwrap_exception(e)
        print(f"[/ask-sf error] {real_error}")
        answer = f"Something went wrong answering that: {real_error}"

    # Defense in depth: Slack's API rejects chat.postMessage outright if
    # text is empty ('no_text' error) -- this is exactly what happened
    # on the complex risk-ranking prompt that looked like a silent hang
    # from the Slack side. Never let an empty string reach this call,
    # regardless of which code path produced it.
    if not answer or not answer.strip():
        answer = "Sorry, I couldn't generate an answer to that -- try rephrasing or asking something narrower."

    web_client.chat_postMessage(channel=channel_id, text=answer, thread_ts=thread_ts)


def handle(client: SocketModeClient, req: SocketModeRequest):
    # Ack every request immediately regardless of type -- required by
    # Slack, and this is the exact call already proven working in the
    # diagnostic script.
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
    # their own, so anyone reading along would only ever see the
    # answer with no context. Include the question in this first
    # message, and thread the real answer under it.
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
