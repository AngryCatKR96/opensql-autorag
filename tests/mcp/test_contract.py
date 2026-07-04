from opensql_autorag_mcp.server import TOOL_NAMES


def test_mcp_tool_names_are_stable():
    assert TOOL_NAMES == {
        "search_documents",
        "get_chunk_context",
        "list_documents",
        "get_sync_status",
    }
