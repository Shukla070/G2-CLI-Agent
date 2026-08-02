"""Minimal orchestrator for Lantern.

This lightweight orchestrator provides the Phase 9 contract needed by the
unit tests: it can accept a user message, execute a tool through the
already-built safety stack, and return the resulting tool output back to
its caller.
"""

from __future__ import annotations

from typing import Any

from lantern.agent.prompt import render_prompt
from lantern.policy import load_policy_block
from lantern.security.action_gate import ActionGate, GateRequest, GateOutcome
from lantern.security.sandbox import Sandbox
from lantern.session import Session, SessionManager
from lantern.tools.extraction import extract_text
from lantern.tools.read import list_directory, read_document, search_documents
from lantern.tools.schemas import TOOL_HANDLERS, TOOL_SCHEMAS
from lantern.workspace import build_workspace_index


class Orchestrator:
    """Very small orchestrator scaffold that routes one tool-use turn."""

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        session_manager: SessionManager,
        client: Any,
    ) -> None:
        self.sandbox = sandbox
        self.session_manager = session_manager
        self.client = client
        self.action_gate = ActionGate(sandbox)
        self.workspace_index = build_workspace_index(sandbox)

    def run_turn(self, session: Session, user_message: str) -> str:
        """Execute one simple user turn and return the tool result text."""
        session.append_turn("user", user_message)
        self.session_manager.save_session(session)

        policies_text = load_policy_block(self.workspace_index, extract_text)
        system_prompt = render_prompt(
            self.sandbox.root,
            policies_text,
            "2026-08-02",
        )

        message = self.client.messages.create(
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=TOOL_SCHEMAS,
            tool_choice={"type": "auto"},
        )

        content = message.get("content", []) if isinstance(message, dict) else getattr(message, "content", [])
        if not content:
            return ""

        if isinstance(content, dict):
            content = [content]

        outputs: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                tool_name = item.get("name")
                tool_input = item.get("input", {})
            else:
                item_type = getattr(item, "type", None)
                tool_name = getattr(item, "name", None)
                tool_input = getattr(item, "input", {}) or {}

            if item_type != "tool_use":
                continue

            model_confidence = str(tool_input.get("confidence", "NONE"))

            gate_request = GateRequest(
                tool_name=tool_name,
                model_confidence=model_confidence,
                paths={
                    key: value
                    for key, value in tool_input.items()
                    if key in {"path", "source", "destination"}
                },
                exposes_restricted_content=bool(
                    tool_input.get("exposes_restricted_content", False)
                ),
            )
            gate_result = self.action_gate.evaluate(gate_request)
            if gate_result.outcome is GateOutcome.REFUSE:
                return gate_result.message

            handler = TOOL_HANDLERS.get(tool_name)
            if handler is None:
                outputs.append(f"Unknown tool during orchestration: {tool_name}")
                continue

            if tool_name == "read_document":
                outputs.append(
                    read_document(
                        sandbox=self.sandbox,
                        index=self.workspace_index,
                        path=tool_input.get("path", ""),
                    )
                )
                continue

            if tool_name == "list_directory":
                outputs.append(
                    list_directory(
                        sandbox=self.sandbox,
                        index=self.workspace_index,
                        path=tool_input.get("path", ""),
                    )
                )
                continue

            if tool_name == "search_documents":
                outputs.append(
                    search_documents(
                        sandbox=self.sandbox,
                        index=self.workspace_index,
                        query=tool_input.get("query", ""),
                    )
                )
                continue

            handler_kwargs = {**tool_input, **gate_result.resolved_paths}
            outputs.append(str(handler(**handler_kwargs)))

        return "\n\n".join(outputs)
