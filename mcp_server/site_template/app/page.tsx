import { PageView } from "./page-view";
import { loadWiki } from "../lib/wiki";

export default async function HomePage() {
  const wiki = loadWiki();
  const page = wiki.indexPage;

  if (!page) {
    return (
      <section className="empty-state">
        <h1>Wiki index missing</h1>
        <p>Add <code>.wiki-keeper/wiki/index.md</code> and rebuild the site.</p>
      </section>
    );
  }

  return <PageView page={page} wiki={wiki} />;
}
