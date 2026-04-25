import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { getSiteTheme, getSiteTitle, groupPagesForNav, loadWiki } from "../lib/wiki";

export const metadata: Metadata = {
  title: getSiteTitle(),
  description: "Read-only wiki generated from .wiki-keeper/wiki."
};

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  const wiki = loadWiki();
  const groups = groupPagesForNav(wiki.pages);
  const title = getSiteTitle();
  const theme = getSiteTheme();

  return (
    <html lang="en">
      <body className={`theme-${theme}`}>
        <div className="site-layout">
          <nav className="sidebar" aria-label="Wiki navigation">
            <Link className="sidebar-title" href="/">
              {title}
            </Link>
            {groups.map((group) => (
              <div key={group.label}>
                <h2>{group.label}</h2>
                <ul>
                  {group.pages.map((page) => (
                    <li key={page.id}>
                      <Link href={page.url}>{page.title}</Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
          <main className="main-content">{children}</main>
        </div>
      </body>
    </html>
  );
}
