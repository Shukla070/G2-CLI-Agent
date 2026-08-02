"""Session persistence for Lantern.

This module provides the minimal, testable session contract needed for
Phase 7:

* ``Session`` stores ordinary turn history plus an optional queued
  ``PendingInteraction``.
* ``SessionManager`` creates, saves, and resumes sessions from a
  workspace-local ``.lantern/sessions`` directory.
* ``save_session()`` writes the JSON payload atomically using a temp file
  + rename, which matches the project's "crash-safe persistence" goal.
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Turn:
    """One conversational turn in the session history."""

    role: str
    content: str


@dataclass(slots=True)
class PendingInteraction:
    """A frozen, human-readable description of a paused action.

    Two kinds of pause exist, distinguished by ``kind``:

    * ``"tool_confirmation"`` — a mutating tool call (write/move/delete/
      create) was classified LOW/MEDIUM/HIGH by the Action Gate. Resuming
      means interpreting the human's reply as approve/deny and, if
      approved, re-executing ``tool_name`` with ``tool_input`` after a
      fresh sandbox re-validation.
    * ``"clarification"`` — a ``finalize_response`` call itself was
      classified LOW/MEDIUM/HIGH (ambiguous request needing a choice, or
      an answer that would expose restricted content). Resuming means
      feeding the human's free-text reply back to the model as the next
      turn in the same task, rather than executing anything.

    ``tool_input`` carries the exact arguments the model supplied for the
    paused tool call, so a resume — even after a process restart — can
    re-run the same decision without re-asking the model to re-derive it.
    Both new fields default so old session JSON without them still loads.
    """

    tool_call_id: str
    tool_name: str
    description: str
    confirmation_prompt: str
    kind: str = "tool_confirmation"
    tool_input: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Session:
    """A per-workspace conversational session.

    The session stores ordinary message history plus one optional pending
    interaction. The pending interaction is frozen when the assistant is
    paused for approval, so resume logic can replay the exact decision
    context even if the workspace has changed since the pause.
    """

    session_id: str
    workspace_root: Path
    created_at: float
    updated_at: float
    turns: list[Turn] = field(default_factory=list)
    pending_interaction: PendingInteraction | None = None

    def append_turn(self, role: str, content: str) -> None:
        self.turns.append(Turn(role=role, content=content))
        self.updated_at = time.time()

    def set_pending_interaction(self, pending: PendingInteraction) -> None:
        self.pending_interaction = pending
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["workspace_root"] = str(self.workspace_root)
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "Session":
        turns = [Turn(**item) for item in payload.get("turns", [])]
        pending = payload.get("pending_interaction")
        pending_obj = None
        if pending:
            pending_obj = PendingInteraction(**pending)

        return cls(
            session_id=str(payload["session_id"]),
            workspace_root=Path(str(payload["workspace_root"])),
            created_at=float(payload["created_at"]),
            updated_at=float(payload["updated_at"]),
            turns=turns,
            pending_interaction=pending_obj,
        )


class SessionManager:
    """Create, save, and restore sessions in workspace-local storage."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.session_dir = self.workspace_root / ".lantern" / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self) -> Session:
        timestamp = time.time()
        session_id = f"session-{uuid.uuid4().hex[:12]}"
        return Session(
            session_id=session_id,
            workspace_root=self.workspace_root,
            created_at=timestamp,
            updated_at=timestamp,
            turns=[],
            pending_interaction=None,
        )

    def save_session(self, session: Session) -> Path:
        session.workspace_root = self.workspace_root
        session.updated_at = time.time()

        path = self._session_path(session.session_id)
        payload = session.to_dict()

        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=parent,
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            temp_name = handle.name

        Path(temp_name).replace(path)
        return path

    def resume_session(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id!r} does not exist.")

        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        return Session.from_dict(payload)

    def find_latest_session(self) -> Session | None:
        session_files = sorted(self.session_dir.glob("*.json"))
        if not session_files:
            return None

        latest_path = max(session_files, key=lambda p: p.stat().st_mtime)
        with latest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        return Session.from_dict(payload)

    def _session_path(self, session_id: str) -> Path:
        return self.session_dir / f"{session_id}.json"