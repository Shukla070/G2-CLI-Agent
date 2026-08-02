from pathlib import Path

from lantern.agent.orchestrator import Orchestrator
from lantern.security.sandbox import Sandbox
from lantern.session import SessionManager


class FakeClient:
    def __init__(self):
        self.messages = self

    def create(self, **kwargs):
        return {
            "content": [
                {
                    "type": "tool_use",
                    "name": "read_document",
                    "input": {"path": "notes.txt"},
                },
                {
                    "type": "tool_use",
                    "name": "finalize_response",
                    "input": {
                        "content": "The note was reviewed.",
                        "confidence": "NONE",
                        "rationale": "A read-only inspection was enough.",
                        "decision_type": "inform",
                        "exposes_restricted_content": False,
                    },
                },
            ]
        }


def test_orchestrator_runs_a_full_tool_use_then_finalize_sequence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")

    sandbox = Sandbox.create(workspace)
    manager = SessionManager(workspace)
    session = manager.create_session()

    orchestrator = Orchestrator(
        sandbox=sandbox,
        session_manager=manager,
        client=FakeClient(),
    )

    result = orchestrator.run_turn(session, "read the note")

    assert "hello" in result
    assert "The note was reviewed." in result
    assert "decision_type=inform" in result
