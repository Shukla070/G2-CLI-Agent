"""Tests for Phase 9 — Agent client, prompt, and orchestrator.

Updated for the Phase 9 rebuild: the orchestrator now returns
``TurnResult`` (with ``.status`` and ``.message``), not a bare string.
"""

from __future__ import annotations

from pathlib import Path

from lantern.agent.client import AnthropicClient
from lantern.agent.orchestrator import Orchestrator, TurnResult, TurnStatus
from lantern.agent.prompt import render_prompt
from lantern.security.sandbox import Sandbox
from lantern.session import SessionManager


class TestAgentPrompt:
    def test_prompt_builder_contains_workspace_context_and_policy_block(self):
        workspace = Path("/tmp/example_workspace")
        prompt = render_prompt(workspace, "<workspace_policies>\npolicy text\n</workspace_policies>", "2026-08-02")

        assert "Lantern" in prompt
        assert str(workspace) in prompt
        assert "<workspace_policies>" in prompt
        assert "policy text" in prompt
        assert "2026-08-02" in prompt


class TestAnthropicClient:
    def test_client_wrapper_uses_configured_api_key_and_model(self, monkeypatch):
        captured: dict[str, object] = {}

        class FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)
                return {"ok": True}

        class FakeAnthropic:
            def __init__(self, api_key: str):
                self.messages = FakeMessages()

        monkeypatch.setattr("lantern.agent.client.anthropic.Anthropic", FakeAnthropic)

        client = AnthropicClient(api_key="test-key", model="claude-3-5-haiku")
        response = client.messages.create(
            model="claude-3-5-haiku",
            system="system prompt",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            tool_choice="any",
        )

        assert response == {"ok": True}
        assert captured["model"] == "claude-3-5-haiku"
        assert captured["system"] == "system prompt"
        assert captured["tool_choice"] == "any"

    def test_client_wrapper_injects_default_model_and_max_tokens(self, monkeypatch):
        captured: dict[str, object] = {}

        class FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)
                return {"ok": True}

        class FakeAnthropic:
            def __init__(self, api_key: str):
                self.messages = FakeMessages()

        monkeypatch.setattr("lantern.agent.client.anthropic.Anthropic", FakeAnthropic)

        client = AnthropicClient(api_key="test-key")
        response = client.messages.create(
            system="system prompt",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        )

        assert response == {"ok": True}
        assert captured["model"] == AnthropicClient.DEFAULT_MODEL
        assert captured["system"] == "system prompt"
        assert captured["max_tokens"] == 4096


class TestOrchestrator:
    def test_orchestrator_can_run_a_simple_turn(self, tmp_path: Path):
        """The fake client returns read_document first, then finalize_response.
        The orchestrator should execute the read, feed the result back, then
        get the finalize and return a COMPLETED TurnResult with the content."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "notes.txt").write_text("hello", encoding="utf-8")

        sandbox = Sandbox.create(workspace)
        manager = SessionManager(workspace)
        session = manager.create_session()

        call_count = 0

        class FakeClient:
            def __init__(self):
                self.messages = self

            def create(self, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # First call: model reads the document
                    return {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_1",
                                "name": "read_document",
                                "input": {"path": "notes.txt"},
                            }
                        ]
                    }
                else:
                    # Second call: model finalizes with an answer
                    return {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_2",
                                "name": "finalize_response",
                                "input": {
                                    "content": "The note contains: hello",
                                    "confidence": "NONE",
                                    "rationale": "Simple read-only inspection.",
                                    "decision_type": "inform",
                                    "exposes_restricted_content": False,
                                },
                            }
                        ]
                    }

        orchestrator = Orchestrator(sandbox=sandbox, session_manager=manager, client=FakeClient())
        result = orchestrator.run_turn(session, user_message="read the note")

        assert isinstance(result, TurnResult)
        assert result.status is TurnStatus.COMPLETED
        assert "hello" in result.message
        assert call_count == 2  # Two API calls: read → finalize
