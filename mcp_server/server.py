from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import tools

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError as exc:  # pragma: no cover - dependency guard
    raise SystemExit("The `mcp` package is required. Install with `pip install -e .`") from exc


Handler = Callable[[dict[str, Any]], Any | Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler

    def to_mcp_tool(self) -> Tool:
        return Tool(name=self.name, description=self.description, inputSchema=self.input_schema)


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        out["required"] = required
    return out


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        "get_page",
        "Read a wiki page by name. Returns content and metadata.",
        _schema({"page_name": {"type": "string", "description": "Page title, optionally `category/Title`."}}, ["page_name"]),
        lambda args: tools.get_page(args["page_name"]),
    ),
    ToolSpec(
        "read_article",
        "Read an article with parsed frontmatter and latest audit metadata.",
        _schema({"page_name": {"type": "string"}}, ["page_name"]),
        lambda args: tools.read_article(args["page_name"]),
    ),
    ToolSpec(
        "read_audits",
        "Read recent audits for an article id.",
        _schema({"article_id": {"type": "string"}, "limit": {"type": "integer", "default": 5, "minimum": 0}}, ["article_id"]),
        lambda args: tools.read_audits(args["article_id"], limit=int(args.get("limit", 5))),
    ),
    ToolSpec(
        "list_pages",
        "List all wiki pages, optionally filtered by category.",
        _schema({"category": {"type": "string", "enum": ["decisions", "modules", "concepts"]}}),
        lambda args: tools.list_pages(args.get("category")),
    ),
    ToolSpec(
        "list_articles",
        "List pages with frontmatter and last-audit metadata.",
        _schema({"category": {"type": "string", "enum": ["decisions", "modules", "concepts"]}}),
        lambda args: tools.list_articles(args.get("category")),
    ),
    ToolSpec(
        "next_review",
        "Return the next roadmap entry after the state cursor.",
        _schema(),
        lambda _args: tools.next_review(),
    ),
    ToolSpec(
        "run_review",
        "Run one nightly review pass.",
        _schema({"article_id": {"type": "string"}}),
        lambda args: tools.run_review_async(args.get("article_id")),
    ),
    ToolSpec(
        "run_nightly",
        "Run the git-delta nightly review workflow.",
        _schema({"since": {"type": "string"}, "until": {"type": "string"}, "dry_run": {"type": "boolean", "default": False}}),
        lambda args: tools.run_nightly_async(
            since=args.get("since"),
            until=args.get("until"),
            dry_run=bool(args.get("dry_run", False)),
        ),
    ),
    ToolSpec(
        "query_wiki",
        "Keyword search across wiki pages.",
        _schema(
            {
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["keyword", "hybrid"], "default": "keyword"},
                "top_k": {"type": "integer", "default": 5, "minimum": 0},
            },
            ["query"],
        ),
        lambda args: tools.query_wiki(args["query"], mode=args.get("mode", "keyword"), top_k=int(args.get("top_k", 5))),
    ),
    ToolSpec(
        "update_knowledge",
        "Create or modify a wiki page atomically. Modes: replace (default), append, create_only.",
        _schema(
            {
                "page_name": {"type": "string"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["replace", "append", "create_only"], "default": "replace"},
            },
            ["page_name", "content"],
        ),
        lambda args: tools.update_knowledge(args["page_name"], args["content"], mode=args.get("mode", "replace")),
    ),
    ToolSpec("rebuild_index", "Regenerate .wiki-keeper/wiki/index.md from the current page tree.", _schema(), lambda _args: tools.rebuild_index()),
    ToolSpec("lint_wiki", "Check wiki health: orphans, broken links, and index drift.", _schema(), lambda _args: tools.lint_wiki()),
    ToolSpec("validate", "Run structural, frontmatter, roadmap, and lint validations.", _schema(), lambda _args: tools.validate()),
]

TOOLS_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


async def dispatch_tool(name: str, arguments: dict[str, Any] | None) -> Any:
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"Unknown tool {name!r}")
    result = spec.handler(arguments or {})
    if inspect.isawaitable(result):
        return await result
    return result


def build_server() -> Server:
    server: Server = Server("wiki-keeper")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return [spec.to_mcp_tool() for spec in TOOL_SPECS]

    @server.call_tool()
    async def _call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = await dispatch_tool(name, arguments)
        except Exception as exc:
            payload = {"error": type(exc).__name__, "message": str(exc)}
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


async def _run() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
