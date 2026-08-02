from __future__ import annotations

from pathlib import Path

from lantern.session import (
    PendingInteraction,
    Session,
    SessionManager,
)


class TestSessionManager:
    def test_create_save_and_resume_round_trip(self, tmp_path: Path):
        manager = SessionManager(tmp_path)
        session = manager.create_session()

        session.append_turn("user", "Find the coastal notes")
        session.append_turn("assistant", "I will search the workspace")
        manager.save_session(session)

        resumed = manager.resume_session(session.session_id)

        assert resumed.session_id == session.session_id
        assert resumed.workspace_root == tmp_path.resolve()
        assert resumed.turns[0].role == "user"
        assert resumed.turns[0].content == "Find the coastal notes"
        assert resumed.turns[1].role == "assistant"
        assert resumed.turns[1].content == "I will search the workspace"

    def test_pending_interaction_is_frozen_on_save_and_replayed_on_resume(self, tmp_path: Path):
        manager = SessionManager(tmp_path)
        session = manager.create_session()
        pending = PendingInteraction(
            tool_call_id="call_123",
            tool_name="delete_file",
            description="Delete research_notes/draft.txt",
            confirmation_prompt="This would delete the draft file.",
        )
        session.pending_interaction = pending
        manager.save_session(session)

        resumed = manager.resume_session(session.session_id)

        assert resumed.pending_interaction is not None
        assert resumed.pending_interaction.tool_call_id == "call_123"
        assert resumed.pending_interaction.tool_name == "delete_file"
        assert resumed.pending_interaction.description == "Delete research_notes/draft.txt"
        assert resumed.pending_interaction.confirmation_prompt == "This would delete the draft file."

    def test_latest_session_is_found_for_resume(self, tmp_path: Path):
        manager = SessionManager(tmp_path)
        first = manager.create_session()
        second = manager.create_session()

        manager.save_session(first)
        manager.save_session(second)

        latest = manager.find_latest_session()
        assert latest is not None
        assert latest.session_id == second.session_id
