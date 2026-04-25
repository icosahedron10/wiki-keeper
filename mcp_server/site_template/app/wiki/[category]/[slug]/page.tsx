import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageView } from "../../../page-view";
import { getSiteTitle, loadWiki } from "../../../../lib/wiki";

export const dynamicParams = false;

export async function generateStaticParams() {
  const wiki = loadWiki();
  return wiki.pages
    .filter((page) => page.category !== "index" && page.category !== "system")
    .map((page) => ({ category: page.category, slug: page.slug }));
}

export async function generateMetadata({
  params
}: Readonly<{ params: Promise<{ category: string; slug: string }> }>): Promise<Metadata> {
  const { category, slug } = await params;
  const wiki = loadWiki();
  const page = wiki.byRoute.get(`${category}/${slug}`);
  if (!page) {
    return { title: getSiteTitle() };
  }
  return {
    title: `${page.title} | ${getSiteTitle()}`,
    description: page.summary || undefined
  };
}

export default async function WikiPage({
  params
}: Readonly<{ params: Promise<{ category: string; slug: string }> }>) {
  const { category, slug } = await params;
  const wiki = loadWiki();
  const page = wiki.byRoute.get(`${category}/${slug}`);
  if (!page) {
    notFound();
  }
  return <PageView page={page} wiki={wiki} />;
}
