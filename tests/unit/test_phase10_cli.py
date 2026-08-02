"""Tests for Phase 10 — CLI entrypoint and rendering.

Updated for the orchestrator rebuild: ``run_turn()`` returns
``TurnResult``, not a bare string. The CLI now requires either
``--prompt`` or ``--interactive`` to do real work; ``--new`` alone
prints a "nothing to do" help message.
"""

from pathlib import Path

from typer.testing import CliRunner

from lantern.agent.orchestrator import TurnResult, TurnStatus
from lantern.cli.main import app
from lantern.cli.rendering import render_confidence_banner, render_option_menu


runner = CliRunner()


def test_cli_prompt_mode_routes_to_orchestrator(monkeypatch, tmp_path: Path) -> None:
    class FakeOrchestrator:
        def __init__(self, sandbox, session_manager, client) -> None:
            self.sandbox = sandbox
            self.session_manager = session_manager
            self.client = client

        def run_turn(self, session, user_message: str) -> TurnResult:
            assert user_message == "read the note"
            return TurnResult(
                status=TurnStatus.COMPLETED,
                message="orchestrator result",
            )

    class FakeClient:
        pass

    monkeypatch.setattr("lantern.cli.main.Orchestrator", FakeOrchestrator)
    monkeypatch.setattr("lantern.cli.main.AnthropicClient", FakeClient)

    result = runner.invoke(
        app,
        ["--workspace", str(tmp_path), "--new", "--prompt", "read the note"],
    )

    assert result.exit_code == 0
    assert "orchestrator result" in result.stdout


def test_cli_accepts_new_flag_without_mode_shows_help(tmp_path: Path) -> None:
    """--new without --prompt or --interactive prints guidance."""
    result = runner.invoke(
        app,
        ["--workspace", str(tmp_path), "--new"],
    )

    assert result.exit_code == 0
    assert "--prompt" in result.stdout or "--interactive" in result.stdout


def test_cli_resume_nonexistent_session_fails(tmp_path: Path) -> None:
    """Resuming a session that doesn't exist should fail gracefully."""
    result = runner.invoke(
        app,
        ["--workspace", str(tmp_path), "--resume", "session-nonexistent", "--prompt", "hello"],
    )
    # Should fail with exit code 1 and an error message
    assert result.exit_code == 1


def test_rendering_helpers_emit_presentable_text() -> None:
    banner = render_confidence_banner("MEDIUM")
    menu = render_option_menu(["Search docs", "Write report"])

    assert "MEDIUM" in banner
    assert "Search docs" in menu
    assert "Write report" in menu
