from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import nightly as nightly_mod
from . import server, tools
from .init_corpus import init_corpus


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wiki-keeper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("mcp", help="Run MCP server over stdio.")

    init_p = sub.add_parser("init", help="Initialize .wiki-keeper corpus.")
    init_p.add_argument("--repo", default=".")

    val_p = sub.add_parser("validate", help="Validate corpus and lint wiki.")
    val_p.add_argument("--repo", default=".")

    nightly_p = sub.add_parser("run-nightly", help="Run nightly freshness pass.")
    nightly_p.add_argument("--repo", default=".")
    nightly_p.add_argument("--budget", type=int, default=1)

    tools_p = sub.add_parser("tools", help="Debug/scripting surface for base tools.")
    tools_p.add_argument("--repo", default=".")
    tools_sub = tools_p.add_subparsers(dest="tool_cmd", required=True)

    g = tools_sub.add_parser("get")
    g.add_argument("page_name")

    ls = tools_sub.add_parser("list")
    ls.add_argument("--category")

    q = tools_sub.add_parser("query")
    q.add_argument("query")
    q.add_argument("--top-k", type=int, default=5)

    u = tools_sub.add_parser("update")
    u.add_argument("page_name")
    u.add_argument(
        "--content",
        help="Content string. If omitted, stdin is read.",
    )
    u.add_argument("--mode", choices=["replace", "append", "create_only"], default="replace")

    tools_sub.add_parser("rebuild-index")
    tools_sub.add_parser("lint")

    return parser.parse_args(argv)


def _set_repo_root(repo: str) -> None:
    os.environ["WIKI_KEEPER_ROOT"] = str(Path(repo).resolve())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.cmd == "mcp":
        server.main()
        return 0

    if args.cmd == "init":
        out = init_corpus(Path(args.repo))
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.cmd == "validate":
        _set_repo_root(args.repo)
        out = tools.validate()
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.cmd == "run-nightly":
        _set_repo_root(args.repo)
        out = nightly_mod.run_nightly(
            budget=int(args.budget),
            update_knowledge_fn=tools.update_knowledge,
        )
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.cmd == "tools":
        _set_repo_root(args.repo)
        if args.tool_cmd == "get":
            out = tools.get_page(args.page_name)
        elif args.tool_cmd == "list":
            out = tools.list_pages(args.category)
        elif args.tool_cmd == "query":
            out = tools.query_wiki(args.query, top_k=args.top_k)
        elif args.tool_cmd == "update":
            content = args.content if args.content is not None else sys.stdin.read()
            out = tools.update_knowledge(args.page_name, content, mode=args.mode)
        elif args.tool_cmd == "rebuild-index":
            out = tools.rebuild_index()
        elif args.tool_cmd == "lint":
            out = tools.lint_wiki()
        else:  # pragma: no cover
            raise SystemExit(f"unknown command {args.tool_cmd}")
        print(json.dumps(out, indent=2, default=str))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
