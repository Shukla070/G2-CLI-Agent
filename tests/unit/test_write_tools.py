"""Tests for lantern.tools.write — mutating filesystem tools.

Write functions accept already-resolved paths (post-gate).  Unit tests
call them directly with resolved paths; integration tests exercise the
full Action Gate → write-tool path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lantern.confidence import ConfidenceLevel
from lantern.security.action_gate import ActionGate, GateOutcome, GateRequest
from lantern.security.sandbox import Sandbox
from lantern.tools.write import (
    create_directory,
    delete_file,
    move_or_rename_file,
    write_document,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    notes = tmp_path / "research_notes"
    notes.mkdir()
    (notes / "draft.txt").write_text("original content", encoding="utf-8")

    output = tmp_path / "output"
    output.mkdir()

    policies = tmp_path / "policies"
    policies.mkdir()
    (policies / "POLICY_embargo.md").write_text("embargo rules", encoding="utf-8")

    return tmp_path


@pytest.fixture
def sandbox(workspace: Path) -> Sandbox:
    return Sandbox.create(workspace)


@pytest.fixture
def gate(sandbox: Sandbox) -> ActionGate:
    return ActionGate(sandbox)


def _rel(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace).as_posix()


# ------------------------------------------------------------------
# create_directory
# ------------------------------------------------------------------

class TestCreateDirectory:
    def test_creates_nested_directory(self, sandbox: Sandbox, workspace: Path):
        target = sandbox.resolve("output/reports")
        result = create_directory(target, path_label="output/reports")
        assert "Created directory" in result
        assert target.is_dir()

    def test_already_exists_is_idempotent_message(self, sandbox: Sandbox):
        target = sandbox.resolve("output")
        result = create_directory(target, path_label="output")
        assert "already exists" in result

    def test_file_at_path_is_error(self, sandbox: Sandbox):
        target = sandbox.resolve("research_notes/draft.txt")
        result = create_directory(target)
        assert "Error" in result
        assert "not a directory" in result


# ------------------------------------------------------------------
# write_document
# ------------------------------------------------------------------

class TestWriteDocument:
    def test_creates_new_file(self, sandbox: Sandbox):
        target = sandbox.resolve("output/summary.txt")
        result = write_document(
            target,
            "Hello from Lantern.",
            path_label="output/summary.txt",
        )
        assert "Created" in result
        assert target.read_text(encoding="utf-8") == "Hello from Lantern."

    def test_overwrites_existing_file(self, sandbox: Sandbox):
        target = sandbox.resolve("research_notes/draft.txt")
        result = write_document(target, "revised content", path_label="research_notes/draft.txt")
        assert "Overwrote" in result
        assert target.read_text(encoding="utf-8") == "revised content"

    def test_rejects_unsupported_extension(self, sandbox: Sandbox):
        target = sandbox.resolve("output/report.pdf")
        result = write_document(target, "pdf bytes?", path_label="output/report.pdf")
        assert "Error" in result
        assert ".pdf" in result
        assert not target.exists()

    def test_rejects_directory_path(self, sandbox: Sandbox):
        target = sandbox.resolve("output")
        result = write_document(target, "nope")
        assert "Error" in result
        assert "directory" in result


# ------------------------------------------------------------------
# move_or_rename_file
# ------------------------------------------------------------------

class TestMoveOrRenameFile:
    def test_rename_in_same_folder(self, sandbox: Sandbox, workspace: Path):
        source = sandbox.resolve("research_notes/draft.txt")
        dest = sandbox.resolve("research_notes/draft_v2.txt")
        result = move_or_rename_file(
            source,
            dest,
            source_label="research_notes/draft.txt",
            destination_label="research_notes/draft_v2.txt",
        )
        assert "Moved" in result
        assert not source.exists()
        assert dest.read_text(encoding="utf-8") == "original content"

    def test_move_to_output(self, sandbox: Sandbox):
        source = sandbox.resolve("research_notes/draft.txt")
        dest = sandbox.resolve("output/draft.txt")
        result = move_or_rename_file(source, dest)
        assert "Moved" in result
        assert dest.exists()
        assert not source.exists()

    def test_replaces_existing_destination(self, sandbox: Sandbox, workspace: Path):
        source = sandbox.resolve("research_notes/draft.txt")
        dest = workspace / "output" / "existing.txt"
        dest.write_text("old dest", encoding="utf-8")
        dest = dest.resolve()

        result = move_or_rename_file(source, dest)
        assert "Moved" in result
        assert dest.read_text(encoding="utf-8") == "original content"

    def test_missing_source(self, sandbox: Sandbox):
        source = sandbox.resolve("output/missing.txt")
        dest = sandbox.resolve("output/dest.txt")
        result = move_or_rename_file(source, dest)
        assert "Error" in result
        assert "does not exist" in result

    def test_directory_source_rejected(self, sandbox: Sandbox):
        source = sandbox.resolve("output")
        dest = sandbox.resolve("output/renamed")
        result = move_or_rename_file(source, dest)
        assert "Error" in result
        assert "directory" in result


# ------------------------------------------------------------------
# delete_file
# ------------------------------------------------------------------

class TestDeleteFile:
    def test_deletes_file(self, sandbox: Sandbox):
        target = sandbox.resolve("research_notes/draft.txt")
        result = delete_file(target, path_label="research_notes/draft.txt")
        assert "Deleted" in result
        assert not target.exists()

    def test_missing_file(self, sandbox: Sandbox):
        target = sandbox.resolve("output/missing.txt")
        result = delete_file(target)
        assert "Error" in result
        assert "does not exist" in result

    def test_directory_rejected(self, sandbox: Sandbox):
        target = sandbox.resolve("output")
        result = delete_file(target)
        assert "Error" in result
        assert "directory" in result


# ------------------------------------------------------------------
# Gate integration — mutating tools only run after approval
# ------------------------------------------------------------------

class TestGateIntegration:
    """Prove the intended orchestrator flow: gate first, write second."""

    def test_new_write_gated_then_executed(self, gate: ActionGate, sandbox: Sandbox):
        request = GateRequest(
            tool_name="write_document",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": "output/new_note.md"},
        )
        gate_result = gate.evaluate(request)
        assert gate_result.outcome is GateOutcome.EXECUTE

        result = write_document(
            gate_result.resolved_paths["path"],
            "# Notes\n\nFresh content.",
            path_label="output/new_note.md",
        )
        assert "Created" in result
        assert gate_result.resolved_paths["path"].exists()

    def test_delete_blocked_until_high_confidence(self, gate: ActionGate):
        request = GateRequest(
            tool_name="delete_file",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": "research_notes/draft.txt"},
        )
        gate_result = gate.evaluate(request)
        assert gate_result.outcome is GateOutcome.AWAIT_INPUT
        assert gate_result.effective is ConfidenceLevel.HIGH

    def test_delete_after_approved_high(self, gate: ActionGate):
        request = GateRequest(
            tool_name="delete_file",
            model_confidence=ConfidenceLevel.HIGH,
            paths={"path": "research_notes/draft.txt"},
        )
        gate_result = gate.evaluate(request)
        assert gate_result.outcome is GateOutcome.AWAIT_INPUT

        result = delete_file(
            gate_result.resolved_paths["path"],
            path_label="research_notes/draft.txt",
        )
        assert "Deleted" in result
        assert not gate_result.resolved_paths["path"].exists()

    def test_overwrite_elevated_to_high(self, gate: ActionGate):
        request = GateRequest(
            tool_name="write_document",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": "research_notes/draft.txt"},
        )
        gate_result = gate.evaluate(request)
        assert gate_result.outcome is GateOutcome.AWAIT_INPUT
        assert gate_result.effective is ConfidenceLevel.HIGH

    def test_move_gated_at_medium(self, gate: ActionGate):
        request = GateRequest(
            tool_name="move_or_rename_file",
            model_confidence=ConfidenceLevel.NONE,
            paths={
                "path": "research_notes/draft.txt",
                "destination": "output/draft.txt",
            },
        )
        gate_result = gate.evaluate(request)
        assert gate_result.outcome is GateOutcome.AWAIT_INPUT
        assert gate_result.effective is ConfidenceLevel.MEDIUM

    def test_sandbox_escape_never_reaches_write(self, gate: ActionGate, sandbox: Sandbox):
        request = GateRequest(
            tool_name="delete_file",
            model_confidence=ConfidenceLevel.HIGH,
            paths={"path": "../outside.txt"},
        )
        gate_result = gate.evaluate(request)
        assert gate_result.outcome is GateOutcome.REFUSE
        assert not gate_result.resolved_paths
