"""Conversation orchestrator for Lantern.

This is the Phase 9 rebuild described in ``Conversation_Audit.txt``: a
real ``tool_use -> tool_result -> ... -> finalize_response`` loop, with
every mutating tool call and every ``finalize_response`` call routed
through the Action Gate, and ``finalize_response`` handled as a genuine
terminal instead of an ordinary tool.

Design
------
* ``TurnResult`` / ``TurnStatus`` replace the old bare ``str`` return
  value so the CLI can tell COMPLETED, AWAITING_APPROVAL, and REFUSED
  apart without parsing prose out of the answer text.
* ``run_turn()`` starts a new user turn; ``resume_turn()`` continues one
  that paused for human input. Both funnel into the same private
  ``_run_loop()``. The ``Orchestrator`` holds no conversation state on
  ``self`` between calls — every call rebuilds the message history fresh
  from ``session.turns`` — so resuming behaves identically whether the
  pause and the reply happen a second apart or across a full process
  restart. There is no separate "warm" vs "cold" resume path.
* ``session.turns`` stores plain ``role``/``content`` text, not raw
  Anthropic content blocks (see ``session.py``). The real ``tool_use``/
  ``tool_result`` exchange for one ``run_turn()`` or ``resume_turn()``
  call happens entirely in a local ``working_messages`` list inside
  ``_run_loop()`` and is folded back into a single human-readable turn
  before returning. One consequence: a paused tool call's original
  ``tool_use`` id cannot be replayed on resume (the block it refers to
  was never persisted), so resuming a tool confirmation feeds the human's
  reply and the execution outcome back as one new synthetic ``user``
  turn rather than reconstructing the original tool_result pairing.
* Every tool call — read, mutating, or ``finalize_response`` — goes
  through the Action Gate. For read tools this is defense-in-depth: the
  Gate's floor for ``ActionType.READ`` is always ``NONE``, so the only
  way a read call fails the Gate is a sandbox violation, which is fed
  back as a hard stop (see below), never silently executed.
* A ``GateOutcome.REFUSE`` (sandbox escape, unknown tool, or a REFUSE the
  model itself declared) ends the turn immediately with
  ``TurnStatus.REFUSED``. It is not relayed back to the model for another
  attempt — "no approval path" is enforced as a hard stop, not a retry
  loop, matching ``action_gate.py``'s own docstring.
* A ``GateOutcome.AWAIT_INPUT`` on any tool call — mutating or
  ``finalize_response`` — pauses the whole turn immediately. Later
  ``tool_use`` blocks in the same model response are never executed past
  an unresolved approval.
* ``self._workspace_index`` is rebuilt after every successfully executed
  mutating call, so a chained "create the file, then list the folder to
  confirm" sees the new file instead of a stale snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from lantern.agent.prompt import render_prompt
from lantern.policy import load_policy_block
from lantern.security.action_gate import (
    ActionGate,
    GateOutcome,
    GateRequest,
    GateResult,
    resolve_workspace_path,
)
from lantern.security.sandbox import Sandbox, SandboxViolationError
from lantern.session import PendingInteraction, Session, SessionManager
from lantern.tools.extraction import extract_text
from lantern.tools.read import list_directory, read_document, search_documents
from lantern.tools.schemas import TOOL_HANDLERS, TOOL_SCHEMAS
from lantern.workspace import build_workspace_index

# Safety valve per PROJECT_PLAN.md Phase 9: max tool-use round trips
# for a single run_turn()/resume_turn() call before giving up.
_MAX_ITERATIONS = 20

# Tool-input fields that exist purely for Action Gate / confidence
# reporting. None of the tools/write.py handler functions accept these
# as keyword arguments, so they must be stripped before dispatch.
_GATE_ONLY_FIELDS = frozenset({"confidence", "rationale"})

# Schema field names that carry a raw workspace-relative path string.
# Used both to build the Action Gate's ``paths`` mapping and to know
# which tool_input keys must be swapped out for the Gate's resolved
# ``Path`` objects before a handler is called.
_PATH_FIELDS = frozenset({"path", "source", "destination"})

_READ_TOOLS = frozenset({"list_directory", "search_documents", "read_document"})
_MUTATING_TOOLS = frozenset(
    {"create_directory", "write_document", "move_or_rename_file", "delete_file"}
)

# Deterministic, code-level parse of a human's reply to a paused tool
# confirmation. Never left to the model to interpret "yes" for a
# HIGH-floor action.
_APPROVE_WORDS = frozenset(
    {"yes", "y", "approve", "approved", "confirm", "confirmed", "ok", "okay", "go", "proceed"}
)
_DENY_WORDS = frozenset(
    {"no", "n", "deny", "denied", "cancel", "cancelled", "canceled", "stop", "abort"}
)


class TurnStatus(str, Enum):
    """What the CLI should do after a turn (started or resumed) ends."""

    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Everything the CLI needs to render the outcome of one turn."""

    status: TurnStatus
    message: str
    pending_interaction: PendingInteraction | None = None


class Orchestrator:
    """Runs the tool-forced conversation loop for one workspace."""

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
        self._workspace_index = build_workspace_index(sandbox)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run_turn(self, session: Session, user_message: str) -> TurnResult:
        """Start a new user turn.

        Raises ``ValueError`` if *session* has a pending interaction —
        callers must route through ``resume_turn()`` in that case so a
        paused approval or clarification can't be silently skipped.
        """
        if session.pending_interaction is not None:
            raise ValueError(
                "Session has a pending interaction; call resume_turn() "
                "instead of starting a new run_turn()."
            )

        session.append_turn("user", user_message)
        self.session_manager.save_session(session)

        return self._run_loop(session, self._build_api_messages(session))

    def resume_turn(self, session: Session, human_reply: str) -> TurnResult:
        """Continue a turn that paused for human input."""
        pending = session.pending_interaction
        if pending is None:
            raise ValueError("Session has no pending interaction to resume.")

        if pending.kind == "tool_confirmation":
            return self._resume_tool_confirmation(session, pending, human_reply)
        return self._resume_clarification(session, pending, human_reply)

    # ------------------------------------------------------------------
    # Resume handling
    # ------------------------------------------------------------------

    def _resume_tool_confirmation(
        self,
        session: Session,
        pending: PendingInteraction,
        human_reply: str,
    ) -> TurnResult:
        decision = _parse_confirmation(human_reply)

        if decision is None:
            # Not a recognized yes/no — keep the same pending interaction
            # and ask again rather than guessing.
            return TurnResult(
                status=TurnStatus.AWAITING_APPROVAL,
                message=(
                    "I didn't understand that as a yes or no.\n\n"
                    f"{pending.confirmation_prompt}"
                ),
                pending_interaction=pending,
            )

        if decision:
            result_text = self._execute_pending_tool(pending)
        else:
            result_text = f"Cancelled by user: {pending.description}"

        session.pending_interaction = None
        session.append_turn("user", f"{human_reply}\n\n[Result: {result_text}]")
        self.session_manager.save_session(session)

        return self._run_loop(session, self._build_api_messages(session))

    def _execute_pending_tool(self, pending: PendingInteraction) -> str:
        """Re-validate and execute an approved, previously-paused tool call.

        The path(s) are re-resolved through the sandbox now, rather than
        trusting the frozen decision from when the pause happened — the
        workspace could have changed while the human was thinking about
        it (a file deleted, a symlink swapped in, etc).
        """
        try:
            resolved_paths = {
                key: resolve_workspace_path(self.sandbox, str(value))
                for key, value in pending.tool_input.items()
                if key in _PATH_FIELDS
            }
        except SandboxViolationError as exc:
            return f"Approval could not be completed — the sandbox rejected it: {exc}"

        handler = TOOL_HANDLERS.get(pending.tool_name)
        if handler is None:
            return f"Error: unknown tool '{pending.tool_name}'."

        handler_kwargs = {
            key: value
            for key, value in pending.tool_input.items()
            if key not in _GATE_ONLY_FIELDS and key not in _PATH_FIELDS
        }
        handler_kwargs.update(resolved_paths)

        result_text = str(handler(**handler_kwargs))
        self._workspace_index = build_workspace_index(self.sandbox)
        return result_text

    def _resume_clarification(
        self,
        session: Session,
        pending: PendingInteraction,
        human_reply: str,
    ) -> TurnResult:
        session.pending_interaction = None
        session.append_turn("user", human_reply)
        self.session_manager.save_session(session)

        return self._run_loop(session, self._build_api_messages(session))

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    def _run_loop(
        self,
        session: Session,
        api_messages: list[dict[str, Any]],
    ) -> TurnResult:
        """Drive the tool_use -> tool_result -> ... -> finalize_response loop.

        ``api_messages`` seeds the first API call. It grows in-memory as
        ``working_messages`` for the duration of this call only — nothing
        but the final human-readable outcome is written back to
        ``session.turns``.
        """
        system_prompt = self._render_system_prompt()
        working_messages: list[dict[str, Any]] = list(api_messages)

        for _ in range(_MAX_ITERATIONS):
            response = self.client.messages.create(
                system=system_prompt,
                messages=working_messages,
                tools=TOOL_SCHEMAS,
                tool_choice={"type": "any"},
            )

            content = _normalize_content(response)
            tool_use_items = [item for item in content if _block_type(item) == "tool_use"]

            if not tool_use_items:
                return self._finish(
                    session,
                    TurnStatus.COMPLETED,
                    "Lantern did not return a usable action for this request.",
                )

            working_messages.append({"role": "assistant", "content": content})
            tool_results: list[dict[str, Any]] = []

            for position, item in enumerate(tool_use_items):
                tool_id = _block_field(item, "id") or f"call_{len(working_messages)}_{position}"
                tool_name = str(_block_field(item, "name", ""))
                tool_input = _block_field(item, "input", {}) or {}

                gate_result = self._evaluate(tool_name, tool_input)

                if gate_result.outcome is GateOutcome.REFUSE:
                    return self._finish(
                        session, TurnStatus.REFUSED, f"Refused: {gate_result.message}"
                    )

                if gate_result.outcome is GateOutcome.AWAIT_INPUT:
                    return self._pause_for_input(
                        session, tool_id, tool_name, tool_input, gate_result
                    )

                if tool_name == "finalize_response":
                    content_text = str(tool_input.get("content", ""))
                    return self._finish(session, TurnStatus.COMPLETED, content_text)

                result_text = self._execute_tool(tool_name, tool_input, gate_result)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tool_id, "content": result_text}
                )

            working_messages.append({"role": "user", "content": tool_results})

        return self._finish(
            session,
            TurnStatus.COMPLETED,
            "Lantern reached its maximum number of reasoning steps for this "
            "request without producing a final answer. Please try rephrasing "
            "or breaking the request into smaller steps.",
        )

    def _finish(self, session: Session, status: TurnStatus, message: str) -> TurnResult:
        session.append_turn("assistant", message)
        self.session_manager.save_session(session)
        return TurnResult(status=status, message=message)

    def _pause_for_input(
        self,
        session: Session,
        tool_id: str,
        tool_name: str,
        tool_input: Mapping[str, Any],
        gate_result: GateResult,
    ) -> TurnResult:
        level = gate_result.effective.name

        if tool_name == "finalize_response":
            content_text = str(tool_input.get("content", ""))
            message = f"[{level}] {content_text}"
            pending = PendingInteraction(
                tool_call_id=tool_id,
                tool_name=tool_name,
                description=f"finalize_response requires {level} approval.",
                confirmation_prompt=message,
                kind="clarification",
                tool_input=dict(tool_input),
            )
        else:
            description, message = _build_tool_confirmation_prompt(
                tool_name, tool_input, level
            )
            pending = PendingInteraction(
                tool_call_id=tool_id,
                tool_name=tool_name,
                description=description,
                confirmation_prompt=message,
                kind="tool_confirmation",
                tool_input=dict(tool_input),
            )

        session.append_turn("assistant", message)
        session.set_pending_interaction(pending)
        self.session_manager.save_session(session)

        return TurnResult(
            status=TurnStatus.AWAITING_APPROVAL,
            message=message,
            pending_interaction=pending,
        )

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _evaluate(self, tool_name: str, tool_input: Mapping[str, Any]) -> GateResult:
        paths = {str(k): str(v) for k, v in tool_input.items() if k in _PATH_FIELDS}
        request = GateRequest(
            tool_name=tool_name,
            model_confidence=str(tool_input.get("confidence", "NONE")),
            paths=paths,
            exposes_restricted_content=bool(tool_input.get("exposes_restricted_content", False)),
        )
        return self.action_gate.evaluate(request)

    def _execute_tool(
        self,
        tool_name: str,
        tool_input: Mapping[str, Any],
        gate_result: GateResult,
    ) -> str:
        if tool_name == "read_document":
            return read_document(
                sandbox=self.sandbox,
                index=self._workspace_index,
                path=str(tool_input.get("path", "")),
            )
        if tool_name == "list_directory":
            return list_directory(
                sandbox=self.sandbox,
                index=self._workspace_index,
                path=str(tool_input.get("path", "")),
            )
        if tool_name == "search_documents":
            return search_documents(
                sandbox=self.sandbox,
                index=self._workspace_index,
                query=str(tool_input.get("query", "")),
            )

        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return f"Error: unknown tool '{tool_name}'."

        handler_kwargs = {
            key: value
            for key, value in tool_input.items()
            if key not in _GATE_ONLY_FIELDS and key not in _PATH_FIELDS
        }
        handler_kwargs.update(gate_result.resolved_paths)

        result_text = str(handler(**handler_kwargs))

        # Mutating call succeeded — refresh the index so any later
        # list_directory/search_documents/read_document call, in this
        # same loop or a later turn, doesn't see a stale snapshot.
        self._workspace_index = build_workspace_index(self.sandbox)
        return result_text

    # ------------------------------------------------------------------
    # Prompt / history helpers
    # ------------------------------------------------------------------

    def _render_system_prompt(self) -> str:
        policies_text = load_policy_block(self._workspace_index, extract_text)
        return render_prompt(self.sandbox.root, policies_text)

    def _build_api_messages(self, session: Session) -> list[dict[str, Any]]:
        """Rebuild the plain-text API message list from session history.

        ``session.turns`` are plain role/content text, not raw Anthropic
        content blocks. This is what lets a resume — even after a
        process restart — behave exactly like a same-process resume.
        """
        return [{"role": turn.role, "content": turn.content} for turn in session.turns]


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _parse_confirmation(reply: str) -> bool | None:
    """Deterministic allow-list parse of a yes/no reply.

    Returns True (approve), False (deny), or None if *reply* matches
    neither — deliberately never left to the model to interpret "yes"
    for a HIGH-floor action.
    """
    normalized = reply.strip().lower()
    if normalized in _APPROVE_WORDS:
        return True
    if normalized in _DENY_WORDS:
        return False
    return None


def _build_tool_confirmation_prompt(
    tool_name: str,
    tool_input: Mapping[str, Any],
    level: str,
) -> tuple[str, str]:
    """Return (description, confirmation_prompt) for a paused mutating call."""
    action = _describe_action(tool_name, tool_input)
    rationale = str(tool_input.get("rationale", "")).strip()

    lines = [f"[{level}] {action}"]
    if rationale:
        lines.append(f"Rationale: {rationale}")
    lines.append("Reply 'yes' to approve or 'no' to cancel.")

    return action, "\n".join(lines)


def _describe_action(tool_name: str, tool_input: Mapping[str, Any]) -> str:
    if tool_name == "delete_file":
        return f"Delete '{tool_input.get('path', '?')}'."
    if tool_name == "write_document":
        return f"Write to '{tool_input.get('path', '?')}' (this may overwrite an existing file)."
    if tool_name == "move_or_rename_file":
        return f"Move '{tool_input.get('source', '?')}' to '{tool_input.get('destination', '?')}'."
    if tool_name == "create_directory":
        return f"Create directory '{tool_input.get('path', '?')}'."
    return f"{tool_name}({dict(tool_input)})"


def _normalize_content(message: Any) -> list[Any]:
    """Extract the ``content`` list from either a dict or an SDK object."""
    if isinstance(message, dict):
        content = message.get("content", [])
    else:
        content = getattr(message, "content", [])
    if isinstance(content, dict):
        content = [content]
    return content or []


def _block_type(item: Any) -> str | None:
    if isinstance(item, dict):
        return item.get("type")
    return getattr(item, "type", None)


def _block_field(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


__all__ = ["Orchestrator", "TurnResult", "TurnStatus"]