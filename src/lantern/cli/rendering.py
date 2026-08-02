"""Terminal rendering helpers for the Lantern CLI.

These are pure string builders — no I/O, no ``print`` calls — so they
stay trivially unit-testable. ``cli/main.py`` is the only place that
actually writes to the terminal (via a shared ``rich.console.Console``
with markup enabled), keeping "business logic" (what to say) separate
from "CLI logic" (how/where it's displayed).

Strings use Rich console-markup tags (``[green]...[/green]``); a plain
``print()`` or a test's substring assertion still works fine against
them since the tags sit alongside the plain text, not instead of it.
"""

from __future__ import annotations

from lantern.agent.orchestrator import TurnResult, TurnStatus


def render_confidence_banner(confidence: str) -> str:
    """Return a simple, presentable confidence label for the CLI."""
    return f"Confidence: {confidence}"


def render_option_menu(options: list[str]) -> str:
    """Render a simple numbered option menu string for the CLI."""
    lines = ["Options:"]
    for idx, option in enumerate(options, start=1):
        lines.append(f"{idx}. {option}")
    return "\n".join(lines)


def render_session_header(session_id: str) -> str:
    """Header shown once at the start of an interactive session."""
    return f"[bold]Lantern[/bold] — session [cyan]{session_id}[/cyan]"


def render_turn_result(result: TurnResult) -> str:
    """Format one ``TurnResult`` for terminal display.

    * ``COMPLETED`` — the model's final answer, in green.
    * ``REFUSED`` — a hard stop (sandbox violation, REFUSE-floor action,
      or an explicit model refusal), in bold red. There is no "try
      again" framing here on purpose: a REFUSE is not an approval flow.
    * ``AWAITING_APPROVAL`` — a yellow banner. ``pending_interaction``
      already carries a fully-formed prompt (confirmation question for
      a mutating tool call, or the model's own ambiguous-answer text
      for a paused ``finalize_response``), so this just decorates it.
      A yes/no hint is appended only for the ``clarification`` kind —
      the ``tool_confirmation`` kind already embeds "Reply 'yes'/'no'"
      in its prompt text, so repeating it here would be redundant.
    """
    if result.status is TurnStatus.COMPLETED:
        return f"[green]Lantern:[/green] {result.message}"

    if result.status is TurnStatus.REFUSED:
        return f"[bold red]Refused:[/bold red] {result.message}"

    pending = result.pending_interaction
    kind = pending.kind if pending is not None else "tool_confirmation"

    body = f"[yellow]⚠ Awaiting input[/yellow]\n{result.message}"
    if kind == "clarification":
        body += "\n[dim]Reply with your choice or more detail.[/dim]"
    return body


__all__ = [
    "render_confidence_banner",
    "render_option_menu",
    "render_session_header",
    "render_turn_result",
]