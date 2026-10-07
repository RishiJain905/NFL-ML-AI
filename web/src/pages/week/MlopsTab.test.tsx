import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { resetRefreshes } from '../../api/client';
import type { MlopsHealthResponse, MlopsWandbResponse } from '../../api/types';
import { mockApi, renderApp } from '../../test/utils';
import { MlopsTab } from './MlopsTab';
import { resetSection } from './mlops/section';
import {
  ARTIFACTS_W5,
  ARTIFACTS_W5_DOWN,
  ARTIFACTS_W5_NO_PROJECT,
  HEALTH_NONE,
  HEALTH_PROBLEMS,
  HEALTH_W4,
  HEALTH_W5,
  HEALTH_W6,
  PROJECT_URL,
  WANDB_DOWN,
  WANDB_STALE,
  WANDB_W4,
  WANDB_W5,
  WANDB_W5_DOWN,
  WANDB_W5_NO_PROJECT,
} from './mlopsFixtures';

const W5 = { season: 2026, week: 5, isCurrent: true, lastPublishedWeek: 4 };
const W4 = { season: 2026, week: 4, isCurrent: false, lastPublishedWeek: 4 };

const H = (w: number) => `/api/weeks/2026/${w}/mlops/health`;
const W = (w: number) => `/api/weeks/2026/${w}/mlops/wandb`;
const A = (w: number) => `/api/weeks/2026/${w}/mlops/artifacts`;

beforeEach(() => {
  resetSection();
  resetRefreshes();
});

/** A card by its heading. */
const card = (name: string | RegExp) => screen.getByRole('heading', { name }).closest('.card') as HTMLElement;

async function openSection(label: string) {
  fireEvent.click(screen.getByRole('button', { name: label }));
}

/** The W&B banner (role=status too, like the loading line, so wait for the banner itself). */
async function findBanner(container: HTMLElement): Promise<HTMLElement> {
  await waitFor(() => expect(container.querySelector('.wandb-banner')).not.toBeNull());
  return container.querySelector('.wandb-banner') as HTMLElement;
}

function expectClean(container: HTMLElement) {
  expect(container.textContent).not.toMatch(/NaN|undefined|\bnull\b|\[object/);
}

describe('MlopsTab switcher', () => {
  it('opens on Health, fetches only the open section and changes the blurb', async () => {
    const fetchMock = mockApi({ [H(5)]: HEALTH_W5, [W(5)]: WANDB_W5, [A(5)]: ARTIFACTS_W5 });
    renderApp(<MlopsTab {...W5} />);
    const group = screen.getByRole('group', { name: 'MLOps sections' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['Health', 'W&B runs', 'Artifacts']);
    expect(within(group).getByRole('button', { name: 'Health' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Did the run go cleanly, and was the data fresh?')).toBeInTheDocument();
    await screen.findByText('Run status');
    const paths = () => fetchMock.mock.calls.map((c) => String(c[0]));
    expect(paths()).toEqual([H(5)]);

    await openSection('W&B runs');
    expect(screen.getByRole('button', { name: 'W&B runs' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Health' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/main charts redrawn here/)).toBeInTheDocument();
    await screen.findByRole('heading', { name: 'Runs this week' });
    expect(paths()).toContain(W(5));
    expect(paths()).not.toContain(A(5));

    await openSection('Artifacts');
    await screen.findByRole('heading', { name: 'What the week-5 digest was built from' });
    expect(paths()).toContain(A(5));
  });

  it('remembers the section while moving between weeks and tabs', async () => {
    mockApi({ [H(5)]: HEALTH_W5, [W(5)]: WANDB_W5, [W(4)]: WANDB_W4 });
    function Harness() {
      const [week, setWeek] = useState(5);
      return (
        <>
          <button type="button" onClick={() => setWeek(4)}>
            go to week 4
          </button>
          <MlopsTab {...(week === 5 ? W5 : W4)} />
        </>
      );
    }
    const first = renderApp(<Harness />);
    await openSection('W&B runs');
    await screen.findByRole('heading', { name: 'Runs this week' });
    fireEvent.click(screen.getByRole('button', { name: 'go to week 4' }));
    expect(screen.getByRole('button', { name: 'W&B runs' })).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(screen.getByText('digest-2026-w04')).toBeInTheDocument());

    first.unmount(); // another tab and back
    renderApp(<MlopsTab {...W4} />);
    expect(screen.getByRole('button', { name: 'W&B runs' })).toHaveAttribute('aria-pressed', 'true');
    expect(window.sessionStorage.getItem('cr.mlopsSection')).toBe('wandb');
  });

  it('still works when browser storage is blocked', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    mockApi({ [H(5)]: HEALTH_W5, [A(5)]: ARTIFACTS_W5 });
    renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    await openSection('Artifacts');
    expect(screen.getByRole('button', { name: 'Artifacts' })).toHaveAttribute('aria-pressed', 'true');
    await screen.findByText('Versions in all');
  });
});

describe('MlopsTab → Health', () => {
  it('renders week 5: tiles, step timings, what ran', async () => {
    mockApi({ [H(5)]: HEALTH_W5 });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const tiles = container.querySelector('.tiles') as HTMLElement;
    expect(tiles.querySelectorAll('.tile')).toHaveLength(5);
    expect(tiles).toHaveTextContent('ok8/8 steps ok · 0 degraded');
    expect(tiles).toHaveTextContent('30datasets0 failures · snapshot 2026-10-06');
    expect(tiles).toHaveTextContent('17/1712 blocking, all pass');
    expect(tiles).toHaveTextContent('0');
    expect(tiles).toHaveTextContent('6 signals · 5 need more weeks'); // counted by signal, not by row
    expect(tiles).toHaveTextContent('1,86230 team rows · all written to the graph');

    const steps = card('Step timings');
    const bars = within(within(steps).getByRole('list', { name: 'Seconds per step' })).getAllByRole('listitem');
    expect(bars.map((b) => b.querySelector('.hbar-label')?.textContent)).toEqual(['ingest', 'ready', 'curate', 'ratings', 'game', 'graph', 'player', 'digest', 'records']);
    expect(bars[7]).toHaveTextContent('8m 53s');
    expect(steps).toHaveTextContent('The digest step is the long pole (54% of the time in steps): 8m 53s of 16m 29s.');

    const ran = card('What ran');
    const kv = (k: string) => within(ran).getByText(k, { selector: 'dt' }).nextElementSibling as HTMLElement;
    expect(kv('Game model')).toHaveTextContent('game-model-v0:2026-w05');
    expect(kv('Trained through')).toHaveTextContent('2026-w04');
    expect(kv('Player model')).toHaveTextContent('player-model-v1:2026-w05 · 23 stats');
    expect(kv('Team model')).toHaveTextContent('team-model-v1:2026-w05');
    expect(kv('Graph build')).toHaveTextContent('17,837 nodes · 596,237 relationships');
    expect(kv('Digest writer')).toHaveTextContent('z-ai/glm-5.3-flash');
    expect(kv('Route')).toHaveTextContent('openrouter → Novita · 1 call');
    expect(kv('Prompt')).toHaveTextContent('248ed5fb0b33');
    expect(kv('Promoted')).toHaveTextContent('game-model, player-model, team-model → production');
    expect(kv('Command')).toHaveTextContent('nfl weekly run --auto --expect-week 5');
    expect(kv('Launched by')).toHaveTextContent('agent · via control room');
    expectClean(container);
  });

  it('groups data freshness by source and lists nothing as stale when nothing is', async () => {
    mockApi({ [H(5)]: HEALTH_W5 });
    renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const fresh = card('Data freshness');
    expect(fresh).toHaveTextContent('30 sources · none stale');
    expect(within(fresh).getAllByText('fresh')).toHaveLength(30);
    expect([...fresh.querySelectorAll('tr.grp .sub-h')].map((n) => n.textContent)).toEqual([
      'nflverse',
      'ESPN',
      'Next Gen Stats site',
      'Open-Meteo weather',
      'The Odds API',
    ]);
    expect(fresh.querySelector('.tablewrap')).toHaveClass('ml-scroll');
  });

  it('leaves out the run\'s boilerplate notes but keeps the informative ones', async () => {
    mockApi({ [H(5)]: HEALTH_W5 });
    renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const fresh = card('Data freshness');
    expect(within(fresh).queryByText('last ingest: ok')).toBeNull();
    expect(within(fresh).getByText('last ingest: ok; research only')).toBeInTheDocument();
    expect(within(fresh).getAllByText(/snapshot 2026-10-06 · 1 d old/).length).toBeGreaterThan(20);
  });

  it('shows stale and failed rows first, with their notes', async () => {
    mockApi({ [H(5)]: HEALTH_PROBLEMS });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const fresh = card('Data freshness');
    expect(fresh).toHaveTextContent('30 sources · 2 stale or failed');
    const datasets = [...fresh.querySelectorAll('td.mono')].map((n) => n.textContent);
    expect(datasets.slice(0, 2)).toEqual(['pfr_rec', 'schedules']); // nflverse first (it has the stale row), stale row first within it
    expect(datasets.indexOf('odds_totals')).toBeLessThan(datasets.indexOf('espn_scoreboard')); // the failed source before the clean ones
    const stale = within(fresh).getByText('pfr_rec').closest('tr') as HTMLElement;
    expect(stale).toHaveTextContent('snapshot 2026-09-27 · through week 3 · expected week 4 · 9 d old');
    expect(stale).toHaveTextContent('nflverse is a week behind');
    expect(within(stale).getByText('stale')).toBeInTheDocument();
    const failed = within(fresh).getByText('odds_totals').closest('tr') as HTMLElement;
    expect(within(failed).getByText('failed')).toBeInTheDocument();

    const tiles = container.querySelector('.tiles') as HTMLElement;
    expect(tiles).toHaveTextContent('degraded7/8 steps ok · 1 degraded');
    expect(tiles).toHaveTextContent('1 failures');
    const bars = within(card('Step timings')).getAllByRole('listitem');
    expect(bars.map((b) => b.querySelector('.hbar-label')?.textContent)).toContain('graph · degraded');
  });

  it('compares with last week: change, flags and a trend with its tooltip', async () => {
    mockApi({ [H(6)]: HEALTH_W6 });
    renderApp(<MlopsTab season={2026} week={6} isCurrent lastPublishedWeek={5} />);
    await screen.findByText('Run status');
    const vs = card('This week vs last week');
    expect(within(vs).getByRole('columnheader', { name: 'Week 6' })).toBeInTheDocument();
    expect(within(vs).getByRole('columnheader', { name: 'Week 5' })).toBeInTheDocument();
    const row = (m: string) => within(within(vs).getByText(m).closest('tr') as HTMLElement).getAllByRole('cell').map((c) => c.textContent);
    expect(row('Time in steps').slice(0, 4)).toEqual(['Time in steps', '13m 32s', '16m 29s', '−2m 57s']);
    expect(row('LLM cost').slice(0, 4)).toEqual(['LLM cost', '$0.015', '$0.011', '+$0.004']);
    expect(row('Games predicted').slice(0, 4)).toEqual(['Games predicted', '14', '15', '−1']);
    expect(row('Graph nodes').slice(0, 4)).toEqual(['Graph nodes', '17,837', '17,837', 'same']);
    expect(row('Digest passed first time').slice(0, 4)).toEqual(['Digest passed first time', 'no', 'yes', 'changed']);

    const trend = within(vs).getByRole('img', { name: 'Time in steps by week: week 5 16m 29s, week 6 13m 32s' });
    fireEvent.focus(trend);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('Week 5: 16m 29s');
    expect(tip).toHaveTextContent('Week 6: 13m 32s');
  });

  it("says there's no earlier run when last week has no record", async () => {
    mockApi({ [H(5)]: HEALTH_W5 });
    renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const vs = card('This week vs last week');
    expect(within(vs).getByRole('columnheader', { name: 'Week 4' })).toBeInTheDocument();
    const first = within(vs).getByText('Time in steps').closest('tr') as HTMLElement;
    expect(first).toHaveTextContent('16m 29s');
    expect(first).toHaveTextContent('no run');
    expect(within(first).getAllByRole('cell')[3]).toHaveTextContent('—');
    expect(within(vs).getByText('$0.011')).toBeInTheDocument();
    expect(within(vs).getByText('yes')).toBeInTheDocument();
  });

  it('lists ingest dataset by dataset and the quality checks, both in scroll boxes', async () => {
    mockApi({ [H(5)]: HEALTH_W5 });
    renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const ingest = card('Ingest, dataset by dataset');
    expect(ingest).toHaveTextContent('raw/_runs/ingest-20261006T202805.json · 2026-10-06');
    expect(ingest.querySelector('.tablewrap')).toHaveClass('ml-scroll');
    expect(ingest.querySelectorAll('tbody tr')).toHaveLength(30);
    expect(within(ingest).getByText('pbp').closest('tr')).toHaveTextContent('18,642');
    expect(within(ingest).getAllByText('ok')).toHaveLength(30);

    const quality = card('Quality checks');
    expect(quality.querySelector('.tablewrap')).toHaveClass('ml-scroll');
    expect(quality.querySelectorAll('tbody tr')).toHaveLength(17);
    expect(within(quality).getAllByText('pass')).toHaveLength(17);
    expect(within(quality).getAllByText('block')).toHaveLength(12);
    expect(within(quality).getAllByText('warn')).toHaveLength(5);
    expect(within(quality).queryByText(/later curate run/)).toBeNull();
  });

  it("notes that the quality file is a later curate run's, and marks failed and flagged checks", async () => {
    mockApi({ [H(5)]: HEALTH_PROBLEMS });
    renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const quality = card('Quality checks');
    expect(quality).toHaveTextContent('This file holds a later curate run');
    expect(quality).toHaveTextContent("Only the counts in this run's summary (16 of 17 passed) are this run's");
    expect(quality).toHaveTextContent('Failed in this run:');
    const row = within(quality.querySelector('table') as HTMLElement).getByText('odds_rows_per_game').closest('tr') as HTMLElement;
    expect(row).toHaveTextContent('13 of 15 games have odds');
    expect(within(row).getByText('flagged')).toBeInTheDocument(); // a failed warn-level check is flagged, not failed
    const ingestRow = within(card('Ingest, dataset by dataset')).getByText('odds_totals').closest('tr') as HTMLElement;
    expect(ingestRow).toHaveTextContent('HTTP 429');
    expect(within(ingestRow).getByText('failed')).toBeInTheDocument();
  });

  it('groups the drift signals: player signals fold into one block, alerts come first', async () => {
    mockApi({ [H(5)]: HEALTH_W5 });
    renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const drift = card('Drift signals');
    expect(within(drift).getByText('no alerts')).toBeInTheDocument();
    const names = [...drift.querySelectorAll('.signal > b')].map((n) => n.textContent);
    expect(names).toEqual(['game_vs_elo', 'calibration', 'player_vs_baseline · 7 groups', 'player_prob_vs_baseline · 6 groups', 'data_freshness', 'checks']);
    const player = within(drift).getByText('player_vs_baseline', { exact: false }).closest('.signal') as HTMLElement;
    expect(player).toHaveTextContent('insufficient dataQB, RB, WR/TE, EDGE/DL, LB/S, CB/S, TEAM');
    // the run prefixes each sentence with its group ("QB: 4 scored ..."): without it they match, so it's said once
    expect(within(player).getAllByText(/^4 scored week\(s\) so far/)).toHaveLength(1);
    const freshness = within(drift).getByText('data_freshness').closest('.signal') as HTMLElement;
    expect(freshness).toHaveTextContent('ok');
    expect(freshness).toHaveTextContent('No snapshot is older than 7 days');
    expect(freshness).toHaveTextContent('value 0 vs threshold 1');
    expect(freshness).toHaveTextContent('Response: No action needed.');
  });

  it('puts a drift alert first with its value, threshold and response', async () => {
    mockApi({ [H(5)]: HEALTH_PROBLEMS });
    renderApp(<MlopsTab {...W5} />);
    await screen.findByText('Run status');
    const drift = card('Drift signals');
    expect(within(drift).getByText('1 alert')).toBeInTheDocument();
    const first = drift.querySelector('.signal') as HTMLElement;
    expect(first).toHaveTextContent('calibration');
    expect(first).toHaveTextContent('alert');
    expect(first).toHaveTextContent('Season ECE is above the noise level.');
    expect(first).toHaveTextContent('value 0.061 vs threshold 0.05');
    expect(first).toHaveTextContent('Response: Look at the calibration chart before trusting the picks.');
  });

  it('shows what exists for a week without run_summary.json, with the notice', async () => {
    mockApi({ [H(4)]: HEALTH_W4 });
    const { container } = renderApp(<MlopsTab {...W4} />);
    expect(await screen.findByText(/Week 4 ran before run records existed/)).toBeInTheDocument();
    const tiles = container.querySelector('.tiles') as HTMLElement;
    expect(tiles).toHaveTextContent('Data—');
    expect(tiles).toHaveTextContent('Quality checks—');
    expect(tiles).toHaveTextContent('Drift alerts—');
    expect(tiles).toHaveTextContent('no signals recorded');
    const steps = card('Step timings');
    expect(steps).toHaveTextContent('24m 45s');
    expect(steps).toHaveTextContent('The digest step is the long pole (84% of the time in steps)');
    const ran = card('What ran');
    expect(within(ran).getByText('Game model')).toBeInTheDocument();
    expect(within(ran).queryByText('Prompt')).toBeNull();
    expect(within(ran).queryByText('Promoted')).toBeNull(); // unknown, not "nothing"
    expect(card('Data freshness')).toHaveTextContent("This week has no freshness table: the run summary isn't there.");
    expect(card('Ingest, dataset by dataset')).toHaveTextContent('No ingest manifest was found for this run.');
    expect(card('Quality checks')).toHaveTextContent("This file holds a later curate run (");
    expect(card('Quality checks')).toHaveTextContent("This run's own checks weren't kept, so none are listed.");
    expect(card('Drift signals')).toHaveTextContent("no drift record");
    expect(card('This week vs last week')).toHaveTextContent('pipeline_history.parquet');
    expectClean(container);
  });

  it('says so when the run saved no quality file at all', async () => {
    const none: MlopsHealthResponse = { ...HEALTH_W4, quality: { run_at: null, match: 'none', checks: [], failed: [] } };
    mockApi({ [H(4)]: none });
    renderApp(<MlopsTab {...W4} />);
    await screen.findByText(/Week 4 ran before run records existed/);
    expect(card('Quality checks')).toHaveTextContent('No quality file was saved for this run.');
  });

  it('shows an empty state with links for the current week before its run, and a plain one for a past week', async () => {
    mockApi({ [H(7)]: HEALTH_NONE });
    renderApp(<MlopsTab season={2026} week={7} isCurrent lastPublishedWeek={6} />);
    expect(await screen.findByRole('heading', { name: 'Run health appears when the run starts' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to the pipeline' })).toHaveAttribute('href', '/week/2026/7/pipeline');
    expect(screen.getByRole('link', { name: "See week 6's MLOps" })).toHaveAttribute('href', '/week/2026/6/mlops');
    expect(screen.queryByText('Run status')).toBeNull();
  });

  it('says plainly that a past week has no run records', async () => {
    mockApi({ [H(7)]: HEALTH_NONE });
    renderApp(<MlopsTab season={2026} week={7} isCurrent={false} lastPublishedWeek={6} />);
    expect(await screen.findByRole('heading', { name: 'No run records for week 7' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Go to the pipeline' })).toBeNull();
  });

  it('shows an error notice when the API fails', async () => {
    mockApi({});
    renderApp(<MlopsTab {...W5} />);
    expect(await screen.findByText(/Couldn't load this tab/)).toBeInTheDocument();
  });
});

describe('MlopsTab → W&B runs', () => {
  it('lists the current runs, hides re-runs behind a toggle and links every run', async () => {
    mockApi({ [W(5)]: WANDB_W5 });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    const runs = await screen.findByRole('list', { name: 'W&B runs this week' });
    expect(within(runs).getAllByRole('listitem')).toHaveLength(8);
    const pipeline = within(runs).getByText('pipeline-2026-w05').closest('li') as HTMLElement;
    expect(pipeline).toHaveTextContent('weekly-pipeline / pipeline');
    expect(within(pipeline).getByText('pipeline', { selector: '.job' })).toBeInTheDocument();
    expect(within(pipeline).getByRole('link', { name: 'pipeline-2026-w05 (i8fhvizy) in W&B' })).toHaveAttribute('href', `${PROJECT_URL}/runs/i8fhvizy`);
    expect(within(pipeline).getByRole('link')).toHaveAttribute('target', '_blank');
    expect(within(pipeline).getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer');
    expect(within(runs).queryByText('b7d2k9ve ↗')).toBeNull();

    const toggle = screen.getByRole('button', { name: 'Show 1 earlier run (re-runs)' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(within(runs).getAllByRole('listitem')).toHaveLength(9);
    const old = within(runs).getByText('b7d2k9ve ↗').closest('li') as HTMLElement;
    expect(old).toHaveTextContent('re-run');
    fireEvent.click(screen.getByRole('button', { name: 'Hide earlier runs' }));
    expect(within(runs).getAllByRole('listitem')).toHaveLength(8);

    expect(within(card('Runs this week')).getByRole('link', { name: 'project ↗' })).toHaveAttribute('href', PROJECT_URL);
    expect(container.querySelector('.wandb-banner')).toBeNull(); // no banner while W&B answered
    expectClean(container);
  });

  it('has the season-dashboard card with the report, the current run and this week\'s run', async () => {
    mockApi({ [W(5)]: WANDB_W5 });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    const dash = await screen.findByRole('heading', { name: 'Season dashboard' }).then((h) => h.closest('.card') as HTMLElement);
    expect(within(dash).getByRole('link', { name: 'report ↗' })).toHaveAttribute('href', WANDB_W5.dashboard.url);
    expect(dash).toHaveTextContent('now hkf2hana (week 5)');
    expect(within(dash).getAllByRole('link', { name: /hkf2hana/ })[0]).toHaveAttribute('href', `${PROJECT_URL}/runs/hkf2hana`);
    expect(dash).toHaveTextContent("This week's dashboard run: hkf2hana ↗");
    expect(within(dash).getByRole('link', { name: 'Season → Scorecard' })).toHaveAttribute('href', '/season/2026/scorecard');
  });

  it('draws the game-fit dumbbell sorted by the gap, with the run linked and the source named', async () => {
    mockApi({ [W(5)]: WANDB_W5 });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    await screen.findByRole('heading', { name: 'Runs this week' });
    const fit = card('Game fit: model only vs market');
    expect(fit).toHaveTextContent('train-2026-w05 · slate/*');
    expect(within(fit).getByRole('link', { name: 'train-2026-w05 (1jmmh8g9) in W&B' })).toHaveAttribute('href', `${PROJECT_URL}/runs/1jmmh8g9`);
    expect(fit).toHaveTextContent('Drawn from predictions_games.parquet');
    expect([...fit.querySelectorAll('.db-label')].map((n) => n.textContent)).toEqual(['NYJ at NE', 'BAL at KC', 'DET at GB', 'SEA at SF', 'BUF at MIA', 'DAL at PHI']);
    expect(fit.querySelector('.legend')).toHaveTextContent('Model only');
    expect(fit).toHaveTextContent('1 game(s) have no market line yet and are left out.');
    expect(within(fit).getByRole('img', { name: /^SEA at SF: model only SF 40%, market SF 41%, gap −1 pts$/ })).toBeInTheDocument();
  });

  it('draws the player scoreboard, naming the scored week and the live rows', async () => {
    mockApi({ [W(5)]: WANDB_W5 });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    await screen.findByRole('heading', { name: 'Runs this week' });
    const sb = card('Player scoreboard: vs the baseline');
    expect(sb).toHaveTextContent("Graded by this run: week 4's results, live.");
    const rows = within(within(sb).getByRole('list', { name: 'Player model improvement over the baseline, by group' })).getAllByRole('listitem');
    expect(rows.map((r) => r.textContent)).toEqual(['QB+5.8%', 'WR+4.1%', 'RB+3.2%', 'DB−0.8%']);
    expect(sb).toHaveTextContent('Not scored yet: TE.');
    expect(sb).toHaveTextContent('scoreboard-2026-w04 · scoreboard/improvement_*');
  });

  it('labels walk-forward rows when no live week has been graded', async () => {
    const backtest: MlopsWandbResponse = {
      ...WANDB_W5,
      cards: { ...WANDB_W5.cards, player_scoreboard: { ...WANDB_W5.cards.player_scoreboard, mode: 'backtest', scored_week: 3 } },
    };
    mockApi({ [W(5)]: backtest });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    await screen.findByRole('heading', { name: 'Runs this week' });
    expect(card('Player scoreboard: vs the baseline')).toHaveTextContent("Week 3's walk-forward rows (no live week has been graded yet).");
  });

  it('does not repeat the walk-forward caveat when the run supplies its own note', async () => {
    const backtest: MlopsWandbResponse = {
      ...WANDB_W5,
      cards: {
        ...WANDB_W5.cards,
        player_scoreboard: {
          ...WANDB_W5.cards.player_scoreboard,
          mode: 'backtest',
          scored_week: 3,
          note: 'Week 3 has walk-forward rows only (before the live player model).',
        },
      },
    };
    mockApi({ [W(5)]: backtest });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    await screen.findByRole('heading', { name: 'Runs this week' });
    const sb = card('Player scoreboard: vs the baseline');
    expect(sb).toHaveTextContent("Week 3's walk-forward rows. MAE improvement");
    expect(sb).not.toHaveTextContent('no live week has been graded yet');
    expect(sb).toHaveTextContent('Week 3 has walk-forward rows only (before the live player model).');
  });

  it('keeps links working when W&B leaves project_url null', async () => {
    mockApi({ [W(5)]: WANDB_W5_NO_PROJECT });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    await screen.findByRole('list', { name: 'W&B runs this week' });
    expect(within(card('Runs this week')).getByRole('link', { name: 'project ↗' })).toHaveAttribute('href', PROJECT_URL);
    expect(within(card('Season dashboard')).getByRole('link', { name: 'hkf2hana' })).toHaveAttribute('href', `${PROJECT_URL}/runs/hkf2hana`);
  });

  it('scrolls a long list of re-runs', async () => {
    const many: MlopsWandbResponse = {
      ...WANDB_W5,
      runs: [
        ...WANDB_W5.runs,
        ...Array.from({ length: 8 }, (_, i) => ({ ...WANDB_W5.runs[2], id: `old${i}`, url: `${PROJECT_URL}/runs/old${i}`, current: false })),
      ],
    };
    mockApi({ [W(5)]: many });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    const toggle = await screen.findByRole('button', { name: 'Show 9 earlier runs (re-runs)' });
    const list = screen.getByRole('list', { name: 'W&B runs this week' });
    expect(list).not.toHaveClass('ml-scroll');
    fireEvent.click(toggle);
    expect(list).toHaveClass('ml-scroll');
    expect(within(list).getAllByRole('listitem')).toHaveLength(17);
  });

  it('draws projections per stat, graph build stages and the digest words with budgets', async () => {
    mockApi({ [W(5)]: WANDB_W5 });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    await screen.findByRole('heading', { name: 'Runs this week' });

    const fit = card('Player fit: projections per stat');
    const stats = within(within(fit).getByRole('list', { name: 'Projections per stat' })).getAllByRole('listitem');
    expect(stats[0]).toHaveTextContent('receiving yards · WR260');
    expect(stats[stats.length - 1]).toHaveTextContent('passing yards · QB32');
    expect(fit).toHaveTextContent('662 projections across 4 stats.');

    const graph = card('Graph build: time by stage');
    const stages = within(within(graph).getByRole('list', { name: 'Graph build seconds by stage' })).getAllByRole('listitem');
    expect(stages[0]).toHaveTextContent('wipe122 s');
    expect(stages[stages.length - 1]).toHaveTextContent('inputs0.2 s');
    expect(graph).toHaveTextContent('17,837 nodes, 596,237 relationships, 0 count mismatches, 200 s in all.');

    const digest = card('Digest: words per section');
    const words = within(within(digest).getByRole('list', { name: 'Words per section' })).getAllByRole('listitem');
    expect(words[0]).toHaveTextContent('players to watch196');
    fireEvent.focus(words[0]);
    expect(within(words[0]).getByRole('tooltip')).toHaveTextContent('budget 180 (over by 16)');
    expect(digest).toHaveTextContent('Check issues:');
    const chips = [...digest.querySelectorAll('.ml-chips .chip')].map((c) => c.textContent);
    expect(chips).toEqual(['✓number_provenance 0', '✓entity_binding 0', '✓banned_language 0']);
  });

  it('marks check issues by level and draws the pipeline card with stale sources and drift', async () => {
    const issues: MlopsWandbResponse = {
      ...WANDB_W5,
      cards: {
        ...WANDB_W5.cards,
        digest: { ...WANDB_W5.cards.digest, checks: [{ name: 'number_provenance', issues: 2, level: 'fail' }, { name: 'hedging', issues: 1, level: 'warn' }] },
        pipeline: { ...WANDB_W5.cards.pipeline, stale_sources: 2 },
      },
    };
    mockApi({ [W(5)]: issues });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    await screen.findByRole('heading', { name: 'Runs this week' });
    const digest = card('Digest: words per section');
    expect(within(digest).getByText('number_provenance 2').closest('.chip')).toHaveClass('err');
    expect(within(digest).getByText('hedging 1').closest('.chip')).toHaveClass('warn');

    const pipe = card('Pipeline run: time per step');
    expect(pipe).toHaveTextContent('2 stale sources');
    const steps = within(within(pipe).getByRole('list', { name: 'Pipeline seconds per step' })).getAllByRole('listitem');
    expect(steps).toHaveLength(9);
    expect(steps[7]).toHaveTextContent('digest533 s');
    expect(pipe).toHaveTextContent('Drift:');
    const pair = within(pipe).getByText('player_vs_baseline').closest('.chip-pair') as HTMLElement;
    expect(pair).toHaveTextContent('insufficient data');
    expect((within(pipe).getByText('data_freshness').closest('.chip-pair') as HTMLElement)).toHaveTextContent('ok');
  });

  it('gives the pipeline card a designed empty state with its note for a week before run records', async () => {
    mockApi({ [W(4)]: WANDB_W4 });
    renderApp(<MlopsTab {...W4} />);
    await openSection('W&B runs');
    await screen.findByRole('heading', { name: 'Runs this week' });
    const pipe = card('Pipeline run: time per step');
    expect(pipe).toHaveTextContent('Week 4 ran before the pipeline run existed (P07).');
    expect(pipe.querySelector('.empty')).not.toBeNull();
    expect(within(pipe).queryByRole('list')).toBeNull();
    expect(within(pipe).queryByRole('link')).toBeNull(); // no run id, no link
    expect(pipe).toHaveTextContent('Drawn from run_summary.json');
  });

  it('shows the banner and local data when W&B is unavailable, with links from the steps\' logs', async () => {
    mockApi({ [W(5)]: WANDB_W5_DOWN });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    const banner = await findBanner(container);
    expect(banner).toHaveTextContent("W&B isn't available");
    expect(banner).toHaveTextContent("WANDB_API_KEY isn't set");
    expect(within(banner).getByRole('button', { name: 'Try again' })).toBeEnabled();

    const runs = screen.getByRole('list', { name: "W&B run links from the steps' logs" });
    expect(within(runs).getAllByRole('listitem')).toHaveLength(3);
    expect(within(runs).getByRole('link', { name: '1jmmh8g9 ↗' })).toHaveAttribute('href', `${PROJECT_URL}/runs/1jmmh8g9`);
    expect(screen.getByText(/these are the run links the steps wrote in their own logs/)).toBeInTheDocument();
    expect(within(card('Runs this week')).queryByRole('button', { name: 'Refresh' })).toBeNull();
    expect(within(card('Runs this week')).getByRole('link', { name: 'project ↗' })).toHaveAttribute('href', PROJECT_URL); // implied by the run links
    expect(within(card('Season dashboard')).queryByRole('link', { name: 'report ↗' })).toBeNull(); // no report URL without W&B
    expect(card('Season dashboard')).toHaveTextContent("The dashboard run ids come from W&B, which isn't available right now.");

    // everything local still draws
    expect(card('Game fit: model only vs market').querySelectorAll('.db-row:not(.db-axis)')).toHaveLength(6);
    expect(card('Graph build: time by stage')).toHaveTextContent('17,837 nodes');
    expect(within(card('Game fit: model only vs market')).getByRole('link', { name: /1jmmh8g9/ })).toBeInTheDocument(); // the step's own link
    expectClean(container);
  });

  it('asks the server to skip its cache when "Try again" is pressed, then shows W&B\'s answer', async () => {
    const fetchMock = mockApi({ [W(5)]: WANDB_W5_DOWN, [`${W(5)}?refresh=1`]: WANDB_W5 });
    renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    await screen.findByRole('list', { name: 'W&B runs this week' });
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContain(`${W(5)}?refresh=1`);
    expect(screen.queryByText(/W&B isn't available/)).toBeNull();
  });

  it('says it is showing older data when W&B can\'t be reached but the cache has an answer', async () => {
    mockApi({ [W(5)]: { ...WANDB_W5, wandb: WANDB_STALE } });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    const banner = await findBanner(container);
    expect(banner).toHaveTextContent("W&B can't be reached right now");
    expect(banner).toHaveTextContent('showing what it said at');
    expect(banner).toHaveTextContent("W&B didn't answer within 10 s");
    expect(screen.getByRole('list', { name: 'W&B runs this week' })).toBeInTheDocument(); // the cached list is still there
  });

  it('does not turn a non-http link from a response into a link', async () => {
    const hostile: MlopsWandbResponse = {
      ...WANDB_W5,
      runs: WANDB_W5.runs.map((r, i) => (i === 0 ? { ...r, url: 'javascript:alert(1)' } : r)),
    };
    mockApi({ [W(5)]: hostile });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await openSection('W&B runs');
    await screen.findByRole('list', { name: 'W&B runs this week' });
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
    const shown = screen.getAllByText('hkf2hana ↗');
    expect(shown.length).toBeGreaterThan(0);
    expect(shown.every((n) => n.tagName !== 'A')).toBe(true); // the id is still shown, as text
  });
});

describe('MlopsTab → Artifacts', () => {
  it('shows production tiles, the lineage and its note', async () => {
    mockApi({ [A(5)]: ARTIFACTS_W5 });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    await screen.findByText('Versions in all');
    const tiles = [...container.querySelectorAll('.tiles .tile')].map((t) => t.textContent);
    expect(tiles).toEqual([
      'game-model · productionv52026-w05 · 1jmmh8g9',
      'player-model · productionv22026-w05 · zjwbooy5',
      'team-model · productionv02026-w05 · ae8potfn',
      'Versions in all23across 5 live artifacts',
    ]);
    const firstTile = container.querySelector('.tiles .tile') as HTMLElement;
    expect(within(firstTile).getByRole('link', { name: '1jmmh8g9 in W&B' })).toHaveAttribute('href', `${PROJECT_URL}/runs/1jmmh8g9`);

    const lineage = card('What the week-5 digest was built from');
    const items = within(within(lineage).getByRole('list', { name: 'Lineage' })).getAllByRole('listitem');
    expect(items.map((i) => i.textContent)).toEqual(['modelgame-model:v5', 'graphgraph-results:v2', 'modelplayer-model:v2', 'modelteam-model:v0', 'publisheddigest:v9']);
    expect(items[4]).toHaveClass('pub');
    expect(items[0]).not.toHaveClass('pub');
    expect(lineage.querySelectorAll('.arr')).toHaveLength(4);
    expect(lineage).toHaveTextContent("From the week's pipeline run (i8fhvizy): the artifacts it used in W&B.");
    expect(within(lineage).getAllByText(/i8fhvizy/)).toHaveLength(1); // the id in the note is the link
    expect(within(lineage).getByRole('link', { name: 'i8fhvizy' })).toHaveAttribute('href', `${PROJECT_URL}/runs/i8fhvizy`);
    expect(lineage).toHaveTextContent("lineage · from the run's used artifacts");
  });

  it('lists versions with aliases, who logged them and their size', async () => {
    mockApi({ [A(5)]: ARTIFACTS_W5 });
    renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    await screen.findByText('Versions in all');
    const game = card('game-model');
    expect(game).toHaveTextContent('model weekly game fit');
    expect(within(game).getByRole('link', { name: 'W&B ↗' })).toHaveAttribute('href', `${PROJECT_URL}/artifacts/model/game-model`);
    const rows = [...game.querySelectorAll('tbody tr')];
    expect(rows).toHaveLength(6);
    const v5 = rows[0];
    expect(v5).toHaveTextContent('v5');
    expect(v5).toHaveTextContent('Oct 6, 4:40 PM'); // 20:40Z shown in ET
    expect(within(v5 as HTMLElement).getByText('production')).toHaveClass('run');
    expect(within(v5 as HTMLElement).getByText('2026-w05')).toHaveClass('flat');
    expect(within(v5 as HTMLElement).getByText('latest')).toHaveClass('ghost');
    expect(within(v5 as HTMLElement).getByRole('link', { name: '1jmmh8g9 in W&B' })).toHaveAttribute('href', `${PROJECT_URL}/runs/1jmmh8g9`);
    expect(v5).toHaveTextContent('8 KB');
    expect(rows[1]).toHaveTextContent('2026-w04');
    expect(within(rows[1] as HTMLElement).queryByText('production')).toBeNull();

    expect(card('player-model')).toHaveTextContent('11.4 MB');
    // W&B returns [week alias, latest, production]: production is shown first, latest last
    const pm = card('player-model').querySelector('tbody tr') as HTMLElement;
    expect([...pm.querySelectorAll('.chip')].map((c) => c.textContent)).toEqual(['production', '2026-w05', 'latest']);
    expect(card('team-model')).toHaveTextContent('641 KB');
  });

  it('shows the newest six versions and expands the rest on demand', async () => {
    mockApi({ [A(5)]: ARTIFACTS_W5 });
    renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    await screen.findByText('Versions in all');
    const digest = card('digest');
    expect(digest.querySelectorAll('tbody tr')).toHaveLength(6);
    expect(digest.querySelector('tbody tr')).toHaveTextContent('v9');
    const more = within(digest).getByRole('button', { name: '4 older versions' });
    fireEvent.click(more);
    expect(digest.querySelectorAll('tbody tr')).toHaveLength(10);
    expect(within(digest).getByRole('button', { name: 'Show the newest 6' })).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(within(digest).getByRole('button', { name: 'Show the newest 6' }));
    expect(digest.querySelectorAll('tbody tr')).toHaveLength(6);
    expect(within(card('player-model')).queryByRole('button')).toBeNull(); // 3 versions: nothing to expand
  });

  it('says "No versions yet" with when the first one comes', async () => {
    mockApi({ [A(5)]: ARTIFACTS_W5 });
    renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    await screen.findByText('Versions in all');
    const injury = card('injury-update');
    expect(injury).toHaveTextContent('No versions yet. The first one comes from the first Saturday injury update.');
    expect(injury.querySelector('table')).toBeNull();
  });

  it('shows the banner and what the run recorded when W&B is unavailable', async () => {
    mockApi({ [A(5)]: ARTIFACTS_W5_DOWN });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    const banner = await findBanner(container);
    expect(banner).toHaveTextContent("W&B isn't available");
    const tiles = [...container.querySelectorAll('.tiles .tile')].map((t) => t.textContent);
    expect(tiles[0]).toBe('game-model · production—needs W&B');
    expect(tiles[3]).toBe('Versions in all—');
    const local = card("Recorded by this week's run");
    expect(local).toHaveTextContent('game-model');
    expect(local).toHaveTextContent('game-model-v0:2026-w05');
    expect(local).toHaveTextContent('team-model-v1:2026-w05');
    expect(container.querySelector('table')).toBeNull(); // no version tables without W&B
    expect(screen.getByText("The lineage lives in W&B, which isn't available right now.")).toBeInTheDocument();
    expectClean(container);
  });

  it('names the models the run recorded locally by their collection names', async () => {
    mockApi({ [A(5)]: ARTIFACTS_W5_DOWN });
    renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    const local = (await screen.findByRole('heading', { name: "Recorded by this week's run" })).closest('.card') as HTMLElement;
    expect([...local.querySelectorAll('dt')].map((d) => d.textContent)).toEqual(['game-model', 'player-model', 'team-model']);
  });

  it('keeps run links working when W&B leaves project_url null', async () => {
    mockApi({ [A(5)]: ARTIFACTS_W5_NO_PROJECT });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    await screen.findByText('Versions in all');
    const tile = container.querySelector('.tiles .tile') as HTMLElement;
    expect(within(tile).getByRole('link', { name: '1jmmh8g9 in W&B' })).toHaveAttribute('href', `${PROJECT_URL}/runs/1jmmh8g9`);
    // no note to hang the pipeline run on: it is added to the lineage as a link
    expect(within(card('What the week-5 digest was built from')).getByRole('link', { name: 'i8fhvizy' })).toHaveAttribute('href', `${PROJECT_URL}/runs/i8fhvizy`);
  });

  it('says so when W&B is unavailable and the run recorded nothing locally', async () => {
    mockApi({ [A(5)]: { ...ARTIFACTS_W5_DOWN, local_models: null, wandb: WANDB_DOWN } });
    renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    expect(await screen.findByText(/The run didn't record any model versions/)).toBeInTheDocument();
  });

  it('ends with the read-only note and has no stray values', async () => {
    mockApi({ [A(5)]: ARTIFACTS_W5 });
    const { container } = renderApp(<MlopsTab {...W5} />);
    await openSection('Artifacts');
    await screen.findByText('Versions in all');
    expect(screen.getByText(/Read from the W&B API on the server \(read-only, cached\)\. The W&B key never reaches the browser\./)).toBeInTheDocument();
    expectClean(container);
  });
});
