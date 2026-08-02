#!/usr/bin/env python3
"""Record a REAL Lantern conversation against the live Anthropic API as a
clean, readable Markdown transcript.

Why this exists
----------------
This does not write, imagine, or embellish anything. It wraps the real
``AnthropicClient`` with a thin logging proxy that records the exact
request/response pair for every API call the Orchestrator makes, then
formats that raw traffic into readable Markdown. Every tool call, every
tool result, and every confidence level in the output file is copied
verbatim from what the API actually returned and what the tools actually
did against your real example_workspace. There is no hand-written
narrative anywhere in the output.

Usage
-----
Single-turn scenario:

    python scripts/record_transcript.py \\
        --workspace example_workspace \\
        --out transcripts/01_ordinary_search_question.md \\
        --title "Ordinary Search / Question" \\
        --new \\
        --turn "Which files mention coastal erosion, and what do they say?"

Multi-turn scenario (e.g. a HIGH-confidence action you then approve):

    python scripts/record_transcript.py \\
        --workspace example_workspace \\
        --out transcripts/04_consequential_approval.md \\
        --title "Consequential Action Requiring Approval" \\
        --new \\
        --turn "Delete research_notes/corrupted_note.txt." \\
        --turn "yes"

Stop-and-resume scenario (run this as TWO separate commands, in two
separate terminal sessions if you like — that's the point):

    # First run — do some work, then stop.
    python scripts/record_transcript.py \\
        --workspace example_workspace \\
        --out transcripts/05_session_resume.md \\
        --title "Session Stopped and Resumed" \\
        --new \\
        --turn "Search for files about deep sea ecosystems."
    # ^ prints "Session ID: session-xxxxxxxxxxxx" at the end — copy it.

    # Second run — resume that exact session and continue.
    python scripts/record_transcript.py \\
        --workspace example_workspace \\
        --out transcripts/05_session_resume.md \\
        --title "Session Stopped and Resumed (continued)" \\
        --resume session-xxxxxxxxxxxx \\
        --append \\
        --turn "What did you find? Summarize it into output/deep_sea_summary.md"

Requires a real ANTHROPIC_API_KEY in your .env (repo root). This makes
real, billed API calls — one per tool-use round trip per --turn.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make the package importable when run as `python scripts/record_transcript.py`
# without requiring an editable install first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from lantern.agent.client import AnthropicClient  # noqa: E402
from lantern.agent.orchestrator import Orchestrator, TurnResult  # noqa: E402
from lantern.security.sandbox import Sandbox  # noqa: E402
from lantern.session import Session, SessionManager  # noqa: E402
from lantern.workspace import build_workspace_index  # noqa: E402


# ----------------------------------------------------------------------
# Recording client — wraps the REAL AnthropicClient, changes nothing
# about its behavior, just logs every request/response pair it sees.
# ----------------------------------------------------------------------


class _RecordingMessages:
    def __init__(self, real_messages: Any, log: list[dict[str, Any]]) -> None:
        self._real = real_messages
        self._log = log

    def create(self, **kwargs: Any) -> Any:
        response = self._real.create(**kwargs)
        # The orchestrator reuses and mutates the SAME `messages` list
        # object across iterations of its loop (appending as it goes).
        # Snapshot it now — a shallow copy of the list is enough, since
        # the orchestrator only ever appends new dicts, never mutates an
        # already-appended one in place — or every logged call would
        # end up showing the *final* state of the conversation instead
        # of what was actually sent at that point in time.
        logged_kwargs = dict(kwargs)
        if "messages" in logged_kwargs:
            logged_kwargs["messages"] = list(logged_kwargs["messages"])
        self._log.append({"request": logged_kwargs, "response": response})
        return response


class RecordingClient:
    """Drop-in replacement for AnthropicClient that logs real traffic.

    The Orchestrator only ever calls ``client.messages.create(...)`` —
    this proxies that exact call to the real client and keeps a copy of
    every request/response pair, in order, for later rendering.
    """

    def __init__(self, real_client: AnthropicClient) -> None:
        self.log: list[dict[str, Any]] = []
        self.messages = _RecordingMessages(real_client.messages, self.log)


# ----------------------------------------------------------------------
# Dispatch — mirrors cli/main.py's _dispatch_turn exactly: a paused
# session's next --turn answers the pause, otherwise it starts fresh.
# ----------------------------------------------------------------------


def _dispatch(orchestrator: Orchestrator, session: Session, text: str) -> TurnResult:
    if session.pending_interaction is not None:
        return orchestrator.resume_turn(session, text)
    return orchestrator.run_turn(session, text)


# ----------------------------------------------------------------------
# Rendering — turns raw logged API traffic into readable Markdown.
# Content blocks may be real Anthropic SDK objects (TextBlock,
# ToolUseBlock) or plain dicts, depending on where in the loop they
# came from — handle both, same as orchestrator.py's own helpers do.
# ----------------------------------------------------------------------


def _block_type(item: Any) -> str | None:
    if isinstance(item, dict):
        return item.get("type")
    return getattr(item, "type", None)


def _block_field(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def _normalize_content(value: Any) -> list[Any]:
    if isinstance(value, dict):
        content = value.get("content", [])
    else:
        content = getattr(value, "content", value)
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, str):
        return []
    return list(content) if content else []


def _render_input(input_: Any) -> str:
    try:
        return json.dumps(dict(input_), indent=2, default=str)
    except (TypeError, ValueError):
        return str(input_)


def _render_api_call(call: dict[str, Any], index: int) -> list[str]:
    lines = [f"### API round trip {index}", ""]

    request_messages = call["request"].get("messages", [])
    if request_messages:
        last = request_messages[-1]
        last_content = _normalize_content(last) if not isinstance(last.get("content"), str) else None
        if last.get("role") == "user" and last_content:
            tool_results = [b for b in last_content if _block_type(b) == "tool_result"]
            if tool_results:
                lines.append("**Tool result(s) fed back to the model:**")
                lines.append("")
                for block in tool_results:
                    content_text = _block_field(block, "content", "")
                    lines.append("```")
                    lines.append(str(content_text))
                    lines.append("```")
                lines.append("")

    response_content = _normalize_content(call["response"])
    for block in response_content:
        block_type = _block_type(block)
        if block_type == "text":
            text = _block_field(block, "text", "")
            if text.strip():
                lines.append(f"**Model text:** {text}")
                lines.append("")
        elif block_type == "tool_use":
            name = _block_field(block, "name", "?")
            input_ = _block_field(block, "input", {})
            lines.append(f"**Tool call:** `{name}`")
            lines.append("```json")
            lines.append(_render_input(input_))
            lines.append("```")
            lines.append("")

    return lines


def _render_workspace_state(workspace: Path) -> list[str]:
    sandbox = Sandbox.create(workspace)
    index = build_workspace_index(sandbox)
    lines: list[str] = []
    for label, files in (
        ("Source", index.sources),
        ("Policy", index.policies),
        ("Output", index.outputs),
    ):
        if not files:
            continue
        lines.append(f"**{label} files:**")
        for f in sorted(files, key=lambda x: x.relative_path.as_posix()):
            lines.append(f"- `{f.relative_path}` ({f.size_bytes} bytes)")
        lines.append("")
    return lines


def _render_transcript(
    title: str,
    workspace: Path,
    session: Session,
    exchanges: list[dict[str, Any]],
    resumed: bool,
    starting_state_lines: list[str] | None,
) -> str:
    lines: list[str] = []

    if resumed:
        lines.append(f"## {title}")
    else:
        lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**Workspace:** `{workspace}`")
    lines.append(f"**Session ID:** `{session.session_id}`")
    lines.append("")

    if not resumed and starting_state_lines is not None:
        lines.append("## Starting Workspace State")
        lines.append("")
        lines.extend(starting_state_lines)

    for turn_num, exchange in enumerate(exchanges, start=1):
        lines.append(f"## Turn {turn_num}")
        lines.append("")
        lines.append(f"**User:** {exchange['user']}")
        lines.append("")

        for call_num, call in enumerate(exchange["api_calls"], start=1):
            lines.extend(_render_api_call(call, call_num))

        result: TurnResult = exchange["result"]
        lines.append(f"**Status:** `{result.status.value}`")
        lines.append("")
        lines.append("**Lantern's response:**")
        lines.append("")
        lines.append(result.message)
        lines.append("")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--turn",
        action="append",
        required=True,
        help="One user message. Repeat --turn (in order) for a multi-turn scenario.",
    )
    parser.add_argument("--new", action="store_true", help="Start a fresh session.")
    parser.add_argument("--resume", default=None, help="Resume a specific session id.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to --out instead of overwriting (use for the second half of a resume scenario).",
    )
    args = parser.parse_args()

    load_dotenv()

    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace not found: {workspace}")

    sandbox = Sandbox.create(workspace)
    session_manager = SessionManager(workspace)

    if args.resume:
        session = session_manager.resume_session(args.resume)
    elif args.new:
        session = session_manager.create_session()
    else:
        session = session_manager.find_latest_session() or session_manager.create_session()

    try:
        real_client = AnthropicClient()
    except ValueError as exc:
        raise SystemExit(f"{exc}\nSet ANTHROPIC_API_KEY in a .env file at the repo root.")
    recording_client = RecordingClient(real_client)
    orchestrator = Orchestrator(sandbox=sandbox, session_manager=session_manager, client=recording_client)

    # Snapshot BEFORE any turn runs — a scenario that deletes or writes
    # files would otherwise render a misleading "starting state" that's
    # actually the ending state, since rendering happens after the loop.
    starting_state_lines = None if args.resume else _render_workspace_state(workspace)

    exchanges: list[dict[str, Any]] = []
    for turn_text in args.turn:
        before = len(recording_client.log)
        result = _dispatch(orchestrator, session, turn_text)
        exchanges.append(
            {
                "user": turn_text,
                "result": result,
                "api_calls": recording_client.log[before:],
            }
        )
        print(f"  [{result.status.value}] {result.message[:100]}")

    markdown = _render_transcript(
        args.title, workspace, session, exchanges, resumed=bool(args.resume),
        starting_state_lines=starting_state_lines,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.append and args.out.exists():
        with args.out.open("a", encoding="utf-8") as handle:
            handle.write("\n\n---\n\n")
            handle.write(markdown)
    else:
        args.out.write_text(markdown, encoding="utf-8")

    print(f"\nWrote {args.out}")
    print(f"Session ID: {session.session_id}")


if __name__ == "__main__":
    main()
