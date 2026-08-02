"""Typer entrypoint for Lantern.

Two real usage modes, matching PROJECT_PLAN Phase 10 / SETUP.md:

* ``--prompt "..."`` — one-shot: run a single turn against the
  orchestrator, print the result, exit.
* ``--interactive`` — a REPL that keeps prompting until the user exits.

Design
------
* Session resolution (``--new`` / ``--resume <id>`` / neither) is one
  function, ``_resolve_session()``, shared by both modes — there is
  exactly one place that decides which session a run starts from.
* Turn dispatch is one function, ``_dispatch_turn()``, shared by both
  modes — it is the single place that decides ``run_turn()`` vs.
  ``resume_turn()`` based on ``session.pending_interaction``. Neither
  mode re-implements that branch, which is what fixes Conversation_
  Audit.txt Issue 7 (AWAIT_INPUT previously had nowhere to go — the
  old loop always called ``run_turn`` and never resumed).
* ``main()`` itself only wires flags to those two helpers plus IO; it
  does not know about ``TurnStatus`` or the Action Gate. All rendering
  decisions live in ``cli/rendering.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

from lantern.agent.client import AnthropicClient
from lantern.agent.orchestrator import Orchestrator, TurnResult, TurnStatus
from lantern.cli.rendering import render_session_header, render_turn_result
from lantern.security.sandbox import Sandbox
from lantern.session import Session, SessionManager

app = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    invoke_without_command=True,
)

console = Console()


@app.callback(invoke_without_command=True)
def main(
    workspace: Path = typer.Option(
        Path.cwd(), "--workspace", "-w", help="Workspace root to launch Lantern against."
    ),
    new: bool = typer.Option(False, "--new", help="Start a fresh session, ignoring any saved one."),
    resume: Optional[str] = typer.Option(
        None, "--resume", help="Resume a specific session by id."
    ),
    prompt: Optional[str] = typer.Option(
        None, "--prompt", help="Run a single turn against the orchestrator and exit."
    ),
    interactive: bool = typer.Option(
        False, "--interactive", help="Run Lantern as an interactive REPL."
    ),
) -> None:
    """Lantern CLI entrypoint."""
    load_dotenv()

    if new and resume:
        typer.echo("--new and --resume cannot be used together.", err=True)
        raise typer.Exit(code=1)

    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    sandbox = Sandbox.create(workspace)
    session_manager = SessionManager(workspace)

    try:
        session = _resolve_session(session_manager, new=new, resume=resume)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    if not prompt and not interactive:
        typer.echo(
            "Nothing to do. Pass --prompt \"...\" for a single turn, or "
            "--interactive for a chat loop. Run --help for all options."
        )
        return

    try:
        orchestrator = _build_orchestrator(sandbox, session_manager)
    except ValueError as exc:
        # Raised by AnthropicClient when ANTHROPIC_API_KEY is unset.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    if prompt:
        _run_one_shot(orchestrator, session, prompt)
        return

    _run_interactive_loop(orchestrator, session)


def _resolve_session(
    session_manager: SessionManager, *, new: bool, resume: Optional[str]
) -> Session:
    """Pick the session a run starts from.

    ``--resume <id>`` loads that exact session (raises ``FileNotFoundError``
    if it doesn't exist — surfaced to the user as an error, not silently
    swallowed into a fresh session). ``--new`` always creates a session.
    With neither flag, Lantern continues the most recently saved session
    for this workspace if one exists, so a plain ``--interactive`` run
    naturally picks up where the last one left off — including a pending
    approval that was never answered.
    """
    if resume:
        return session_manager.resume_session(resume)
    if new:
        return session_manager.create_session()
    return session_manager.find_latest_session() or session_manager.create_session()


def _build_orchestrator(sandbox: Sandbox, session_manager: SessionManager) -> Orchestrator:
    client = AnthropicClient()
    return Orchestrator(sandbox=sandbox, session_manager=session_manager, client=client)


def _dispatch_turn(orchestrator: Orchestrator, session: Session, text: str) -> TurnResult:
    """Route *text* to ``resume_turn()`` or ``run_turn()``.

    The single decision point: if the session is currently paused
    waiting on a human reply, *text* answers that pause. Otherwise it
    starts a brand new turn. Shared by one-shot and interactive mode.
    """
    if session.pending_interaction is not None:
        return orchestrator.resume_turn(session, text)
    return orchestrator.run_turn(session, text)


def _run_one_shot(orchestrator: Orchestrator, session: Session, prompt: str) -> None:
    result = _dispatch_turn(orchestrator, session, prompt)
    console.print(render_turn_result(result))

    if result.status is TurnStatus.AWAITING_APPROVAL:
        console.print(
            f'\n[dim]To continue: lantern --workspace {orchestrator.sandbox.root} '
            f'--resume {session.session_id} --prompt "<your reply>"[/dim]'
        )


def _run_interactive_loop(orchestrator: Orchestrator, session: Session) -> None:
    console.print(render_session_header(session.session_id))
    console.print("[dim]Type 'exit' to quit.[/dim]\n")

    if session.pending_interaction is not None:
        # Resumed a session that was mid-pause — show what it's still
        # waiting on before asking for the next line of input.
        console.print(
            render_turn_result(
                TurnResult(
                    status=TurnStatus.AWAITING_APPROVAL,
                    message=session.pending_interaction.confirmation_prompt,
                    pending_interaction=session.pending_interaction,
                )
            )
        )

    while True:
        try:
            user_text = typer.prompt("You")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        stripped = user_text.strip()
        if not stripped:
            continue
        if stripped.lower() in {"exit", "quit", "bye"}:
            console.print("[dim]Goodbye.[/dim]")
            break

        result = _dispatch_turn(orchestrator, session, user_text)
        console.print(render_turn_result(result))


if __name__ == "__main__":
    app()