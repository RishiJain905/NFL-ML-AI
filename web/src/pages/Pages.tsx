// Season and system pages: header + a designed empty state until CR03 fills them.

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../components/ui';

function Page({ eyebrow, title, sub, children }: { eyebrow: string; title: string; sub: string; children: ReactNode }) {
  return (
    <>
      <header className="top">
        <div className="titleblock">
          <span className="eyebrow">{eyebrow}</span>
          <h1>{title}</h1>
          <span className="muted">{sub}</span>
        </div>
      </header>
      <section className="view">{children}</section>
    </>
  );
}

export function SeasonPage() {
  return (
    <Page eyebrow="Season" title="Scorecard" sub="How the models are doing, week by week">
      <EmptyState glyph="CR03" title="The season at a glance">
        Season-to-date Brier for the model, Elo and the market; pick accuracy; calibration; player model vs baseline;
        pipeline health. A 2025 backtest example until 2026 has graded weeks. Coming in CR03.
      </EmptyState>
    </Page>
  );
}

export function TeamsPage() {
  return (
    <Page eyebrow="Season" title="Teams & rankings" sub="Elo and EPA ratings, week by week">
      <EmptyState glyph="CR03" title="Power rankings and movers">
        Biggest risers and fallers, the rankings with Elo sparklines and EPA, and a detail row per team. Coming in
        CR03.
      </EmptyState>
    </Page>
  );
}

export function ModelsPage() {
  return (
    <Page eyebrow="Models" title="What runs in production" sub="Versions, backtests and model cards">
      <EmptyState glyph="CR03" title="One card per production model">
        Game model, ratings and Elo, player models, team totals and the digest writer, each with its headline numbers
        and model card. Coming in CR03.
      </EmptyState>
    </Page>
  );
}

export function AlertsPage() {
  return (
    <Page eyebrow="Operations" title="Alerts" sub="Drift signals, failed steps and stale data">
      <EmptyState glyph="CR03" title="The season's alerts">
        Every run's alerts with their investigation notes, and when each drift signal can first fire. Coming in CR03.
      </EmptyState>
    </Page>
  );
}

export function HealthPage() {
  return (
    <Page eyebrow="System" title="Health" sub="Services, keys and the data drive">
      <EmptyState glyph="CR03" title="Services, keys and recent runs">
        Neo4j, Docker, W&amp;B and OpenRouter; keys by name with set / not set (never a value); the last runs. The
        sidebar footer already shows the lock, Neo4j and the data drive. Coming in CR03.
      </EmptyState>
    </Page>
  );
}

export function NotFound() {
  return (
    <Page eyebrow="Control room" title="Not found" sub="There's no page here">
      <EmptyState glyph="404" title="This page doesn't exist" actions={<Link className="btn sm" to="/">Go to this week</Link>} />
    </Page>
  );
}
