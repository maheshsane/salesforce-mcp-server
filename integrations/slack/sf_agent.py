#!/usr/bin/env python3
"""
The shared engine behind every Slack-facing piece of this project --
extracted out of slack_handler.py at the moment a second consumer
(the Case Alert workflow) needed the exact same logic, so there's one
proven implementation instead of two copies that could quietly drift
apart.

Spawns server.py as a local MCP subprocess (exactly like Claude
Desktop does) and drives a Claude tool-use loop until there's a final
answer. Nothing here is Slack-specific -- system_prompt is passed in
by the caller, so an interactive command and a proactive background
workflow can each supply their own framing while sharing this same
tested loop.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "claude-sonnet-5"
MAX_TOOL_ITERATIONS = 10  # safety cap so a stuck loop can't run forever

# server.py lives in the project root, two directories up from this
# file (integrations/slack/). Computed from this file's own location
# rather than left as a bare relative "server.py" -- that only worked
# by coincidence, so long as whoever ran this happened to cd into the
# project root first. Worth fixing now, before a cron job or a
# different invocation location hits the same class of bug that
# case_alert_watcher.py's sf_client import just did.
SERVER_PY_PATH = str(Path(__file__).parent.parent.parent / "server.py")

anthropic_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def convert_mcp_tools_to_anthropic(mcp_tools):
    """MCP tool objects -> Anthropic's tool-use format. Pure, testable.
    Tries both input_schema (Python convention) and inputSchema
    (matching the JSON wire format), since the installed SDK version's
    actual naming wasn't confirmed until it broke live the first time
    this was built."""
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
    """TaskGroup/ExceptionGroup failures (common with the MCP client's
    internal async plumbing) wrap the real error inside .exceptions
    rather than raising it directly -- str(e) alone just says something
    like "unhandled errors in a TaskGroup (1 sub-exception)", which is
    useless for debugging. Recurses to find the actual leaf error(s)."""
    if hasattr(e, "exceptions"):
        return " | ".join(unwrap_exception(sub) for sub in e.exceptions)
    return f"{type(e).__name__}: {e}"


async def answer_question(question: str, system_prompt: str, tools: list = None) -> str:
    """Spawns server.py, discovers its tools, and runs the Claude
    tool-use loop until there's a final text answer. system_prompt is
    supplied by the caller -- an interactive command and a proactive
    alert need different framing even though they share this same loop.
    tools, if given, is a list of tool names -- only those tools are
    made available to Claude this call, not the full discovered set.
    None (the default) keeps every existing caller's behavior exactly
    as it was; only a caller that explicitly passes tools is affected."""
    server_params = StdioServerParameters(command="python3", args=[SERVER_PY_PATH])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_list = await session.list_tools()
            anthropic_tools = convert_mcp_tools_to_anthropic(tool_list.tools)
            if tools is not None:
                anthropic_tools = [t for t in anthropic_tools if t["name"] in tools]

            messages = [{"role": "user", "content": question}]

            for _ in range(MAX_TOOL_ITERATIONS):
             
                response = anthropic_client.messages.create(
                    model=MODEL, max_tokens=8192,
                    system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                    tools=anthropic_tools, messages=messages,
                )

                if response.stop_reason == "max_tokens":
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
