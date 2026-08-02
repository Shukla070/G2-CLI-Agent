"""Action Gate — the mandatory sandbox → confidence funnel.

Every tool call that touches the filesystem (and every
``finalize_response`` terminal call) passes through this module
before execution.  Ordering is the entire point:

    1. **Sandbox** — ``sandbox.resolve()`` on every supplied path.
       Failure → ``REFUSE`` immediately.  Confidence is never computed.
    2. **Classification** — map the tool + filesystem facts to one or
       more ``ActionType`` values (e.g. overwrite vs new write, policy
       file targeting).  The model never self-reports overwrite mode.
    3. **Confidence** — ``resolve_confidence_from_candidates()`` merges
       the model's declared level with the code-enforced floor.

This ordering makes "a user confirmation cannot override the sandbox"
true by construction: approval is a later stage a sandbox violation
never reaches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Mapping

from lantern.confidence import (
    ActionType,
    ConfidenceLevel,
    ConfidenceResult,
    parse_confidence_level,
    resolve_confidence_from_candidates,
)
from lantern.policy import is_policy_path
from lantern.security.sandbox import Reason, Sandbox, SandboxViolationError


class GateOutcome(str, Enum):
    """What the orchestrator should do after evaluation."""

    EXECUTE = "execute"          # effective == NONE — run the tool now
    AWAIT_INPUT = "await_input"  # LOW / MEDIUM / HIGH — pause for human
    REFUSE = "refuse"            # sandbox block or REFUSE — no approval path


# Tools grouped by how the gate treats them.
_READ_TOOLS = frozenset({"list_directory", "search_documents", "read_document"})
_MUTATING_TOOLS = frozenset({
    "create_directory",
    "write_document",
    "move_or_rename_file",
    "delete_file",
})
_TERMINAL_TOOLS = frozenset({"finalize_response"})


@dataclass(frozen=True, slots=True)
class GateRequest:
    """Everything the Action Gate needs to evaluate one tool call."""

    tool_name: str
    model_confidence: ConfidenceLevel | str = ConfidenceLevel.NONE
    paths: Mapping[str, str] = field(default_factory=dict)
    exposes_restricted_content: bool = False


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of a single Action Gate evaluation."""

    outcome: GateOutcome
    effective: ConfidenceLevel
    confidence: ConfidenceResult | None
    action_types: tuple[ActionType, ...]
    resolved_paths: dict[str, Path]
    sandbox_reason: Reason | None
    message: str

    @property
    def is_refused(self) -> bool:
        return self.outcome is GateOutcome.REFUSE

    @property
    def requires_human_input(self) -> bool:
        return self.outcome is GateOutcome.AWAIT_INPUT


class ActionGate:
    """Evaluate tool calls through sandbox → classification → confidence."""

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    @property
    def sandbox(self) -> Sandbox:
        return self._sandbox

    def evaluate(self, request: GateRequest) -> GateResult:
        """Run the full funnel for *request* and return a ``GateResult``."""
        if request.tool_name not in (
            _READ_TOOLS | _MUTATING_TOOLS | _TERMINAL_TOOLS
        ):
            return GateResult(
                outcome=GateOutcome.REFUSE,
                effective=ConfidenceLevel.REFUSE,
                confidence=None,
                action_types=(),
                resolved_paths={},
                sandbox_reason=None,
                message=f"Unknown tool: {request.tool_name!r}",
            )

        try:
            model_declared = parse_confidence_level(request.model_confidence)
        except ValueError as exc:
            return GateResult(
                outcome=GateOutcome.REFUSE,
                effective=ConfidenceLevel.REFUSE,
                confidence=None,
                action_types=(),
                resolved_paths={},
                sandbox_reason=None,
                message=str(exc),
            )

        # Terminal tools have no filesystem paths to resolve.
        if request.tool_name in _TERMINAL_TOOLS:
            return self._evaluate_terminal(request, model_declared)

        try:
            resolved_paths = _resolve_all_paths(self._sandbox, request.paths)
        except SandboxViolationError as exc:
            return GateResult(
                outcome=GateOutcome.REFUSE,
                effective=ConfidenceLevel.REFUSE,
                confidence=None,
                action_types=(),
                resolved_paths={},
                sandbox_reason=exc.reason,
                message=str(exc),
            )

        action_types = classify_action_types(
            request.tool_name,
            self._sandbox,
            resolved_paths,
        )
        confidence = resolve_confidence_from_candidates(
            model_declared, action_types
        )

        if confidence.effective is ConfidenceLevel.REFUSE:
            return GateResult(
                outcome=GateOutcome.REFUSE,
                effective=ConfidenceLevel.REFUSE,
                confidence=confidence,
                action_types=action_types,
                resolved_paths=resolved_paths,
                sandbox_reason=None,
                message="Action refused.",
            )

        if confidence.effective is ConfidenceLevel.NONE:
            outcome = GateOutcome.EXECUTE
        else:
            outcome = GateOutcome.AWAIT_INPUT

        return GateResult(
            outcome=outcome,
            effective=confidence.effective,
            confidence=confidence,
            action_types=action_types,
            resolved_paths=resolved_paths,
            sandbox_reason=None,
            message="",
        )

    def _evaluate_terminal(
        self,
        request: GateRequest,
        model_declared: ConfidenceLevel,
    ) -> GateResult:
        if request.exposes_restricted_content:
            action_types = (ActionType.FINALIZE_RESTRICTED,)
        else:
            action_types = (ActionType.FINALIZE,)

        confidence = resolve_confidence_from_candidates(
            model_declared, action_types
        )

        if confidence.effective is ConfidenceLevel.REFUSE:
            outcome = GateOutcome.REFUSE
        elif confidence.effective is ConfidenceLevel.NONE:
            outcome = GateOutcome.EXECUTE
        else:
            outcome = GateOutcome.AWAIT_INPUT

        return GateResult(
            outcome=outcome,
            effective=confidence.effective,
            confidence=confidence,
            action_types=action_types,
            resolved_paths={},
            sandbox_reason=None,
            message="",
        )


def _resolve_all_paths(
    sandbox: Sandbox,
    paths: Mapping[str, str],
) -> dict[str, Path]:
    """Resolve every path in *paths*, mapping empty/'.' to workspace root."""
    resolved: dict[str, Path] = {}
    for key, raw in paths.items():
        resolved[key] = resolve_workspace_path(sandbox, raw)
    return resolved


def resolve_workspace_path(sandbox: Sandbox, raw_path: str) -> Path:
    """Resolve a workspace-relative path, treating '' and '.' as root."""
    if not raw_path or raw_path.strip() in ("", "."):
        return sandbox.root
    return sandbox.resolve(raw_path)


def classify_action_types(
    tool_name: str,
    sandbox: Sandbox,
    resolved_paths: Mapping[str, Path],
) -> tuple[ActionType, ...]:
    """Map a tool call + resolved paths to applicable ``ActionType`` values."""
    if tool_name in _READ_TOOLS:
        return (ActionType.READ,)

    action_types: list[ActionType] = []

    if tool_name == "create_directory":
        action_types.append(ActionType.CREATE_DIR)
        path = resolved_paths.get("path")
        if path is not None:
            _maybe_add_policy_target(sandbox, path, action_types)

    elif tool_name == "write_document":
        path = resolved_paths["path"]
        if path.exists():
            action_types.append(ActionType.WRITE_OVERWRITE)
        else:
            action_types.append(ActionType.WRITE_NEW)
        _maybe_add_policy_target(sandbox, path, action_types)

    elif tool_name == "move_or_rename_file":
        source = resolved_paths.get("path") or resolved_paths.get("source")
        destination = resolved_paths["destination"]
        if destination.exists():
            action_types.append(ActionType.MOVE_COLLISION)
        else:
            action_types.append(ActionType.MOVE_RENAME)
        if source is not None:
            _maybe_add_policy_target(sandbox, source, action_types)
        _maybe_add_policy_target(sandbox, destination, action_types)

    elif tool_name == "delete_file":
        path = resolved_paths["path"]
        action_types.append(ActionType.DELETE)
        _maybe_add_policy_target(sandbox, path, action_types)

    else:
        raise ValueError(f"Unsupported mutating tool: {tool_name!r}")

    return tuple(action_types)


def _maybe_add_policy_target(
    sandbox: Sandbox,
    path: Path,
    action_types: list[ActionType],
) -> None:
    relative = _relative_posix_path(sandbox, path)
    if is_policy_path(relative) and ActionType.POLICY_TARGET not in action_types:
        action_types.append(ActionType.POLICY_TARGET)


def _relative_posix_path(sandbox: Sandbox, path: Path) -> PurePosixPath:
    return PurePosixPath(path.relative_to(sandbox.root).as_posix())
