"""Integration test: full tool_use → tool_result → finalize_response loop.

Updated for the Phase 9 rebuild: the orchestrator returns TurnResult,
and the fake client drives a proper two-step conversation
(read_document → finalize_response) exactly as the real API would.
"""

from pathlib import Path

from lantern.agent.orchestrator import Orchestrator, TurnResult, TurnStatus
from lantern.security.sandbox import Sandbox
from lantern.session import SessionManager


class FakeClient:
    """Simulates a two-step model response:
    1. read_document (the model reads the note)
    2. finalize_response (the model synthesizes an answer)
    """

    def __init__(self):
        self.messages = self
        self._call_count = 0

    def create(self, **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_read",
                        "name": "read_document",
                        "input": {"path": "notes.txt"},
                    },
                ]
            }
        else:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_finalize",
                        "name": "finalize_response",
                        "input": {
                            "content": "The note was reviewed. It contains: hello",
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

    # Result is a TurnResult, not a bare string
    assert isinstance(result, TurnResult)
    assert result.status is TurnStatus.COMPLETED

    # The finalize_response content is the message
    assert "The note was reviewed" in result.message

    # Session should have turns recorded
    assert len(session.turns) >= 2  # user + assistant at minimum


def test_orchestrator_records_conversation_history(tmp_path: Path) -> None:
    """Session turns should include both user and assistant messages."""
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

    # The user turn should be in the session
    user_turns = [t for t in session.turns if t.role == "user"]
    assistant_turns = [t for t in session.turns if t.role == "assistant"]

    assert len(user_turns) >= 1
    assert user_turns[0].content == "read the note"
    assert len(assistant_turns) >= 1
