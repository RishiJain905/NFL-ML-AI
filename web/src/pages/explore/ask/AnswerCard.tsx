// One answer, in the card for its status (mockup: answerCard): answered (sentences, the number
// check mark, chips, rows, query), refused (who refused it and why), which one? (an option per
// button, re-asked with `picks`), no rows (the query and the columns it would show) and error.

import { forwardRef } from 'react';
import type { AskChoice, AskExamplesResponse, AskResponse } from '../../../api/types';
import { TipTarget } from '../../../charts/TipTarget';
import {
  STAGE,
  clockTime,
  costLines,
  errorTitle,
  money,
  refusalGlyph,
  refusedBy,
  routeLines,
  secs,
  timeLines,
} from '../../../lib/ask';
import { QueryBlock } from './QueryBlock';
import { RowsTable } from './RowsTable';

type Limits = AskExamplesResponse['limits'] | undefined;

const EYEBROW: Record<AskResponse['status'], string> = {
  answered: 'Answer',
  refused: 'Not answered',
  clarify: 'Needs a choice',
  empty: 'No rows',
  error: 'Error',
};

export function MetaChips({ answer: a, limits }: { answer: AskResponse; limits: Limits }) {
  const routed = a.route === 'template' || a.route === 'generated';
  const t = timeLines(a);
  const c = costLines(a);
  return (
    <div className="ask-meta">
      {a.source ? (
        <TipTarget
          className="chip flat"
          lines={['Source', a.source.label, ...(a.source.rebuilt ? [`Tables rebuilt ${a.source.rebuilt}`] : [])]}
          label={`Source: ${a.source.label}`}
        >
          <span className="k">Source</span> {a.source.label}
        </TipTarget>
      ) : null}
      {routed ? (
        <TipTarget
          className={`chip ${a.route === 'generated' ? 'run' : 'flat'}`}
          lines={routeLines(a, limits)}
          label={`Route: ${a.route_label ?? a.route}. ${routeLines(a, limits).join('. ')}`}
        >
          <span className="k">Route</span> {a.route_label}
        </TipTarget>
      ) : null}
      <TipTarget className="chip flat" lines={t} label={`Time: ${secs(a.timing.total_s)}. ${t.slice(1).join(', ')}`}>
        <span className="k">Time</span> {secs(a.timing.total_s)}
      </TipTarget>
      <TipTarget className="chip flat" lines={c} label={`Cost: ${money(a.cost)}. ${c.join('. ')}`}>
        <span className="k">Cost</span> {money(a.cost)}
      </TipTarget>
    </div>
  );
}

function Choices({ choices, onPick }: { choices: AskChoice[]; onPick: (c: AskChoice, value: string) => void }) {
  return (
    <>
      {choices.map((c) => (
        <div key={`${c.kind}:${c.text}`} className="ask-opts" role="group" aria-label={c.text}>
          {choices.length > 1 ? <span className="eyebrow">{c.text}</span> : null}
          {c.options.map((o) => (
            <button key={o.value} type="button" className="ask-opt" onClick={() => onPick(c, o.value)}>
              {o.label}
              <span className="arrow" aria-hidden="true">
                →
              </span>
            </button>
          ))}
        </div>
      ))}
    </>
  );
}

function Body({
  answer: a,
  limits,
  onPick,
}: {
  answer: AskResponse;
  limits: Limits;
  onPick: (c: AskChoice, value: string) => void;
}) {
  if (a.status === 'answered') {
    return (
      <>
        <p className="ask-say">{a.answer}</p>
        {a.answer_kind === 'model' ? (
          <TipTarget
            className="ask-checked"
            lines={['The number check', 'The model wrote these sentences; every number in them was found in the rows below.']}
            label="Every number checked against the rows"
          >
            <span className="ic" aria-hidden="true">
              ✓
            </span>
            Every number checked against the rows
          </TipTarget>
        ) : (
          <div className="notice" role="note">
            <span className="ic" aria-hidden="true">
              i
            </span>
            <div>
              <b>Only the table is shown.</b> The model&apos;s sentences didn&apos;t pass the number check (a number in
              them wasn&apos;t in the rows), so this fixed line points to the table instead.
            </div>
          </div>
        )}
        <MetaChips answer={a} limits={limits} />
        <RowsTable columns={a.columns} rows={a.rows} rowCount={a.row_count} truncated={a.truncated} />
        <QueryBlock answer={a} />
      </>
    );
  }
  if (a.status === 'refused') {
    const r = a.refusal;
    return (
      <>
        <div className="ask-state st-refused">
          <span className="glyph" aria-hidden="true">
            {refusalGlyph(r?.rule)}
          </span>
          <h3>{r?.message ?? a.answer}</h3>
          <p className="who">
            {refusedBy(a)}
            {r?.rule ? (
              <>
                {' · rule '}
                <code>{r.rule}</code>
              </>
            ) : null}
          </p>
        </div>
        <MetaChips answer={a} limits={limits} />
        <QueryBlock answer={a} label="The query it would have run" />
      </>
    );
  }
  if (a.status === 'clarify') {
    const one = a.choices.length === 1 ? a.choices[0] : null;
    return (
      <>
        <div className="ask-state st-clarify">
          <span className="glyph" aria-hidden="true">
            Which?
          </span>
          <h3>{one ? `Which ${one.text}?` : 'Which ones?'}</h3>
          <p>
            {a.answer} Pick one and the same question is asked again with that {one?.kind ?? 'name'}.
          </p>
          <Choices choices={a.choices} onPick={onPick} />
        </div>
        <MetaChips answer={a} limits={limits} />
      </>
    );
  }
  if (a.status === 'empty') {
    return (
      <>
        <div className="ask-state st-empty">
          <span className="glyph" aria-hidden="true">
            0 rows
          </span>
          <h3>{a.answer ?? 'No rows matched.'}</h3>
          <p>The query ran and found nothing. Try a wider season range, another spelling or another source.</p>
          {a.columns.length ? (
            <div className="ask-cols" aria-label="Columns it would show">
              {a.columns.map((c) => (
                <code key={c}>{c}</code>
              ))}
            </div>
          ) : null}
        </div>
        <MetaChips answer={a} limits={limits} />
        <QueryBlock answer={a} />
      </>
    );
  }
  const e = a.error;
  const kv: [string, string | null | undefined, string][] = [
    ['Stage', e?.stage ? (STAGE[e.stage] ?? e.stage) : null, ''],
    ['Provider', e?.provider, 'mono'],
    ['Model', e?.model, 'mono'],
    ['Code', e?.code, 'mono'],
  ];
  return (
    <>
      <div className="ask-state st-error">
        <span className="glyph" aria-hidden="true">
          {e?.code === 'out_of_credits' ? '$0' : 'Error'}
        </span>
        <h3>{errorTitle(e?.code)}</h3>
        <p>{e?.message ?? a.answer ?? ''}</p>
        <dl className="ask-kv">
          {kv
            .filter(([, v]) => v)
            .map(([k, v, cls]) => (
              <div key={k} style={{ display: 'contents' }}>
                <dt>{k}</dt>
                <dd className={cls || undefined}>{v}</dd>
              </div>
            ))}
        </dl>
      </div>
      <MetaChips answer={a} limits={limits} />
      <QueryBlock answer={a} label="The last query tried" />
    </>
  );
}

export const AnswerCard = forwardRef<
  HTMLHeadingElement,
  { answer: AskResponse; limits: Limits; onPick: (c: AskChoice, value: string) => void }
>(function AnswerCard({ answer, limits, onPick }, ref) {
  return (
    <article className="card ask-card" aria-labelledby="askAnswerH">
      <div className="card-h">
        <span className="eyebrow" style={{ flexBasis: '100%' }}>
          {EYEBROW[answer.status] ?? answer.status}
        </span>
        <h2 id="askAnswerH" tabIndex={-1} ref={ref}>
          {answer.question}
        </h2>
        <span className="when">{clockTime(answer.asked_at)}</span>
      </div>
      <div className="card-b">
        <Body answer={answer} limits={limits} onPick={onPick} />
      </div>
    </article>
  );
});
