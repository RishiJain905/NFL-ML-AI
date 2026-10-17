// PC03: the play browser on a team page (mockup: playsCard, pbWeekBar, pbFilters, pbList and
// playInfo in documentation/play-calling/mockup/playcalling.js). Behind "Show plays" (fetched only
// once opened, like History). Offense / Defense follow the page: on defense it lists the plays run
// against the team. A game picker, one-value filter chips from the answer's `options` (the server
// filters), "Showing N of M", the list (rows are buttons; up / down / Home / End move through
// them), and the diagram player for the selected play (beside the list on wide screens, below it on
// narrow ones). "See a real one" plays a Big Data Bowl clip of the play's concept in the same player.

import { useState, type KeyboardEvent, type ReactNode } from 'react';
import { usePlayDiagram, usePlaycallPlays, usePlaycallRealClip } from '../../api/client';
import type { PlayDiagram, PlaySummary, PlaycallPlaysResponse, PlaycallSide, RealClipResponse } from '../../api/types';
import { PlayDiagramPlayer } from '../../charts/PlayDiagramPlayer';
import { TipTarget } from '../../charts/TipTarget';
import { Card, CardHeader } from '../../components/ui';
import { DOWN_NAME, PLAY_TAG_HELP, PLAY_TAG_NAME, PLAY_TYPE_NAME, epaText, playKey } from '../../lib/playdiagram';
import { PageError } from '../season/common';
import './explore.css';
import './playbrowser.css';

type Filters = { down: number | null; type: string | null; result: string | null };
const NO_FILTERS: Filters = { down: null, type: null, result: null };

/** The server's message without its command, and the command on its own line. */
function InlineEmpty({ message, title, glyph = '▶', children }: { message: string | null; title: string; glyph?: string; children?: ReactNode }) {
  const msg = message ?? '';
  const cmd = /`([^`]+)`/.exec(msg)?.[1];
  const text = msg.replace(/\s*\(`[^`]+`[^)]*\)/, '').replace(/`([^`]+)`/g, '$1');
  return (
    <div className="empty pbempty">
      <div className="glyph" aria-hidden="true">
        {glyph}
      </div>
      <h3>{title}</h3>
      {text ? <p>{text}</p> : null}
      {cmd ? <div className="cmd">{cmd}</div> : null}
      {children}
    </div>
  );
}

function PlayChips({ p, tips = false }: { p: PlaySummary; tips?: boolean }) {
  return (
    <>
      <span className="chip flat pbt">{PLAY_TYPE_NAME[p.type] ?? p.type}</span>
      {p.tags.map((t) =>
        tips ? (
          <TipTarget key={t} className="chip ghost pbt" lines={[PLAY_TAG_HELP[t] ?? t]}>
            {PLAY_TAG_NAME[t] ?? t}
          </TipTarget>
        ) : (
          <span key={t} className="chip ghost pbt">
            {PLAY_TAG_NAME[t] ?? t}
          </span>
        ),
      )}
      {p.result.touchdown ? <span className="chip ok pbt">TD</span> : null}
      {p.result.turnover ? <span className="chip err pbt">Turnover</span> : null}
      {!p.charted ? (
        tips ? (
          <TipTarget
            className="chip warn pbt"
            lines={["Not charted", "FTN hasn't charted this play: the hash, the box, the rushers and motion are not known, so the diagram draws more generic spots"]}
          >
            not charted
          </TipTarget>
        ) : (
          <span className="chip warn pbt">not charted</span>
        )
      ) : null}
    </>
  );
}

function WeekBar({ a, onWeek }: { a: PlaycallPlaysResponse; onWeek: (w: number) => void }) {
  const ws = a.weeks;
  const i = ws.findIndex((w) => w.week === a.week);
  const prev = i > 0 ? ws[i - 1] : null;
  const next = i >= 0 && i < ws.length - 1 ? ws[i + 1] : null;
  return (
    <div className="pbweek">
      <label className="sortsel">
        <span className="eyebrow">Game</span>
        <select aria-label="Game" value={a.week ?? ''} onChange={(e) => onWeek(Number(e.target.value))}>
          {a.week == null ? (
            <option value="" disabled>
              Pick a game
            </option>
          ) : null}
          {ws.map((w) => (
            <option key={w.week} value={w.week}>
              {w.label} · {w.plays} plays
            </option>
          ))}
        </select>
      </label>
      <span className="seg" role="group" aria-label="Step games">
        <button type="button" disabled={!prev} aria-label="Previous game" onClick={() => prev && onWeek(prev.week)}>
          ‹
        </button>
        <button type="button" disabled={!next} aria-label="Next game" onClick={() => next && onWeek(next.week)}>
          ›
        </button>
      </span>
      {a.status === 'ok' ? (
        <span className="pbcount num" aria-live="polite">
          Showing <b>{a.counts.shown}</b> of {a.counts.total} plays
        </span>
      ) : null}
    </div>
  );
}

function FilterChips({ a, filters, onChange }: { a: PlaycallPlaysResponse; filters: Filters; onChange: (f: Filters) => void }) {
  const group = <K extends keyof Filters>(key: K, label: string, opts: { value: NonNullable<Filters[K]>; label: string }[]) => (
    <div className="pbfg" role="group" aria-label={label}>
      <span className="eyebrow">{label}</span>
      <span className="pbchips">
        {opts.map((o) => {
          const on = filters[key] === o.value;
          return (
            <button
              key={String(o.value)}
              type="button"
              className="pbchip"
              aria-pressed={on}
              onClick={() => onChange({ ...filters, [key]: on ? null : o.value })}
            >
              {o.label}
            </button>
          );
        })}
      </span>
    </div>
  );
  const any = filters.down != null || filters.type != null || filters.result != null;
  return (
    <div className="pbfilters">
      {group(
        'down',
        'Down',
        a.options.down.map((d) => ({ value: d, label: DOWN_NAME[d] ?? String(d) })),
      )}
      {group('type', 'Play type', a.options.type)}
      {group('result', 'Result', a.options.result)}
      {any ? (
        <button type="button" className="pbclear" onClick={() => onChange(NO_FILTERS)}>
          Clear filters
        </button>
      ) : null}
    </div>
  );
}

function filterWords(a: PlaycallPlaysResponse): string {
  const f = a.filters;
  const out: string[] = [];
  if (f.down != null) out.push(`on ${DOWN_NAME[f.down] ?? f.down} down`);
  if (f.type) out.push((a.options.type.find((o) => o.value === f.type)?.label ?? f.type).toLowerCase());
  if (f.result) out.push((a.options.result.find((o) => o.value === f.result)?.label ?? f.result).toLowerCase());
  return out.join(', ') || 'filtered';
}

function PlayList({ a, selected, onPick, onClear }: { a: PlaycallPlaysResponse; selected: string | null; onPick: (k: string) => void; onClear: () => void }) {
  if (!a.plays.length) {
    return (
      <InlineEmpty message={null} title="No plays match these filters" glyph="∅">
        <p>
          {a.counts.total} plays in {a.game?.label ?? 'this game'}; none is {filterWords(a)}.
        </p>
        <button type="button" className="btn sm" onClick={onClear}>
          Clear filters
        </button>
      </InlineEmpty>
    );
  }
  const keys = a.plays.map(playKey);
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const j =
      e.key === 'ArrowDown' ? i + 1 : e.key === 'ArrowUp' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? keys.length - 1 : null;
    if (j == null) return;
    e.preventDefault();
    const k = Math.max(0, Math.min(keys.length - 1, j));
    if (k === i) return;
    onPick(keys[k]);
    const list = e.currentTarget.closest('ol');
    (list?.querySelectorAll<HTMLButtonElement>('.prow')[k])?.focus();
  };
  return (
    <ol className="plist" aria-label={`Plays${a.game ? `, ${a.game.label}` : ''}`}>
      {a.plays.map((p, i) => {
        const k = keys[i];
        const on = k === selected;
        return (
          <li key={k}>
            <button
              type="button"
              className={`prow${on ? ' on' : ''}${p.result.first_down ? ' fd' : ''}`}
              aria-pressed={on}
              data-play={k}
              onClick={() => onPick(k)}
              onKeyDown={(e) => onKey(e, i)}
            >
              <span className="pr1">
                <span className="pq num">
                  {p.quarter_text ?? ''} {p.clock ?? ''}
                </span>
                <b className="pdd">{p.down_text ?? 'No down'}</b>
                <span className="psp muted">{p.spot ?? ''}</span>
                <span className="pch">
                  <PlayChips p={p} />
                </span>
                <span className="pres">{p.result.text}</span>
                <span className="pepa num">
                  {epaText(p.epa)}
                  <small>EPA</small>
                </span>
              </span>
              {p.desc ? <span className="pr2">{p.desc}</span> : null}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/** Under the field: the play's header, the description, and Known / Approximate / Not in the data. */
function PlayInfo({ dg }: { dg: PlayDiagram }) {
  const p = dg.play;
  const list = (items: string[], none: string) =>
    items.length ? items.map((x) => <li key={x}>{x}</li>) : <li className="muted">{none}</li>;
  return (
    <div className="pdinfo">
      <div className="pdhead">
        <span className="eyebrow">
          {p.game_label ?? ''}
          {p.season ? ` · ${p.season}` : ''}
        </span>
        <div className="pdh1">
          <b className="num">
            {p.quarter_text ?? ''} {p.clock ?? ''}
          </b>
          <b>{p.down_text ?? ''}</b>
          <span className="muted">{p.spot ?? ''}</span>
          <PlayChips p={p} tips />
        </div>
        <div className="pdh2">
          <b>{p.result.text}</b>
          <span className="muted num">EPA {epaText(p.epa)}</span>
        </div>
        {p.desc ? <p className="pddesc">{p.desc}</p> : null}
      </div>
      <div className="pdkau">
        <section aria-label="Known">
          <h4>
            <i className="kk kn" aria-hidden="true" />
            Known
          </h4>
          <ul>{list(dg.known, 'None')}</ul>
        </section>
        <section aria-label="Approximate">
          <h4>
            <i className="kk ap" aria-hidden="true" />
            Approximate <small>drawn at generic spots</small>
          </h4>
          <ul>{list(dg.approximate, 'Nothing')}</ul>
        </section>
        <section aria-label="Not in the data">
          <h4>
            <i className="kk un" aria-hidden="true" />
            Not in the data <small>not drawn</small>
          </h4>
          <ul>{list(dg.unknown, 'None')}</ul>
        </section>
      </div>
      {dg.sources.length ? <p className="pdsrc muted">Sources: {dg.sources.join(' · ')}</p> : null}
    </div>
  );
}

function ClipNav({ clip, onIndex, onBack }: { clip: RealClipResponse; onIndex: (i: number) => void; onBack: () => void }) {
  const n = clip.examples.length;
  const i = clip.index ?? 0;
  const label = clip.examples.find((e) => e.index === i)?.label;
  return (
    <div className="pdreal real">
      <button type="button" className="btn sm" onClick={onBack}>
        <span aria-hidden="true">←</span> Back to the reconstruction
      </button>
      <span className="grow">
        <b>{clip.name}</b>
        {clip.match ? <span className="muted"> · {clip.match}</span> : null}
        {label ? <span className="muted"> · {label}</span> : null}
      </span>
      {n > 1 ? (
        <span className="seg" role="group" aria-label="Real examples">
          <button type="button" disabled={i <= 0} aria-label="Previous example" onClick={() => onIndex(i - 1)}>
            ‹
          </button>
          <span className="exn num" aria-live="polite">
            {i + 1} of {n}
          </span>
          <button type="button" disabled={i >= n - 1} aria-label="Next example" onClick={() => onIndex(i + 1)}>
            ›
          </button>
        </span>
      ) : null}
    </div>
  );
}

function RealClip({
  concept,
  index,
  onIndex,
  onBack,
}: {
  concept: string;
  index: number;
  onIndex: (i: number) => void;
  onBack: () => void;
}) {
  const q = usePlaycallRealClip(concept, index, true);
  const clip = q.data;
  const back = (
    <div className="pdreal real">
      <button type="button" className="btn sm" onClick={onBack}>
        <span aria-hidden="true">←</span> Back to the reconstruction
      </button>
    </div>
  );
  if (q.isLoading) {
    return (
      <div className="card pdcard">
        {back}
        <div className="muted pdwait" role="status">
          Loading the real example…
        </div>
      </div>
    );
  }
  if (q.error || !clip) {
    return (
      <div className="card pdcard">
        {back}
        {q.error ? <PageError error={q.error} what="the real example" /> : null}
      </div>
    );
  }
  if (clip.status !== 'ok' || !clip.diagram) {
    return (
      <div className="card pdcard">
        <ClipNav clip={clip} onIndex={onIndex} onBack={onBack} />
        <InlineEmpty
          message={clip.message}
          title={clip.status === 'no_library' ? 'No Big Data Bowl library here' : `No real ${clip.name.toLowerCase()} yet`}
        />
      </div>
    );
  }
  return (
    <PlayDiagramPlayer key={`${concept}-${clip.index}`} diagram={clip.diagram} autoplay top={<ClipNav clip={clip} onIndex={onIndex} onBack={onBack} />}>
      {clip.note ? <p className="pdnote muted">{clip.note}</p> : null}
      <PlayInfo dg={clip.diagram} />
    </PlayDiagramPlayer>
  );
}

function DiagramPanel({ k, autoplay }: { k: string; autoplay: boolean }) {
  const [game, pid] = k.split('/');
  const q = usePlayDiagram(game, Number(pid));
  const [real, setReal] = useState<{ concept: string; index: number } | null>(null);
  if (real) {
    return <RealClip concept={real.concept} index={real.index} onIndex={(i) => setReal({ ...real, index: i })} onBack={() => setReal(null)} />;
  }
  if (q.isLoading) {
    return (
      <div className="card pdcard">
        <div className="muted pdwait" role="status">
          Loading the play…
        </div>
      </div>
    );
  }
  if (q.error) {
    return (
      <div className="card pdcard">
        <InlineEmpty message={q.error.message} title="This play can't be drawn" />
      </div>
    );
  }
  const dg = q.data?.diagram;
  if (!dg) return null;
  const ex = dg.real_example;
  const top = ex ? (
    <div className="pdreal">
      <button type="button" className="btn sm" onClick={() => setReal({ concept: ex.concept, index: 0 })}>
        <span aria-hidden="true">▶</span> See a real one: {ex.name}
      </button>
      <span className="muted">
        matches {ex.match ?? 'this play'} · {ex.count} real example{ex.count === 1 ? '' : 's'}
      </span>
    </div>
  ) : null;
  return (
    <PlayDiagramPlayer key={k} diagram={dg} autoplay={autoplay} top={top}>
      <PlayInfo dg={dg} />
    </PlayDiagramPlayer>
  );
}

function BrowserBody({ team, season, side }: { team: string; season: number; side: PlaycallSide }) {
  const [week, setWeek] = useState<number | null>(null);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [picked, setPicked] = useState<string | null>(null);
  const [clicked, setClicked] = useState<string | null>(null); // the play the viewer picked: it plays at once
  const q = usePlaycallPlays(team, side, { season, week, ...filters });
  // keep the last answer on screen while a new week or filter loads
  const [last, setLast] = useState<PlaycallPlaysResponse | undefined>(q.data);
  if (q.data && q.data !== last) setLast(q.data);
  const a = q.data ?? last;
  if (q.error && !a) return <PageError error={q.error} what="the plays" />;
  if (!a) {
    return (
      <div className="muted" role="status">
        Loading the plays…
      </div>
    );
  }
  if (a.status === 'not_built') return <InlineEmpty message={a.message} title={`No play-by-play tables for ${a.season}`} />;
  if (a.status === 'no_plays') return <InlineEmpty message={a.message} title="No plays" />;
  const pick = (k: string) => {
    setPicked(k);
    setClicked(k);
  };
  const onWeek = (w: number) => {
    setWeek(w);
    setPicked(null);
    setClicked(null);
  };
  const weekBar = <WeekBar a={a} onWeek={onWeek} />;
  if (a.status === 'no_week') {
    return (
      <div className="pbbody">
        {weekBar}
        <InlineEmpty message={a.message} title="No plays that week" />
      </div>
    );
  }
  const keys = a.plays.map(playKey);
  const selected = picked && keys.includes(picked) ? picked : (keys[0] ?? null);
  return (
    <div className="pbbody" aria-busy={q.isFetching || undefined}>
      {weekBar}
      <FilterChips a={a} filters={filters} onChange={setFilters} />
      {q.error ? <PageError error={q.error} what="the plays" /> : null}
      <div className="pbgrid">
        <div className="pbleft">
          <PlayList a={a} selected={selected} onPick={pick} onClear={() => setFilters(NO_FILTERS)} />
          {a.library ? null : (
            <p className="pblib muted">
              <span aria-hidden="true">○</span> Real examples ("See a real one") need the Big Data Bowl library on this computer.
            </p>
          )}
        </div>
        <div className="pbright">{selected ? <DiagramPanel key={selected} k={selected} autoplay={clicked === selected} /> : null}</div>
      </div>
    </div>
  );
}

export function PlayBrowser({ team, season, side }: { team: string; season: number; side: PlaycallSide }) {
  const [open, setOpen] = useState(false);
  const sideNote = side === 'offense' ? `${team}'s plays, one game at a time` : `The plays run against ${team}'s defense, one game at a time`;
  return (
    <Card className="pbcard">
      <CardHeader
        title="Plays"
        sub={sideNote}
        right={
          <button type="button" className="btn sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? 'Hide' : 'Show plays'}
          </button>
        }
      />
      <div className="card-b">
        {open ? (
          <BrowserBody key={`${team}-${season}-${side}`} team={team} season={season} side={side} />
        ) : (
          <p className="muted pbintro">
            Every play of a game, filtered by down, play type and result. Pick one to watch it drawn as a playbook card,{' '}
            <b>reconstructed from the charting data</b> (there's no public player tracking for these seasons). Opens on request.
          </p>
        )}
      </div>
    </Card>
  );
}

