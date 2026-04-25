from __future__ import annotations

import pytest

from mcp_server import server


def test_every_registered_tool_has_schema_and_handler():
    assert server.TOOL_SPECS
    for spec in server.TOOL_SPECS:
        assert spec.name
        assert spec.description
        assert spec.input_schema["type"] == "object"
        assert callable(spec.handler)
    assert set(server.TOOLS_BY_NAME) == {spec.name for spec in server.TOOL_SPECS}


def test_unknown_tool_raises_clean_error():
    with pytest.raises(ValueError, match="Unknown tool"):
        import asyncio

        asyncio.run(server.dispatch_tool("missing_tool", {}))


def test_registry_entry_generates_mcp_tool():
    tool = server.TOOLS_BY_NAME["get_page"].to_mcp_tool()
    assert tool.name == "get_page"
    assert tool.inputSchema["required"] == ["page_name"]
