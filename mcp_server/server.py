from __future__ import annotations

import asyncio
import json
from typing import Any

from . import tools

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        "The `mcp` package is required. Install with `pip install -e .`"
    ) from exc


_TOOLS: list[Tool] = [
    Tool(
        name="get_page",
        description="Read a wiki page by name. Returns content and metadata.",
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Page title, optionally `category/Title`.",
                }
            },
            "required": ["page_name"],
        },
    ),
    Tool(
        name="list_pages",
        description="List all wiki pages, optionally filtered by category.",
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["decisions", "modules", "concepts"],
                }
            },
        },
    ),
    Tool(
        name="query_wiki",
        description="Keyword search across wiki pages.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["keyword", "hybrid"],
                    "default": "keyword",
                },
                "top_k": {"type": "integer", "default": 5, "minimum": 0},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="update_knowledge",
        description=(
            "Create or modify a wiki page atomically. "
            "Modes: replace (default), append, create_only."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {"type": "string"},
                "content": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["replace", "append", "create_only"],
                    "default": "replace",
                },
            },
            "required": ["page_name", "content"],
        },
    ),
    Tool(
        name="rebuild_index",
        description="Regenerate .wiki-keeper/wiki/index.md from the current page tree.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="lint_wiki",
        description="Check wiki health: orphans, broken links, and index drift.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="validate",
        description="Run structural, frontmatter, roadmap, and lint validations.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_articles",
        description="List pages with frontmatter and last-audit metadata.",
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["decisions", "modules", "concepts"],
                }
            },
        },
    ),
    Tool(
        name="next_review",
        description="Return the next roadmap entry after the state cursor.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="run_review",
        description=(
            "Run one nightly review pass for a single article "
            "(explicit article_id or next roadmap item)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "article_id": {"type": "string"},
            },
        },
    ),
    Tool(
        name="read_article",
        description="Read an article with parsed frontmatter and latest audit metadata.",
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {"type": "string"},
            },
            "required": ["page_name"],
        },
    ),
    Tool(
        name="read_audits",
        description="Read recent audits for an article id.",
        inputSchema={
            "type": "object",
            "properties": {
                "article_id": {"type": "string"},
                "limit": {"type": "integer", "default": 5, "minimum": 0},
            },
            "required": ["article_id"],
        },
    ),
]


def _dispatch(name: str, arguments: dict[str, Any]) -> Any:
    arguments = arguments or {}
    if name == "get_page":
        return tools.get_page(arguments["page_name"])
    if name == "read_article":
        return tools.read_article(arguments["page_name"])
    if name == "read_audits":
        return tools.read_audits(arguments["article_id"], limit=int(arguments.get("limit", 5)))
    if name == "list_pages":
        return tools.list_pages(arguments.get("category"))
    if name == "list_articles":
        return tools.list_articles(arguments.get("category"))
    if name == "next_review":
        return tools.next_review()
    if name == "run_review":
        return tools.run_review(arguments.get("article_id"))
    if name == "query_wiki":
        return tools.query_wiki(
            arguments["query"],
            mode=arguments.get("mode", "keyword"),
            top_k=int(arguments.get("top_k", 5)),
        )
    if name == "update_knowledge":
        return tools.update_knowledge(
            arguments["page_name"],
            arguments["content"],
            mode=arguments.get("mode", "replace"),
        )
    if name == "rebuild_index":
        return tools.rebuild_index()
    if name == "lint_wiki":
        return tools.lint_wiki()
    if name == "validate":
        return tools.validate()
    raise ValueError(f"Unknown tool {name!r}")


def build_server() -> Server:
    server: Server = Server("wiki-keeper")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return _TOOLS

    @server.call_tool()
    async def _call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = _dispatch(name, arguments)
        except Exception as exc:
            payload = {"error": type(exc).__name__, "message": str(exc)}
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]
        return [
            TextContent(
                type="text",
                text=json.dumps(result, indent=2, default=str),
            )
        ]

    return server


async def _run() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
