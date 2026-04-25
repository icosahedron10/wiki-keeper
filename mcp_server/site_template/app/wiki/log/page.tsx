import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageView } from "../../page-view";
import { getSiteTitle, loadWiki } from "../../../lib/wiki";

export const metadata: Metadata = {
  title: `Wiki Log | ${getSiteTitle()}`
};

export default async function WikiLogPage() {
  const wiki = loadWiki();
  const page = wiki.logPage;
  if (!page) {
    notFound();
  }
  return <PageView page={page} wiki={wiki} />;
}
