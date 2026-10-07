// Models (mockup: modelsView): one card per production model (game, ratings & Elo, player, team
// totals, the digest writer) with its version, headline backtest numbers, settings and a
// "Model card" button that opens the rendered card. W&B gives the versions and aliases; without
// it the page still shows everything from the local files, under a banner.

import * as Dialog from '@radix-ui/react-dialog';
import { Fragment, useState } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { MODELS_PATH, refreshWandb, useModelCard, useModels } from '../../api/client';
import type { ModelInfo } from '../../api/types';
import { HBars } from '../../charts/HBars';
import { WandbBanner } from '../../components/WandbBanner';
import { Card, CardHeader, Chip, EmptyState, Tile } from '../../components/ui';
import { comma, pct, signed } from '../../lib/format';
import { Page } from '../Pages';
import { ExternalLink, PageError, PageLoading } from './common';

/** Bar values are percent points: one decimal, two below 1 (0.06 → +0.06%). */
const pp = (v: number) => `${signed(v, Math.abs(v) < 1 ? 2 : 1)}%`;

/** A card's links: web links open in a new tab; a link to another card (`game-model-v1.md`)
 *  opens that card here; other repo-relative links (`../04-track1-models.md#…`) can't open from
 *  the app, so they stay as text with the path in the tooltip. */
function mdComponents(cardIds: Set<string>, onOpen: (id: string) => void): Components {
  return {
    table: ({ children }) => (
      <div className="tablewrap">
        <table>{children}</table>
      </div>
    ),
    a: ({ href, children }) => {
      if (href && /^https?:\/\//i.test(href)) {
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      }
      const card = href?.match(/^(?:\.\/)?([\w-]+)\.md(?:#.*)?$/)?.[1];
      if (card && cardIds.has(card)) {
        return (
          <button type="button" className="linkbtn" onClick={() => onOpen(card)}>
            {children}
          </button>
        );
      }
      return (
        <span className="mdref" title={href ? `In the repo: ${href}` : undefined}>
          {children}
        </span>
      );
    },
    // the app stays offline: an image shows its alt text instead of loading
    img: ({ alt }) => <span className="muted">{alt ? `[${alt}]` : ''}</span>,
  };
}

function headlineValue(h: ModelInfo['headline'][number]): string {
  if (h.value == null) return '—';
  switch (h.unit) {
    case 'brier':
    case 'mse':
      return h.value.toFixed(4);
    case 'pct': // a 0–1 share ("Pick accuracy")
      return pct(h.value, 1);
    default:
      return comma(h.value);
  }
}

/** Versioned artifacts only: ratings and the writer have no W&B version (their label is text). */
function VersionChips({ p }: { p: ModelInfo['production'] }) {
  if (!p.version) return null;
  return (
    <>
      <Chip tone="run" title={p.week_alias ? `week alias ${p.week_alias}` : undefined}>
        production · {p.version}
      </Chip>
      {p.source ? <Chip>{p.source === 'wandb' ? 'from W&B' : 'from the run summary'}</Chip> : null}
    </>
  );
}

/** Bars per metric before "Show all" (the player model has 23 stats). */
const BARS_SHOWN = 8;

function Bars({ title, bars }: { title: string; bars: NonNullable<ModelInfo['bars']> }) {
  const [all, setAll] = useState(false);
  const groups = [
    {
      metric: 'mae' as const,
      title: 'MAE: % better than the rolling baseline',
      rows: bars.filter((b) => b.metric === 'mae'),
    },
    {
      metric: 'brier' as const,
      title: 'Brier (yes / no stats): % better than the rolling baseline',
      rows: bars.filter((b) => b.metric === 'brier'),
    },
  ].filter((g) => g.rows.length);
  const top = Math.max(0, ...bars.map((b) => Math.abs(b.value)));
  const long = groups.some((g) => g.rows.length > BARS_SHOWN);
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {groups.map((g) => (
        <div key={g.metric}>
          <div className="bars-h">{g.title}</div>
          <HBars
            label={`${title}: ${g.title}`}
            max={top * 1.05 || 1}
            rows={(all ? g.rows : g.rows.slice(0, BARS_SHOWN)).map((b) => ({
              label: b.label,
              value: b.value,
              tip: `${b.label}\n${pp(b.value)} vs the rolling baseline (${b.metric === 'mae' ? 'MAE' : 'Brier'})`,
            }))}
            format={pp}
          />
        </div>
      ))}
      {long ? (
        <button
          type="button"
          className="btn sm"
          style={{ justifySelf: 'start' }}
          aria-expanded={all}
          onClick={() => setAll((a) => !a)}
        >
          {all ? 'Show the top stats only' : `Show all ${bars.length} stats`}
        </button>
      ) : null}
    </div>
  );
}

function ModelCardView({ m, onOpen }: { m: ModelInfo; onOpen: (id: string) => void }) {
  const p = m.production;
  // the production label, unless a setting already says it (the writer's model name)
  const label = p.label && !m.settings.some((s) => s.value === p.label) ? p.label : null;
  return (
    <Card className="model-card">
      <CardHeader title={m.title} right={<VersionChips p={p} />} />
      <div className="card-b">
        {m.headline.length ? (
          <div className="tiles">
            {m.headline.map((h) => (
              <Tile key={h.label} k={h.label} v={headlineValue(h)} d={h.detail} />
            ))}
          </div>
        ) : null}
        {m.bars?.length ? <Bars title={m.title} bars={m.bars} /> : null}
        {label || m.settings.length ? (
          <dl className="kv" style={{ margin: 0 }}>
            {label ? (
              <>
                <dt>Production</dt>
                <dd className={p.version ? 'mono' : undefined}>{label}</dd>
              </>
            ) : null}
            {m.settings.map((s) => (
              <Fragment key={s.label}>
                <dt>{s.label}</dt>
                <dd className={s.mono ? 'mono' : undefined}>{s.value}</dd>
              </Fragment>
            ))}
          </dl>
        ) : null}
        {m.note ? (
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            {m.note}
          </p>
        ) : null}
        {m.cards.length ? (
          <div className="model-cards">
            {m.cards.map((c) => {
              // the writer has a guide rather than a model card
              const name = c.kind === 'guide' ? 'Guide' : 'Model card';
              return (
                <button
                  key={c.id}
                  type="button"
                  className="btn sm"
                  title={c.title}
                  onClick={() => onOpen(c.id)}
                  aria-label={m.cards.length > 1 ? `${name}: ${c.title}` : name}
                >
                  {m.cards.length > 1 ? `${name} · ${c.title}` : name}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function CardDialog({
  id,
  cardIds,
  onOpen,
  onClose,
}: {
  id: string | null;
  cardIds: Set<string>;
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  const q = useModelCard(id);
  return (
    <Dialog.Root open={id != null} onOpenChange={(o) => (o ? null : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="scrim" />
        <Dialog.Content
          className="modal mdcard"
          style={{
            position: 'fixed',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            zIndex: 41,
          }}
          aria-describedby={undefined}
        >
          <header>
            <span className="eyebrow">Documentation</span>
            <Dialog.Title asChild>
              <h2>{q.data?.title ?? 'Loading…'}</h2>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" className="btn sm">
                Close
              </button>
            </Dialog.Close>
          </header>
          <div className="mdbody" key={id ?? ''}>
            {q.isPending ? (
              <p className="muted" role="status">
                Loading the card…
              </p>
            ) : q.isError ? (
              <p className="muted">Couldn't load this card: {q.error.message}</p>
            ) : (
              <article className="digest">
                <Markdown remarkPlugins={[remarkGfm]} components={mdComponents(cardIds, onOpen)}>
                  {q.data.markdown}
                </Markdown>
              </article>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ModelsPage() {
  const q = useModels();
  const [cardId, setCardId] = useState<string | null>(null);
  const projectUrl = q.data?.wandb.project_url;
  return (
    <Page
      eyebrow="Models"
      title="What runs in production"
      sub="Versions, backtests and model cards"
      right={projectUrl ? <ExternalLink href={projectUrl}>W&amp;B project</ExternalLink> : null}
    >
      {q.isPending ? (
        <PageLoading />
      ) : q.isError ? (
        <PageError error={q.error} what="the models" />
      ) : (
        <>
          <WandbBanner
            state={q.data.wandb}
            refreshing={q.isFetching}
            onRefresh={() => {
              refreshWandb(MODELS_PATH);
              void q.refetch();
            }}
          />
          {q.data.models.length ? (
            <div className="grid g2">
              {q.data.models.map((m) => (
                <ModelCardView key={m.id} m={m} onOpen={setCardId} />
              ))}
            </div>
          ) : (
            <EmptyState glyph="—" title="No production models found" />
          )}
          <div className="foot">
            Headline numbers come from the model cards in{' '}
            <span className="mono">documentation/model_cards/</span> and the backtest summaries;
            versions from W&amp;B's <span className="mono">production</span> aliases (or the week's
            run summary when W&amp;B can't be reached).
          </div>
        </>
      )}
      <CardDialog
        id={cardId}
        cardIds={new Set(q.data?.models.flatMap((m) => m.cards.map((c) => c.id)) ?? [])}
        onOpen={setCardId}
        onClose={() => setCardId(null)}
      />
    </Page>
  );
}
