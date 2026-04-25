import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { loadWiki, renderMarkdown, sourceRef, sourceUrl } from "./wiki";

test("discovers pages, category routes, summaries, and exact source paths", () => {
  const wikiDir = fixtureWiki({
    "index.md": "# Wiki Index\n\n## Summary\nProject state.\n",
    "modules/Auth Service.md":
      "---\ntitle: Authentication\nsource_paths:\n  - services/auth/handler.ts\nsources:\n  - services/auth/**\n---\n# Authentication\n\n## Summary\nLogin flow.\n\nSee `repo:services/auth/session.ts`.\n"
  });

  const wiki = loadWiki({ wikiDir });
  const page = wiki.byRoute.get("modules/authentication");
  assert.ok(page);
  assert.equal(page.url, "/wiki/modules/authentication/");
  assert.equal(page.summary, "Login flow.");
  assert.deepEqual(page.sourcePaths, ["services/auth/handler.ts", "services/auth/session.ts"]);
});

test("throws on category slug collisions", () => {
  const wikiDir = fixtureWiki({
    "modules/Auth Service.md": "# Auth Service\n",
    "modules/Auth_Service.md": "# Auth Service\n"
  });

  assert.throws(() => loadWiki({ wikiDir }), /Slug collision/);
});

test("renders wiki links and marks missing links as broken", async () => {
  const wikiDir = fixtureWiki({
    "modules/Auth Service.md": "# Auth Service\n\nSee [[concepts/Retry Policy]] and [[Missing Page]].\n",
    "concepts/Retry Policy.md": "# Retry Policy\n"
  });
  const wiki = loadWiki({ wikiDir });
  const page = wiki.byRoute.get("modules/auth-service");
  assert.ok(page);

  const html = await renderMarkdown(page, wiki);
  assert.match(html, /href="\/wiki\/concepts\/retry-policy\/"/);
  assert.match(html, /class="wiki-link broken-link"/);
});

test("uses explicit template source URLs and sanitizes unsafe markdown", async () => {
  const previousTemplate = process.env.WIKI_KEEPER_SOURCE_URL_TEMPLATE;
  const previousSha = process.env.VERCEL_GIT_COMMIT_SHA;
  process.env.WIKI_KEEPER_SOURCE_URL_TEMPLATE = "https://example.test/blob/{ref}/{path}";
  process.env.VERCEL_GIT_COMMIT_SHA = "abc123";
  try {
    const wikiDir = fixtureWiki({
      "modules/Auth Service.md":
        "# Auth Service\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\nSee `@file:services/auth.ts`.\n"
    });
    const wiki = loadWiki({ wikiDir });
    const page = wiki.byRoute.get("modules/auth-service");
    assert.ok(page);

    assert.equal(sourceRef(), "abc123");
    assert.equal(sourceUrl("services/auth.ts"), "https://example.test/blob/abc123/services/auth.ts");
    const html = await renderMarkdown(page, wiki);
    assert.doesNotMatch(html, /<script/);
    assert.doesNotMatch(html, /onerror/);
    assert.ok(html.includes("https://example.test/blob/abc123/services/auth.ts"));
  } finally {
    restoreEnv("WIKI_KEEPER_SOURCE_URL_TEMPLATE", previousTemplate);
    restoreEnv("VERCEL_GIT_COMMIT_SHA", previousSha);
  }
});

function fixtureWiki(files: Record<string, string>) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "wiki-keeper-site-"));
  const wikiDir = path.join(root, ".wiki-keeper", "wiki");
  for (const category of ["decisions", "modules", "concepts"]) {
    fs.mkdirSync(path.join(wikiDir, category), { recursive: true });
  }
  for (const [relativePath, content] of Object.entries(files)) {
    const fullPath = path.join(wikiDir, relativePath);
    fs.mkdirSync(path.dirname(fullPath), { recursive: true });
    fs.writeFileSync(fullPath, content, "utf8");
  }
  return wikiDir;
}

function restoreEnv(key: string, value: string | undefined) {
  if (value === undefined) {
    delete process.env[key];
  } else {
    process.env[key] = value;
  }
}
