from __future__ import annotations

from lantern.tools import schemas


class TestToolSchemas:
    def test_tool_schema_registry_contains_expected_tools(self):
        names = {entry["name"] for entry in schemas.TOOL_SCHEMAS}
        assert names == {
            "list_directory",
            "search_documents",
            "read_document",
            "create_directory",
            "write_document",
            "move_or_rename_file",
            "delete_file",
            "finalize_response",
        }

    def test_handler_registry_matches_schema_registry(self):
        assert set(schemas.TOOL_HANDLERS) == {
            "list_directory",
            "search_documents",
            "read_document",
            "create_directory",
            "write_document",
            "move_or_rename_file",
            "delete_file",
            "finalize_response",
        }

    def test_schema_payload_is_json_schema_ready(self):
        for entry in schemas.TOOL_SCHEMAS:
            assert "name" in entry
            assert "description" in entry
            assert "input_schema" in entry
            assert "type" in entry["input_schema"]
            assert entry["input_schema"]["type"] == "object"
