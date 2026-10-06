import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { mockApi, renderApp } from '../../test/utils';
import { DigestTab } from './DigestTab';
import { GamesTab } from './GamesTab';
import { GraphTab } from './GraphTab';
import { PlayersTab } from './PlayersTab';
import { ResultsTab } from './ResultsTab';
import { WeekEmpty } from './WeekEmpty';
import {
  DIGEST,
  GAMES,
  GRAPH,
  GRAPH_NONE,
  NOT_GRADED,
  PLAYERS,
  PLAYERS_ALL,
  RESULTS,
  SLATE,
  TEAM_INFO,
} from './weekFixtures';

const W4 = { season: 2026, week: 4, isCurrent: false, lastPublishedWeek: 4 };
const W5 = { season: 2026, week: 5, isCurrent: true, lastPublishedWeek: 4 };

function api(paths: Record<string, unknown>) {
  return mockApi({ '/api/team-info': TEAM_INFO, ...paths });
}

describe('DigestTab', () => {
  it('renders the digest with tables in scroll boxes, safe links and no raw HTML', async () => {
    api({ '/api/weeks/2026/4/digest': DIGEST });
    const { container } = renderApp(<DigestTab {...W4} />);
    expect(await screen.findByRole('heading', { name: 'Week 4 digest' })).toBeInTheDocument();
    const article = container.querySelector('article.digest') as HTMLElement;
    const table = article.querySelector('.tablewrap > table');
    expect(table).not.toBeNull();
    expect(table).toHaveTextContent('Bisons at Anchors');
    expect(article.querySelector('strong')).toHaveTextContent('Anchors');
    const link = within(article).getByRole('link', { name: 'the schedule' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(container.querySelector('script')).toBeNull();
  });

  it('shows the checks, words per section, the writer and the files in the rail', async () => {
    api({ '/api/weeks/2026/4/digest': DIGEST });
    renderApp(<DigestTab {...W4} />);
    await screen.findByRole('heading', { name: 'Checks' });
    expect(screen.getByText('passed after one regeneration')).toBeInTheDocument();
    expect(screen.getByText('number_provenance')).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: 'passed' })).toHaveLength(2);
    const words = screen.getByRole('list', { name: 'Words per section' });
    expect(words).toHaveTextContent('players to watch');
    expect(words).toHaveTextContent('310');
    expect(screen.getByText('openrouter → DeepInfra')).toBeInTheDocument();
    expect(screen.getByText('abc123def456')).toBeInTheDocument();
    expect(screen.getByText('first draft')).toBeInTheDocument();
    expect(screen.getByText('rewrite')).toBeInTheDocument();
    expect(screen.getByText('24m 40s')).toBeInTheDocument();
    expect(screen.getByText('12,345')).toBeInTheDocument();
    expect(screen.getByText('$0.0224')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'digest run ↗' })).toHaveAttribute('href', DIGEST.wandb_url);
    expect(screen.getByText(/runs\/2026\/week04\/payload\.json · missing/)).toHaveClass('muted');
    expect(screen.getByText('no addendum')).toBeInTheDocument();
    expect(screen.queryByText(/week04-injury-update/)).toBeNull(); // the missing Saturday file says "no addendum" once
    expect(screen.getByText('openrouter', { selector: 'dd' })).toBeInTheDocument(); // writers deduplicated
  });

  it('never shows fields beyond the contract (no prompt or raw LLM text)', async () => {
    const leaky = {
      ...DIGEST,
      raw_text: 'RAW-LLM-OUTPUT',
      writer: { ...DIGEST.writer, system_prompt: 'SYSTEM-PROMPT-TEXT', calls: DIGEST.writer!.calls.map((c) => ({ ...c, text: 'CALL-TEXT' })) },
    };
    api({ '/api/weeks/2026/4/digest': leaky });
    const { container } = renderApp(<DigestTab {...W4} />);
    await screen.findByRole('heading', { name: 'Week 4 digest' });
    expect(container).not.toHaveTextContent('RAW-LLM-OUTPUT');
    expect(container).not.toHaveTextContent('SYSTEM-PROMPT-TEXT');
    expect(container).not.toHaveTextContent('CALL-TEXT');
  });

  it("flags the run's unpublished draft and renders Saturday's addendum", async () => {
    api({
      '/api/weeks/2026/4/digest': {
        ...DIGEST,
        status: 'unpublished',
        addendum: { markdown: '## Injury update\n\nThe Anchors lose a starter.', material: true, at: '2026-10-10T14:00:00Z' },
      },
    });
    renderApp(<DigestTab {...W4} />);
    expect(await screen.findByText(/Not published: the run's draft/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Injury update' })).toBeInTheDocument();
    expect(screen.getByText(/addendum below · Sat, Oct 10, 10:00 AM ET/)).toBeInTheDocument();
  });

  it('shows an error notice when the API fails', async () => {
    api({});
    renderApp(<DigestTab {...W4} />);
    expect(await screen.findByText(/Couldn't load this tab/)).toBeInTheDocument();
  });
});

describe('GamesTab', () => {
  it('renders the tiles, the market gaps and a card per game with the final', async () => {
    api({ '/api/weeks/2026/4/games': GAMES });
    renderApp(<GamesTab {...W4} />);
    expect(await screen.findByText('Games predicted')).toBeInTheDocument();
    expect(screen.getByText('1 before kickoff + 1 after kickoff (not graded)')).toBeInTheDocument();
    expect(screen.getByText('2/2')).toBeInTheDocument();
    expect(screen.getByText('game-model-v9')).toBeInTheDocument();
    expect(screen.getByText('trained through 2026-w03')).toBeInTheDocument();

    const gapCard = screen.getByRole('heading', { name: 'Where the model disagrees with the market' }).closest('.card') as HTMLElement;
    await waitFor(() => expect(gapCard).toHaveTextContent('Comets'));
    expect(gapCard).toHaveTextContent('Model only gives Comets 35%, market 50% (−15 pts)');

    const first = await screen.findByRole('group', { name: 'Bisons at Anchors' });
    expect(first).toHaveTextContent('predicted after kickoff');
    expect(first).toHaveTextContent('final · not graded'); // Sol review: no hit / miss for it
    expect(first).not.toHaveTextContent('hit · final');
    expect(first.querySelector('.pts b')).toHaveTextContent('24');
    expect(first).toHaveTextContent('market AAA −3.0 · total 44.5');

    const second = screen.getByRole('group', { name: 'Dragons at Comets' });
    expect(second).toHaveTextContent('market DDD −2.5');
    expect(second).toHaveTextContent("result after week 5's ingest");
    expect(within(second).getByRole('img', { name: /QB change: New Starter starts/ })).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 games are final so far/)).toBeInTheDocument();
  });

  it('marks a graded final as a hit or a miss', async () => {
    const graded = { ...GAMES.games[0], predicted_after_kickoff: false, hit: true };
    api({ '/api/weeks/2026/4/games': { ...GAMES, games: [graded, GAMES.games[1]] } });
    renderApp(<GamesTab {...W4} />);
    const first = await screen.findByRole('group', { name: 'Bisons at Anchors' });
    expect(first).toHaveTextContent('✓hit · final');
    expect(screen.getByText('all before kickoff')).toBeInTheDocument();
  });

  it('shows a tooltip on each dot of the strip, on focus as well as hover', async () => {
    api({ '/api/weeks/2026/4/games': GAMES });
    renderApp(<GamesTab {...W4} />);
    const strip = await screen.findByRole('group', { name: 'Anchors win chance by source' });
    const elo = within(strip).getByRole('img', { name: /^Elo:/ });
    fireEvent.focus(elo);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Anchors 58% · Bisons 42%');
    fireEvent.blur(elo);
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.pointerMove(within(strip).getByRole('img', { name: /^Model only:/ }), { clientX: 10, clientY: 10 });
    expect(screen.getByRole('tooltip')).toHaveTextContent('Model only');
    expect(within(strip).getAllByRole('img')).toHaveLength(4);
  });

  it("renders the current week's slate before its run", async () => {
    api({ '/api/weeks/2026/5/games': SLATE });
    renderApp(<GamesTab {...W5} />);
    expect(await screen.findByRole('heading', { name: 'Week 5 slate' })).toBeInTheDocument();
    expect(screen.getByText(/games from the schedule \(snapshot 2026-10-05\) · byes: BBB, CCC/)).toBeInTheDocument();
    expect(screen.getAllByText('after the run')).toHaveLength(2);
    const row = screen.getByText('Dragon Dome').closest('tr') as HTMLElement;
    expect(row).toHaveTextContent('Sun 1:00 PM');
    expect(row).toHaveTextContent('AAA −6.5');
    expect(row).toHaveTextContent('41.5');
  });
});

describe('PlayersTab', () => {
  it('renders the watch list by side, tough spots and the projections', async () => {
    api({ '/api/weeks/2026/4/players': PLAYERS });
    renderApp(<PlayersTab {...W4} />);
    expect(await screen.findByRole('heading', { name: 'Players to watch' })).toBeInTheDocument();
    expect(screen.getByText(/The 2 model picks from the digest · 1,234 projections in all/)).toBeInTheDocument();
    const offense = screen.getByRole('region', { name: 'Offense' });
    expect(offense).toHaveTextContent('Wide Receiverson');
    expect(offense).toHaveTextContent('+21 vs his baseline 60.0 · faces a weak pass defense');
    const defense = screen.getByRole('region', { name: 'Defense' });
    expect(defense).toHaveTextContent('Edge Rusherman');
    await waitFor(() => expect(defense).toHaveTextContent('at Dragons'));
    expect(defense).toHaveTextContent('Questionable');
    expect(defense).toHaveTextContent('no single factor stands out');
    const tough = screen.getByRole('heading', { name: 'Tough spots' }).closest('.card') as HTMLElement;
    expect(tough).toHaveTextContent('40 receiving yards projected · baseline 81 −41');

    const range = within(offense).getByRole('img', { name: /^Wide Receiverson/ });
    fireEvent.pointerMove(range, { clientX: 5, clientY: 5 });
    expect(screen.getByRole('tooltip')).toHaveTextContent('range 30–110 · projection 88');
    expect(screen.getByRole('tooltip')).toHaveTextContent('baseline 60');
    expect(screen.getByText(/1,234 projections: 23 stats, 2 team totals/)).toBeInTheDocument();
  });

  it('filters the table by group, sorted by vs baseline, with the chance column', async () => {
    api({ '/api/weeks/2026/4/players': PLAYERS });
    renderApp(<PlayersTab {...W4} />);
    const card = (await screen.findByRole('heading', { name: 'All projections' })).closest('.card') as HTMLElement;
    const body = () => card.querySelectorAll('tbody tr');
    expect(body()).toHaveLength(3);
    expect(body()[0]).toHaveTextContent('Wide Receiverson');
    expect(body()[2]).toHaveTextContent('Slow Weekson');
    fireEvent.click(within(card).getByRole('button', { name: 'EDGE/DL' }));
    expect(within(card).getByRole('button', { name: 'EDGE/DL' })).toHaveAttribute('aria-pressed', 'true');
    expect(body()).toHaveLength(1);
    expect(body()[0]).toHaveTextContent('Edge Rusherman');
    expect(body()[0]).toHaveTextContent('42%');
    expect(within(body()[0] as HTMLElement).getByText('Questionable')).toHaveClass('chip');
  });

  it('switches to every stat through ?stats=all', async () => {
    const fetchMock = api({ '/api/weeks/2026/4/players': PLAYERS, '/api/weeks/2026/4/players?stats=all': PLAYERS_ALL });
    renderApp(<PlayersTab {...W4} />);
    const card = (await screen.findByRole('heading', { name: 'All projections' })).closest('.card') as HTMLElement;
    expect(within(card).queryByText('receptions')).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: 'Show every stat' }));
    expect(await within(card).findByText('receptions')).toBeInTheDocument();
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContain('/api/weeks/2026/4/players?stats=all');
    expect(within(card).getByRole('button', { name: 'Show every stat' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows the first 40 rows and a button for the rest', async () => {
    const many = Array.from({ length: 45 }, (_, i) => ({ ...PLAYERS.rows[0], player: `Player ${i}`, player_id: `x${i}`, vs_baseline: i }));
    api({ '/api/weeks/2026/4/players': { ...PLAYERS, rows: many } });
    renderApp(<PlayersTab {...W4} />);
    const card = (await screen.findByRole('heading', { name: 'All projections' })).closest('.card') as HTMLElement;
    expect(card.querySelectorAll('tbody tr')).toHaveLength(40);
    fireEvent.click(within(card).getByRole('button', { name: 'Show all 45' }));
    expect(card.querySelectorAll('tbody tr')).toHaveLength(45);
  });
});

describe('ResultsTab', () => {
  it('renders a graded week: tiles, game by game, groups and the watch list', async () => {
    api({ '/api/weeks/2026/4/results': RESULTS });
    renderApp(<ResultsTab {...W4} />);
    expect(await screen.findByText('Picks right')).toBeInTheDocument();
    expect(screen.getByText('1/1')).toBeInTheDocument();
    expect(screen.getByText('100% of games · 1 not graded')).toBeInTheDocument();
    expect(screen.getByText('0.160')).toBeInTheDocument();
    expect(screen.getByText('model better by 0.016')).toBeInTheDocument();
    expect(screen.getByText('0.185')).toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
    expect(screen.getByText(/recomputed numbers differ from the report card/)).toBeInTheDocument();

    const games = screen.getByRole('heading', { name: 'Game by game' }).closest('.card') as HTMLElement;
    const [hit, kept] = Array.from(games.querySelectorAll('tbody tr')) as HTMLElement[];
    expect(hit).toHaveTextContent('23–17');
    expect(hit).toHaveTextContent('✓hit');
    expect(hit).toHaveTextContent('0.203');
    expect(hit).toHaveTextContent('−4.1');
    expect(kept).toHaveTextContent('not graded');
    expect(kept).toHaveTextContent('predicted after kickoff: not graded');

    const groups = screen.getByRole('list', { name: 'Player model vs baseline by group' });
    expect(groups).toHaveTextContent('+6.2%');
    expect(groups).toHaveTextContent('−1.5%');

    expect(screen.getByRole('heading', { name: 'Player model vs baseline' })).toBeInTheDocument();
    const watch = screen.getByRole('heading', { name: 'Watch list' }).closest('.card') as HTMLElement;
    expect(watch.parentElement).toHaveClass('wk-stack'); // full width, not half of a grid
    const offense = within(watch).getByRole('rowgroup', { name: 'Offense' });
    expect(offense).toHaveTextContent('Offense 1/1 beat their baseline');
    const defense = within(watch).getByRole('rowgroup', { name: 'Defense' });
    expect(defense).toHaveTextContent('0/0 beat their baseline');
    const played = offense.querySelector('tr:not(.grp)') as HTMLElement;
    const dnp = defense.querySelector('tr:not(.grp)') as HTMLElement;
    expect(played).toHaveTextContent('104');
    expect(played).toHaveTextContent('✓inside');
    expect(played).toHaveTextContent('✓beat');
    expect(played.querySelector('.ract')).not.toBeNull();
    expect(dnp).toHaveClass('dnp');
    expect(dnp).toHaveTextContent('did not play');
  });

  it('says so when a graded week has no scoreboard rows (backtests)', async () => {
    api({ '/api/weeks/2025/13/results': { ...RESULTS, season: 2025, week: 13, consistent: true, players: [] } });
    renderApp(<ResultsTab season={2025} week={13} isCurrent={false} lastPublishedWeek={null} />);
    expect(await screen.findByText(/No scoreboard rows for this week/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Player model vs baseline' })).toBeNull();
    const watch = screen.getByRole('heading', { name: 'Watch list' }).closest('.card') as HTMLElement;
    expect(within(watch).getAllByRole('rowgroup', { name: /Offense|Defense/ })).toHaveLength(2);
    expect(screen.queryByRole('list', { name: 'Player model vs baseline by group' })).toBeNull();
    expect(screen.queryByText(/recomputed numbers differ/)).toBeNull();
  });

  it("explains when a week isn't graded yet", async () => {
    api({ '/api/weeks/2026/5/results': NOT_GRADED });
    renderApp(<ResultsTab {...W5} />);
    expect(await screen.findByRole('heading', { name: "Week 5's results are graded by week 6's run" })).toBeInTheDocument();
    expect(screen.getByText(/On Tue Oct 13 the run grades this week/)).toBeInTheDocument();
    expect(screen.getByText("Week 6's run hasn't happened yet.")).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to the pipeline' })).toHaveAttribute('href', '/week/2026/6/pipeline');
    expect(screen.getByRole('link', { name: "See week 4's Results" })).toHaveAttribute('href', '/week/2026/4/results');
  });
});

describe('GraphTab', () => {
  it('renders the tiles, the insights used, the query rows and what was not used', async () => {
    api({ '/api/weeks/2026/4/graph': GRAPH });
    renderApp(<GraphTab {...W4} />);
    expect(await screen.findByText('1,500')).toBeInTheDocument();
    expect(screen.getByText('3 labels')).toBeInTheDocument();
    expect(screen.getByText('4,200')).toBeInTheDocument();
    expect(screen.getByText('wipe 4.2 s · load 61 s · queries 3.4 s')).toBeInTheDocument();
    expect(screen.getByText('3 skipped: game already started')).toBeInTheDocument();

    const used = screen.getByRole('heading', { name: 'Used in the digest' }).closest('.card') as HTMLElement;
    expect(used.querySelectorAll('.insight')).toHaveLength(2);
    expect(used).toHaveTextContent('Matchup / risk');
    expect(used).toHaveTextContent('Non-obvious');
    expect(used).toHaveTextContent('q7_former_teammates');
    expect(used).toHaveTextContent('0.81');
    expect(used).toHaveTextContent('high confidence');
    expect(used).toHaveTextContent('A small sample: few games together.');
    expect(within(used).getByRole('link', { name: 'Neo4j Browser ↗' })).toHaveAttribute('href', 'http://localhost:7474/browser/');

    const queries = screen.getByRole('list', { name: 'Rows per query' });
    expect(queries).toHaveTextContent('Q2 injury ripple');
    expect(screen.getByText(/: timed out/)).toBeInTheDocument();

    const notUsed = screen.getByRole('heading', { name: 'Found but not used' }).closest('.card') as HTMLElement;
    expect(notUsed).toHaveTextContent('game already started');
    expect(notUsed).toHaveTextContent('used in a recent digest');
    expect(notUsed).toHaveTextContent('red zone mismatch');
    expect(screen.getByRole('list', { name: 'Nodes by label' })).toHaveTextContent('1,200');
    expect(screen.getByText(/Graph Data Science: ok · version 2.13.2/)).toHaveTextContent('pagerank ok (3.2 s)');
  });

  it('says plainly when a past week has no graph', async () => {
    api({ '/api/weeks/2026/3/graph': { ...GRAPH_NONE, week: 3 } });
    renderApp(<GraphTab {...W4} week={3} />);
    expect(await screen.findByRole('heading', { name: 'No graph for this week' })).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
  });
});

describe('WeekEmpty', () => {
  it("links the current week's empty tab to the pipeline and the last published week", async () => {
    api({ '/api/weeks/2026/5/digest': { ...DIGEST, week: 5, status: 'none', markdown: null, checks: null, writer: null } });
    renderApp(<DigestTab {...W5} />);
    expect(await screen.findByRole('heading', { name: 'The digest appears here when the run finishes' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to the pipeline' })).toHaveAttribute('href', '/week/2026/5/pipeline');
    expect(screen.getByRole('link', { name: "See week 4's Digest" })).toHaveAttribute('href', '/week/2026/4/digest');
  });

  it('omits the last-week link when nothing is published yet', () => {
    renderApp(<WeekEmpty tab="players" season={2026} week={5} isCurrent lastPublishedWeek={null} />);
    expect(screen.getByRole('heading', { name: 'Player projections arrive with the player step' })).toBeInTheDocument();
    expect(screen.getAllByRole('link')).toHaveLength(1);
  });

  it('renders the graph empty state for the current week', async () => {
    api({ '/api/weeks/2026/5/graph': { ...GRAPH_NONE, week: 5 } });
    renderApp(<GraphTab {...W5} />);
    expect(await screen.findByRole('heading', { name: 'The graph is rebuilt during the run' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('link', { name: "See week 4's Graph" })).toBeInTheDocument());
  });

  it('a past week without data says so', () => {
    renderApp(<WeekEmpty tab="digest" season={2026} week={2} isCurrent={false} lastPublishedWeek={4} />);
    expect(screen.getByRole('heading', { name: 'No digest for week 2' })).toBeInTheDocument();
  });
});

describe('ResultsTab after the Sol review', () => {
  it('shows a played pick with no range or baseline grade as ungraded, not as a miss', async () => {
    if (RESULTS.status !== 'graded') throw new Error('fixture');
    const heuristic = {
      ...RESULTS.watch[0],
      player: 'Heuristic Pick',
      player_id: 'h1',
      p10: null,
      p90: null,
      inside: null,
      hit: null,
    };
    api({ '/api/weeks/2026/4/results': { ...RESULTS, watch: [heuristic] } });
    renderApp(<ResultsTab {...W4} />);
    const row = (await screen.findByText('Heuristic Pick')).closest('tr') as HTMLElement;
    expect(row).not.toHaveTextContent('outside');
    expect(row).not.toHaveTextContent('under');
    expect(within(row).getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });

  it("says the Super Bowl is graded after the season, with no link to a week 23", async () => {
    api({
      '/api/weeks/2026/22/results': {
        season: 2026,
        week: 22,
        status: 'not_graded',
        graded_by_week: null,
        graded_on: null,
        reason: "The Super Bowl isn't graded by a weekly run",
      },
    });
    renderApp(<ResultsTab season={2026} week={22} isCurrent={false} lastPublishedWeek={22} />);
    expect(await screen.findByText('Week 22 is graded after the season')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Go to the pipeline' })).toBeNull();
    expect(document.body.innerHTML).not.toContain('/week/2026/23');
  });
});
