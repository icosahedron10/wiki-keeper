"""Wiki-keeper MCP server."""

from importlib import import_module
from types import ModuleType

__version__ = "0.1.0"

_MODULE_ALIASES = {
    "audits": "mcp_server.wiki.audits",
    "cli": "mcp_server.app.cli",
    "frontmatter": "mcp_server.core.frontmatter",
    "git_delta": "mcp_server.integrations.git_delta",
    "index": "mcp_server.wiki.index",
    "init_bootstrap": "mcp_server.bootstrap.init_bootstrap",
    "init_corpus": "mcp_server.bootstrap.init_corpus",
    "lint": "mcp_server.wiki.lint",
    "llm": "mcp_server.integrations.llm",
    "monorepo_inventory": "mcp_server.bootstrap.monorepo_inventory",
    "nightly": "mcp_server.wiki.nightly",
    "pages": "mcp_server.core.pages",
    "paths": "mcp_server.core.paths",
    "roadmap": "mcp_server.wiki.roadmap",
    "search": "mcp_server.wiki.search",
    "server": "mcp_server.app.server",
    "site_scaffold": "mcp_server.integrations.site_scaffold",
    "source_scan": "mcp_server.bootstrap.source_scan",
    "state": "mcp_server.wiki.state",
    "storage": "mcp_server.core.storage",
    "tools": "mcp_server.app.tools",
    "validate": "mcp_server.wiki.validate",
    "wikilog": "mcp_server.wiki.wikilog",
}


def __getattr__(name: str) -> ModuleType:
    if name in _MODULE_ALIASES:
        module = import_module(_MODULE_ALIASES[name])
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["__version__", *_MODULE_ALIASES]
