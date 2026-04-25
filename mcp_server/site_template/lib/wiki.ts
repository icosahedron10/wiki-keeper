import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeStringify from "rehype-stringify";
import { wikiKeeperWikiDir } from "./generated-config";

export type WikiCategory = "index" | "decisions" | "modules" | "concepts" | "system";

export interface WikiPage {
  id: string;
  category: WikiCategory;
  groupLabel: string;
  slug: string;
  title: string;
  summary: string;
  status: "current" | "draft" | "stale";
  kind: string;
  tags: string[];
  order: number;
  url: string;
  sourcePaths: string[];
  backlinks: WikiPage[];
  filePath: string;
  repoRelativePath: string;
  body: string;
  frontmatter: Record<string, unknown>;
}

export interface WikiRepository {
  wikiDir: string;
  pages: WikiPage[];
  indexPage: WikiPage | null;
  logPage: WikiPage | null;
  byRoute: Map<string, WikiPage>;
  byTitle: Map<string, WikiPage[]>;
}

interface LoadOptions {
  wikiDir?: string;
}

const CATEGORY_LABELS: Record<WikiCategory, string> = {
  index: "Index",
  decisions: "Decisions",
  modules: "Modules",
  concepts: "Concepts",
  system: "System"
};

const NAV_ORDER: WikiCategory[] = ["decisions", "modules", "concepts", "system"];
const CATEGORY_KINDS: Record<WikiCategory, string> = {
  index: "overview",
  decisions: "decision",
  modules: "subsystem",
  concepts: "overview",
  system: "runbook"
};
const WIKI_LINK_RE = /\[\[([^\[\]]+?)\]\]/g;
const SOURCE_TOKEN_RE = /`(?:repo:|@file:)([^`]+)`/g;

export function getSiteTitle() {
  return process.env.WIKI_KEEPER_SITE_TITLE || "Wiki Keeper";
}

export function getSiteTheme() {
  const theme = process.env.WIKI_KEEPER_SITE_THEME;
  return theme === "slate" || theme === "forest" ? theme : "ember";
}

export function loadWiki(options: LoadOptions = {}): WikiRepository {
  const wikiDir = resolveWikiDir(options.wikiDir);
  const pages = discoverPages(wikiDir);
  const byRoute = new Map<string, WikiPage>();
  const byTitle = new Map<string, WikiPage[]>();
  const seenRoutes = new Map<string, string>();

  for (const page of pages) {
    if (page.category !== "index" && page.category !== "system") {
      const routeKey = `${page.category}/${page.slug}`;
      const existing = seenRoutes.get(routeKey);
      if (existing) {
        throw new Error(`Slug collision for ${routeKey}: ${existing} and ${page.repoRelativePath}`);
      }
      seenRoutes.set(routeKey, page.repoRelativePath);
      byRoute.set(routeKey, page);
    }
    const titleKey = normalizeLookup(page.title);
    byTitle.set(titleKey, [...(byTitle.get(titleKey) ?? []), page]);
  }
  attachBacklinks(pages, byTitle, byRoute);

  return {
    wikiDir,
    pages,
    indexPage: pages.find((page) => page.category === "index") ?? null,
    logPage: pages.find((page) => page.category === "system" && page.slug === "log") ?? null,
    byRoute,
    byTitle
  };
}

export function groupPagesForNav(pages: WikiPage[]) {
  return NAV_ORDER.map((category) => ({
    label: CATEGORY_LABELS[category],
    pages: pages
      .filter((page) => page.category === category)
      .sort((a, b) => a.order - b.order || a.title.localeCompare(b.title))
  })).filter((group) => group.pages.length > 0);
}

export async function renderMarkdown(page: WikiPage, wiki: WikiRepository) {
  const processor = unified()
    .use(remarkParse)
    .use(remarkGfm)
    .use(remarkRehype)
    .use(rehypeWikiLinks(wiki))
    .use(rehypeSourceLinks)
    .use(rehypeSanitize, sanitizeSchema)
    .use(rehypeStringify);

  const file = await processor.process(page.body);
  return String(file);
}

export function sourceUrl(sourcePath: string) {
  const cleanPath = normalizeRepoPath(sourcePath);
  if (!cleanPath) {
    return null;
  }

  const ref = sourceRef();
  const template = process.env.WIKI_KEEPER_SOURCE_URL_TEMPLATE;
  if (template) {
    return template.replaceAll("{ref}", encodeURIComponent(ref)).replaceAll("{path}", encodePath(cleanPath));
  }

  const provider = process.env.VERCEL_GIT_PROVIDER;
  const owner = process.env.VERCEL_GIT_REPO_OWNER;
  const repo = process.env.VERCEL_GIT_REPO_SLUG;
  if (provider && provider !== "github") {
    return null;
  }
  if (!owner || !repo) {
    return null;
  }
  return `https://github.com/${owner}/${repo}/blob/${encodeURIComponent(ref)}/${encodePath(cleanPath)}`;
}

export function sourceRef() {
  return (
    process.env.VERCEL_GIT_COMMIT_SHA ||
    process.env.WIKI_KEEPER_SOURCE_REF ||
    process.env.VERCEL_GIT_COMMIT_REF ||
    "main"
  );
}

function resolveWikiDir(override?: string) {
  if (override) {
    return path.resolve(override);
  }
  if (process.env.WIKI_KEEPER_WIKI_DIR) {
    return path.resolve(/*turbopackIgnore: true*/ process.env.WIKI_KEEPER_WIKI_DIR);
  }

  return path.resolve(/*turbopackIgnore: true*/ process.cwd(), wikiKeeperWikiDir);
}

function discoverPages(wikiDir: string) {
  const pages: WikiPage[] = [];
  const indexPath = path.join(wikiDir, "index.md");
  if (fs.existsSync(indexPath)) {
    pages.push(readPage(indexPath, wikiDir, "index"));
  }

  for (const category of ["decisions", "modules", "concepts"] as const) {
    const dir = path.join(wikiDir, category);
    if (!fs.existsSync(dir)) {
      continue;
    }
    for (const entry of fs.readdirSync(dir).sort()) {
      if (entry.endsWith(".md")) {
        pages.push(readPage(path.join(dir, entry), wikiDir, category));
      }
    }
  }

  const logPath = path.join(wikiDir, "log.md");
  if (fs.existsSync(logPath)) {
    pages.push(readPage(logPath, wikiDir, "system"));
  }
  return pages;
}

function readPage(filePath: string, wikiDir: string, category: WikiCategory): WikiPage {
  const raw = fs.readFileSync(filePath, "utf8");
  const parsed = matter(raw);
  const frontmatter = parsed.data as Record<string, unknown>;
  const body = parsed.content.trimEnd() + "\n";
  const title = stringValue(frontmatter.title) || firstHeading(body) || titleFromFile(filePath);
  const slug = category === "index" ? "index" : slugify(title);
  const summary = stringValue(frontmatter.summary) || summaryFromBody(body);
  const status = statusValue(frontmatter.status);
  const kind = stringValue(frontmatter.kind) || CATEGORY_KINDS[category];
  const tags = tagList(frontmatter.tags);
  const order = numberValue(frontmatter.order);
  const repoRelativePath = repoRelativeWikiPath(wikiDir, filePath);
  const sourcePaths = unique([
    ...stringList(frontmatter.source_paths),
    ...extractSourceTokens(body)
  ]);

  return {
    id: `${category}:${slug}`,
    category,
    groupLabel: CATEGORY_LABELS[category],
    slug,
    title,
    summary,
    status,
    kind,
    tags,
    order,
    url: pageUrl(category, slug),
    sourcePaths,
    backlinks: [],
    filePath,
    repoRelativePath,
    body,
    frontmatter
  };
}

function pageUrl(category: WikiCategory, slug: string) {
  if (category === "index") {
    return "/";
  }
  if (category === "system" && slug === "log") {
    return "/wiki/log/";
  }
  return `/wiki/${category}/${slug}/`;
}

function repoRelativeWikiPath(wikiDir: string, filePath: string) {
  const repoRoot = path.resolve(wikiDir, "..", "..");
  return path.relative(repoRoot, filePath).replaceAll("\\", "/");
}

function titleFromFile(filePath: string) {
  return path.basename(filePath, ".md").replace(/[-_]+/g, " ");
}

function firstHeading(body: string) {
  const match = body.match(/^#\s+(.+)$/m);
  return match ? match[1].trim() : "";
}

function summaryFromBody(body: string) {
  const lines = body.split(/\r?\n/);
  const start = lines.findIndex((line) => /^##\s+Summary\s*$/.test(line));
  if (start === -1) {
    return "";
  }
  const collected: string[] = [];
  for (const line of lines.slice(start + 1)) {
    if (/^##\s+/.test(line)) {
      break;
    }
    if (!line.trim()) {
      if (collected.length > 0) {
        break;
      }
      continue;
    }
    collected.push(line.trim());
  }
  return collected.join(" ").replace(/^[-*]\s+/, "");
}

function slugify(value: string) {
  return (
    value
      .normalize("NFKD")
      .toLowerCase()
      .replace(/['"]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "page"
  );
}

function normalizeLookup(value: string) {
  return value.trim().toLowerCase();
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 999;
}

function statusValue(value: unknown): "current" | "draft" | "stale" {
  return value === "draft" || value === "stale" ? value : "current";
}

function stringList(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string").map(normalizeRepoPath).filter(Boolean);
}

function tagList(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean);
}

function attachBacklinks(
  pages: WikiPage[],
  byTitle: Map<string, WikiPage[]>,
  byRoute: Map<string, WikiPage>
) {
  const backlinks = new Map<WikiPage, WikiPage[]>();
  for (const page of pages) {
    for (const label of wikiLinkLabels(page.body)) {
      const linked = resolveWikiLinkFromMaps(label, byTitle, byRoute);
      if (linked && linked !== page) {
        backlinks.set(linked, uniquePages([...(backlinks.get(linked) ?? []), page]));
      }
    }
  }
  for (const page of pages) {
    page.backlinks = backlinks.get(page) ?? [];
  }
}

function wikiLinkLabels(body: string) {
  return [...body.matchAll(WIKI_LINK_RE)].map((match) => (match[1] ?? "").trim()).filter(Boolean);
}

function uniquePages(pages: WikiPage[]) {
  return [...new Map(pages.map((page) => [page.id, page])).values()];
}

function extractSourceTokens(body: string) {
  const out: string[] = [];
  for (const match of body.matchAll(SOURCE_TOKEN_RE)) {
    const sourcePath = normalizeRepoPath(match[1] ?? "");
    if (sourcePath) {
      out.push(sourcePath);
    }
  }
  return out;
}

function normalizeRepoPath(value: string) {
  const cleaned = value.replaceAll("\\", "/").trim();
  if (!cleaned || cleaned.startsWith("/") || cleaned.includes("..")) {
    return "";
  }
  return cleaned;
}

function unique(values: string[]) {
  return [...new Set(values)];
}

function encodePath(value: string) {
  return value.split("/").map(encodeURIComponent).join("/");
}

function resolveWikiLink(label: string, wiki: WikiRepository) {
  return resolveWikiLinkFromMaps(label, wiki.byTitle, wiki.byRoute);
}

function resolveWikiLinkFromMaps(
  label: string,
  byTitle: Map<string, WikiPage[]>,
  byRoute: Map<string, WikiPage>
) {
  const cleaned = label.trim();
  if (!cleaned) {
    return null;
  }

  if (cleaned.includes("/")) {
    const [category, ...rest] = cleaned.split("/");
    const title = rest.join("/");
    if (!isCategory(category) || !title) {
      return null;
    }
    const slug = slugify(title);
    return byRoute.get(`${category}/${slug}`) ?? null;
  }

  const matches = byTitle.get(normalizeLookup(cleaned)) ?? [];
  return matches.length === 1 ? matches[0] : null;
}

function isCategory(value: string): value is "decisions" | "modules" | "concepts" {
  return value === "decisions" || value === "modules" || value === "concepts";
}

function rehypeWikiLinks(wiki: WikiRepository) {
  return () => (tree: HastNode) => {
    visitParents(tree, (node, parent) => {
      if (!parent || node.type !== "text" || typeof node.value !== "string") {
        return;
      }
      if (!node.value.includes("[[") || hasAncestor(node, "a") || hasAncestor(node, "code") || hasAncestor(node, "pre")) {
        return;
      }

      const children = splitWikiText(node.value, wiki);
      if (children.length === 1 && children[0].type === "text") {
        return;
      }
      replaceChild(parent, node, children);
    });
  };
}

function splitWikiText(value: string, wiki: WikiRepository): HastNode[] {
  const out: HastNode[] = [];
  let cursor = 0;
  for (const match of value.matchAll(WIKI_LINK_RE)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      out.push({ type: "text", value: value.slice(cursor, start) });
    }
    const label = match[1].trim();
    const page = resolveWikiLink(label, wiki);
    out.push(
      page
        ? {
            type: "element",
            tagName: "a",
            properties: { className: ["wiki-link"], href: page.url },
            children: [{ type: "text", value: label }]
          }
        : {
            type: "element",
            tagName: "a",
            properties: { className: ["wiki-link", "broken-link"], href: "#" },
            children: [{ type: "text", value: label }]
          }
    );
    cursor = start + match[0].length;
  }
  if (cursor < value.length) {
    out.push({ type: "text", value: value.slice(cursor) });
  }
  return out;
}

function rehypeSourceLinks() {
  return (tree: HastNode) => {
    visitParents(tree, (node, parent) => {
      if (!parent || node.type !== "element" || node.tagName !== "code") {
        return;
      }
      const onlyChild = node.children?.length === 1 ? node.children[0] : null;
      if (!onlyChild || onlyChild.type !== "text" || typeof onlyChild.value !== "string") {
        return;
      }
      const sourcePath = sourcePathFromInlineCode(onlyChild.value);
      if (!sourcePath) {
        return;
      }
      const href = sourceUrl(sourcePath);
      if (!href) {
        node.properties = { ...(node.properties ?? {}), className: ["source-missing"] };
        return;
      }
      replaceChild(parent, node, [
        {
          type: "element",
          tagName: "a",
          properties: { className: ["source-ref"], href },
          children: [node]
        }
      ]);
    });
  };
}

function sourcePathFromInlineCode(value: string) {
  if (value.startsWith("repo:")) {
    return normalizeRepoPath(value.slice("repo:".length));
  }
  if (value.startsWith("@file:")) {
    return normalizeRepoPath(value.slice("@file:".length));
  }
  return "";
}

function visitParents(node: HastNode, visitor: (node: HastNode, parent: HastNode | null) => void, parent: HastNode | null = null) {
  if (parent) {
    node.parent = parent;
  }
  visitor(node, parent);
  if (!node.children) {
    return;
  }
  for (const child of [...node.children]) {
    visitParents(child, visitor, node);
  }
}

function hasAncestor(node: HastNode, tagName: string) {
  let cursor = node.parent;
  while (cursor) {
    if (cursor.type === "element" && cursor.tagName === tagName) {
      return true;
    }
    cursor = cursor.parent;
  }
  return false;
}

function replaceChild(parent: HastNode, oldNode: HastNode, replacements: HastNode[]) {
  if (!parent.children) {
    return;
  }
  const index = parent.children.indexOf(oldNode);
  if (index !== -1) {
    parent.children.splice(index, 1, ...replacements);
    for (const replacement of replacements) {
      replacement.parent = parent;
    }
  }
}

type HastNode = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
  parent?: HastNode;
};

const sanitizeSchema: any = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    a: [
      ...(defaultSchema.attributes?.a ?? []).filter((attribute: any) =>
        (typeof attribute === "string" ? attribute : attribute[0]) !== "className"
      ),
      ["className", "data-footnote-backref", "wiki-link", "broken-link", "source-ref"]
    ],
    code: [...(defaultSchema.attributes?.code ?? []), ["className", "source-missing"]],
    span: [...(defaultSchema.attributes?.span ?? []), ["className", "broken-link"]]
  }
};
