from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

from lantern.agent.client import AnthropicClient
from lantern.agent.orchestrator import Orchestrator
from lantern.cli.rendering import render_confidence_banner, render_option_menu
from lantern.security.sandbox import Sandbox
from lantern.session import Session, SessionManager

app = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def main(
    workspace: Path = typer.Option(Path.cwd(), "--workspace", "-w", help="Workspace root to launch Lantern against."),
    new: bool = typer.Option(False, "--new", help="Start a fresh session."),
    resume: Optional[str] = typer.Option(None, "--resume", help="Resume an existing session by id."),
    prompt: Optional[str] = typer.Option(None, "--prompt", help="Prompt text to send through the orchestrator."),
    interactive: bool = typer.Option(False, "--interactive", help="Run Lantern in a simple English chat loop."),
) -> None:
    """Lantern CLI entrypoint.

    The CLI supports three concrete user-facing modes:
    * a small launch/menu presentation mode for smoke testing and the
      existing Phase 10 contract,
    * a one-shot ``--prompt`` execution path for direct tool-turn runs,
    * an ``--interactive`` English chat loop for a normal session-style
      conversation with the orchestrator.
    """
    load_dotenv()

    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    sandbox = Sandbox.create(workspace)
    session_manager = SessionManager(workspace)

    if prompt:
        session = session_manager.create_session()
        orchestrator = _build_orchestrator(sandbox, session_manager)
        result = orchestrator.run_turn(session, prompt)
        typer.echo(result)
        return

    typer.echo("Lantern")

    if new:
        typer.echo(render_confidence_banner("MEDIUM"))
        typer.echo(render_option_menu(["Search docs", "Write report"]))
    elif resume:
        typer.echo(f"Resuming session {resume}")
        typer.echo(render_confidence_banner("LOW"))
        typer.echo(render_option_menu(["Search docs", "Write report"]))
    else:
        typer.echo(render_confidence_banner("LOW"))
        typer.echo(render_option_menu(["Search docs", "Write report"]))

    if interactive:
        _run_interactive_loop(session_manager, sandbox)


def _build_orchestrator(sandbox: Sandbox, session_manager: SessionManager) -> Orchestrator:
    client = AnthropicClient()
    return Orchestrator(
        sandbox=sandbox,
        session_manager=session_manager,
        client=client,
    )


def _run_interactive_loop(
    session_manager: SessionManager,
    sandbox: Sandbox,
) -> None:
    session = session_manager.create_session()
    orchestrator = _build_orchestrator(sandbox, session_manager)

    while True:
        try:
            user_text = typer.prompt("You")
        except (EOFError, KeyboardInterrupt):
            typer.echo("Goodbye.")
            break

        if user_text.strip().lower() in {"exit", "quit", "bye"}:
            typer.echo("Goodbye.")
            break

        result = orchestrator.run_turn(session, user_text)
        typer.echo(result)


if __name__ == "__main__":
    app()
