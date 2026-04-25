import { renderMarkdown, sourceUrl, type WikiPage, type WikiRepository } from "../lib/wiki";

export async function PageView({
  page,
  wiki
}: Readonly<{ page: WikiPage; wiki: WikiRepository }>) {
  const html = await renderMarkdown(page, wiki);
  const sourcePaths = page.sourcePaths.filter((sourcePath) => sourceUrl(sourcePath));
  const hasFooter = sourcePaths.length > 0 || page.backlinks.length > 0;

  return (
    <article>
      <div className="page-header">
        <h1>{page.title}</h1>
        <div className="page-meta">
          <span className={`badge badge-${page.status}`}>{page.status}</span>
          <span className="tag">{page.kind}</span>
          {page.tags.map((tag) => (
            <span className="tag" key={tag}>
              {tag}
            </span>
          ))}
        </div>
        {page.summary ? <p>{page.summary}</p> : null}
      </div>
      <div className="wiki-body" dangerouslySetInnerHTML={{ __html: html }} />
      {hasFooter ? (
        <footer className="page-footer">
          {sourcePaths.length > 0 ? (
            <section>
              <h3>Source Files</h3>
              <ul>
                {sourcePaths.map((sourcePath) => (
                  <li key={sourcePath}>
                    <a href={sourceUrl(sourcePath) ?? "#"}>{sourcePath}</a>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {page.backlinks.length > 0 ? (
            <section>
              <h3>Backlinks</h3>
              <ul>
                {page.backlinks.map((backlink) => (
                  <li key={backlink.id}>
                    <a href={backlink.url}>{backlink.title}</a>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </footer>
      ) : null}
    </article>
  );
}
