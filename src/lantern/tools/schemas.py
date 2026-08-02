"""Pydantic-backed tool schema registry for Lantern.

This module keeps one authoritative source of truth for the tool call
schema used by the orchestrator and Anthropic client.  Each request
shape is modeled once with Pydantic and then exported in the exact
Anthropic ``tool`` format:

    {
        "name": ...,
        "description": ...,
        "input_schema": {...}
    }

The registry intentionally stays deliberately small: it is a minimal
wire format for the tool loop, not a plugin system.
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict

from lantern.tools.read import list_directory, read_document, search_documents
from lantern.tools.write import (
    create_directory,
    delete_file,
    move_or_rename_file,
    write_document,
)


class _BaseSchemaModel(BaseModel):
    """Shared schema config for the tool request payloads."""

    model_config = ConfigDict(extra="forbid")


class ListDirectoryRequest(_BaseSchemaModel):
    path: str = ""


class SearchDocumentsRequest(_BaseSchemaModel):
    query: str


class ReadDocumentRequest(_BaseSchemaModel):
    path: str


class CreateDirectoryRequest(_BaseSchemaModel):
    path: str
    path_label: str | None = None
    confidence: str = "NONE"
    rationale: str


class WriteDocumentRequest(_BaseSchemaModel):
    path: str
    content: str
    path_label: str | None = None
    confidence: str = "NONE"
    rationale: str


class MoveOrRenameFileRequest(_BaseSchemaModel):
    source: str
    destination: str
    source_label: str | None = None
    destination_label: str | None = None
    confidence: str = "NONE"
    rationale: str


class DeleteFileRequest(_BaseSchemaModel):
    path: str
    path_label: str | None = None
    confidence: str = "NONE"
    rationale: str


class FinalizeResponseRequest(_BaseSchemaModel):
    content: str
    confidence: str = "NONE"
    rationale: str
    decision_type: str = "inform"
    exposes_restricted_content: bool = False


# Anthropic-ready tool schema list.
TOOL_SCHEMAS: list[dict[str, object]] = [
    {
        "name": "list_directory",
        "description": "List files and subdirectories in a workspace-relative directory.",
        "input_schema": ListDirectoryRequest.model_json_schema(),
    },
    {
        "name": "search_documents",
        "description": "Search the workspace by filename fragment or document text content.",
        "input_schema": SearchDocumentsRequest.model_json_schema(),
    },
    {
        "name": "read_document",
        "description": "Read the text content of a supported workspace document.",
        "input_schema": ReadDocumentRequest.model_json_schema(),
    },
    {
        "name": "create_directory",
        "description": "Create a directory under the sandboxed workspace.",
        "input_schema": CreateDirectoryRequest.model_json_schema(),
    },
    {
        "name": "write_document",
        "description": "Create or overwrite a writable text document within the workspace.",
        "input_schema": WriteDocumentRequest.model_json_schema(),
    },
    {
        "name": "move_or_rename_file",
        "description": "Move or rename a file within the workspace.",
        "input_schema": MoveOrRenameFileRequest.model_json_schema(),
    },
    {
        "name": "delete_file",
        "description": "Delete a file from the workspace.",
        "input_schema": DeleteFileRequest.model_json_schema(),
    },
    {
        "name": "finalize_response",
        "description": "Finalize the user-facing response with a confidence tag and rationale.",
        "input_schema": FinalizeResponseRequest.model_json_schema(),
    },
]


def _finalize_response(
    *,
    content: str,
    confidence: str = "NONE",
    rationale: str = "",
    decision_type: str = "inform",
    exposes_restricted_content: bool = False,
) -> str:
    """Return the finalized user-facing response payload.

    This is a placeholder handler for the terminal tool.  The real
    orchestrator will marshal the result and display it through the CLI.
    """
    return (
        f"decision_type={decision_type}; confidence={confidence}; "
        f"exposes_restricted_content={exposes_restricted_content}; "
        f"content={content}; rationale={rationale}"
    )


TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "list_directory": list_directory,
    "search_documents": search_documents,
    "read_document": read_document,
    "create_directory": create_directory,
    "write_document": write_document,
    "move_or_rename_file": move_or_rename_file,
    "delete_file": delete_file,
    "finalize_response": _finalize_response,
}