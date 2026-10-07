// The header wrapper shared by the season and system pages (src/pages/season/, CR03), and the
// not-found page.

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../components/ui';

export function Page({
  eyebrow,
  title,
  sub,
  right,
  children,
}: {
  eyebrow: string;
  title: string;
  sub: ReactNode;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <>
      <header className="top">
        <div className="titleblock">
          <span className="eyebrow">{eyebrow}</span>
          <h1>{title}</h1>
          <span className="muted">{sub}</span>
        </div>
        {right ? <div className="right">{right}</div> : null}
      </header>
      <section className="view">{children}</section>
    </>
  );
}

export function NotFound() {
  return (
    <Page eyebrow="Control room" title="Not found" sub="There's no page here">
      <EmptyState
        glyph="404"
        title="This page doesn't exist"
        actions={
          <Link className="btn sm" to="/">
            Go to this week
          </Link>
        }
      />
    </Page>
  );
}
