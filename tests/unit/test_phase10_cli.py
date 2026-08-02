from pathlib import Path

from typer.testing import CliRunner

from lantern.cli.main import app
from lantern.cli.rendering import render_confidence_banner, render_option_menu


runner = CliRunner()


def test_cli_prompt_mode_routes_to_orchestrator(monkeypatch, tmp_path: Path) -> None:
    class FakeOrchestrator:
        def __init__(self, sandbox, session_manager, client) -> None:
            self.sandbox = sandbox
            self.session_manager = session_manager
            self.client = client

        def run_turn(self, session, user_message: str) -> str:
            assert user_message == "read the note"
            return "orchestrator result"

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


def test_cli_accepts_new_and_resume_flags(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--workspace", str(tmp_path), "--new"],
    )

    assert result.exit_code == 0
    assert "Lantern" in result.stdout

    resume_result = runner.invoke(
        app,
        ["--workspace", str(tmp_path), "--resume", "session-123"],
    )

    assert resume_result.exit_code == 0
    assert "Lantern" in resume_result.stdout


def test_rendering_helpers_emit_presentable_text() -> None:
    banner = render_confidence_banner("MEDIUM")
    menu = render_option_menu(["Search docs", "Write report"])

    assert "MEDIUM" in banner
    assert "Search docs" in menu
    assert "Write report" in menu
