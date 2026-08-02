from __future__ import annotations

from pathlib import Path

from lantern.agent.client import AnthropicClient
from lantern.agent.orchestrator import Orchestrator
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

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-5")
        response = client.messages.create(
            system="system prompt",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        )

        assert response == {"ok": True}
        assert captured["model"] == "claude-sonnet-5"
        assert captured["system"] == "system prompt"
        assert captured["max_tokens"] == 256


class TestOrchestrator:
    def test_orchestrator_can_run_a_simple_turn(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "notes.txt").write_text("hello", encoding="utf-8")

        sandbox = Sandbox.create(workspace)
        manager = SessionManager(workspace)
        session = manager.create_session()

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
                        }
                    ]
                }

        orchestrator = Orchestrator(sandbox=sandbox, session_manager=manager, client=FakeClient())
        result = orchestrator.run_turn(session, user_message="read the note")

        assert "notes.txt" in result
