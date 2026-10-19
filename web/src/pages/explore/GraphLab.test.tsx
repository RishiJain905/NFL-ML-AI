import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mockApi, renderApp, withStatus } from '../../test/utils';
import { ConnectionsPage } from './graphlab/ConnectionsPage';
import { LabPage } from './graphlab/LabPage';
import { AskParts } from './graphlab/AskHeader';
import {
  BENCHMARK,
  CHEMISTRY,
  CHEMISTRY_300,
  CX_ERRORS,
  CX_NO_CHAIN,
  CX_STRONGEST,
  CX_TEAMMATES,
  CX_TWO_STEPS_MORE,
  FAMILIES,
  FAMILY_OF_REID,
  MAP,
  NB_KELCE,
  NB_NOT_IN_MAP,
  PEOPLE_BELICHICK,
  PEOPLE_MAHOMES,
  PEOPLE_NONE,
  STATUS,
} from './graphlabFixtures';

const MAHOMES = 'player:00-0033873';
const KELCE = 'player:00-0030506';
const BRADY = 'player:00-0019596';
const RODGERS = 'player:00-0023459';
const BELICHICK = 'coach:bill-belichick';

const people = (q: string, kind?: string) =>
  `/api/ask/people?${new URLSearchParams(kind ? { q, kind } : { q }).toString()}`;
const cx = (a: string, b: string, k: number, mode = 'fewest') =>
  `/api/ask/connections?${new URLSearchParams({ a, b, k: String(k), mode }).toString()}`;
const notBuilt = (message: string) => withStatus(404, { error: { code: 'not_built', message } });

beforeEach(() => {
  // jsdom has no canvas: the map draws nothing, its lists and labels still render
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function connections(
  route = '/explore/ask/connections',
  extra: Record<string, unknown> = {},
  ui: ReactNode = <ConnectionsPage />,
) {
  const fetchMock = mockApi({
    '/api/ask/graph-lab': STATUS,
    [people('mah')]: PEOPLE_MAHOMES,
    [people('mahomes')]: PEOPLE_MAHOMES,
    [people('bel')]: PEOPLE_BELICHICK,
    [people('zzqx')]: PEOPLE_NONE,
    ...extra,
  });
  renderApp(ui, { route, path: '/explore/ask/connections' });
  return fetchMock;
}

/** The page with the browser's own Back / Forward buttons beside it, and a link to another pair. */
function WithHistory({ to }: { to?: string }) {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(-1)}>
        browser back
      </button>
      <button type="button" onClick={() => navigate(1)}>
        browser forward
      </button>
      {to ? (
        <button type="button" onClick={() => navigate(to)}>
          open another pair
        </button>
      ) : null}
      <ConnectionsPage />
    </>
  );
}
const cxStrongest = { ...CX_TEAMMATES, mode: 'strongest' as const };
const cxBradyMahomes = { ...CX_TWO_STEPS_MORE, k: 1, paths: CX_TWO_STEPS_MORE.paths.slice(0, 1) };
const calls = (f: ReturnType<typeof mockApi>) => f.mock.calls.map((c) => String(c[0]));

async function pick(label: string, typed: string, name: string) {
  const box = screen.getByRole('combobox', { name: label });
  fireEvent.change(box, { target: { value: typed } });
  const option = await screen.findByRole('option', { name: new RegExp(name) });
  fireEvent.keyDown(box, { key: 'ArrowDown' });
  fireEvent.keyDown(box, { key: 'ArrowUp' });
  expect(box).toHaveAttribute('aria-activedescendant', option.id);
  fireEvent.keyDown(box, { key: 'Enter' });
  await screen.findByRole('button', { name: `Change ${label.toLowerCase()}: clear ${name}` });
}

describe('Ask the Engine: the three parts', () => {
  it('links Ask, Connections and the Graph lab, marking the current one', () => {
    mockApi({});
    renderApp(<AskParts />, {
      route: '/explore/ask/connections',
      path: '/explore/ask/connections',
    });
    const nav = screen.getByRole('navigation', { name: 'Ask the Engine' });
    expect(within(nav).getByRole('link', { name: 'Ask' })).toHaveAttribute('href', '/explore/ask');
    expect(within(nav).getByRole('link', { name: 'Connections' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: 'Graph lab' })).toHaveAttribute(
      'href',
      '/explore/ask/lab',
    );
  });
});

describe('Connections', () => {
  it('starts empty, shows the history graph, and needs two people', async () => {
    connections();
    expect(
      await screen.findByText('history graph 2018-2025 + 2026 week 5 · up'),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Pick two people' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled();
    expect(screen.getByText(/the chain with the fewest people in it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Strongest links' }));
    expect(screen.getByText(/every link costs 1 ÷ its snaps together/)).toBeInTheDocument();
  });

  it('searches from two letters, 200 ms after the last key, and says when nobody matches', async () => {
    const f = connections();
    const box = screen.getByRole('combobox', { name: 'From' });
    fireEvent.change(box, { target: { value: 'm' } });
    expect(screen.getByText('Type one more letter')).toBeInTheDocument();
    fireEvent.change(box, { target: { value: 'zzqx' } });
    expect(await screen.findByText(/No player or coach matches “zzqx”/)).toBeInTheDocument();
    expect(calls(f).filter((u) => u.startsWith('/api/ask/people'))).toEqual([people('zzqx')]);
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('picks two people with the keyboard, connects, and puts the pair in the URL', async () => {
    const f = connections('/explore/ask/connections', {
      [cx(MAHOMES, BELICHICK, 1)]: CX_TEAMMATES,
    });
    await pick('From', 'mah', 'Patrick Mahomes');
    expect(screen.getByText('QB · KC · 2018-2026')).toBeInTheDocument();
    await pick('To', 'bel', 'Bill Belichick');
    const go = screen.getByRole('button', { name: 'Connect' });
    await waitFor(() => expect(go).toHaveFocus()); // after the second pick, focus moves on to Connect
    fireEvent.click(go);
    await waitFor(() => expect(calls(f)).toContain(cx(MAHOMES, BELICHICK, 1)));
    // the fixture's answer is Mahomes–Kelce: the card draws whatever the server sends
    const card = await screen.findByRole('article');
    expect(
      within(card).getByRole('heading', { name: 'Patrick Mahomes to Travis Kelce' }),
    ).toHaveFocus();
    expect(within(card).getByText('1 link')).toBeInTheDocument();
    expect(
      within(card).getByText(/teammates on 2018 KC, 1,164 snaps together/),
    ).toBeInTheDocument();
    expect(within(card).getByText('person links 2018-2025 + 2026 week 5')).toBeInTheDocument();
  });

  it('opens a shared link: connects at once, names the people, draws a long chain top to bottom', async () => {
    connections(`/explore/ask/connections?a=${BRADY}&b=${RODGERS}&mode=strongest`, {
      [cx(BRADY, RODGERS, 1, 'strongest')]: CX_STRONGEST,
    });
    expect(screen.getAllByText('Loading…')).toHaveLength(2);
    const card = await screen.findByRole('article');
    expect(within(card).getByText('The strongest chain')).toBeInTheDocument();
    expect(within(card).getByText('5 links')).toBeInTheDocument();
    const chain = within(card).getByRole('list', { name: 'The chain: 5 links' });
    expect(chain).toHaveAttribute('data-hops', '5');
    expect(within(chain).getAllByText('same staff')).toHaveLength(3);
    expect(within(chain).getByText('Byron Leftwich')).toBeInTheDocument();
    // the pickers now carry the names from the answer
    expect(
      screen.getByRole('button', { name: 'Change from: clear Tom Brady' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Strongest links' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('shows 3 more chains (k=4) and draws the one clicked', async () => {
    const f = connections(`/explore/ask/connections?a=${BRADY}&b=${MAHOMES}`, {
      [cx(BRADY, MAHOMES, 1)]: {
        ...CX_TWO_STEPS_MORE,
        k: 1,
        paths: CX_TWO_STEPS_MORE.paths.slice(0, 1),
      },
      [cx(BRADY, MAHOMES, 4)]: CX_TWO_STEPS_MORE,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Show 3 more chains' }));
    await waitFor(() => expect(calls(f)).toContain(cx(BRADY, MAHOMES, 4)));
    const second = await screen.findByRole('button', { name: /via Donovan Smith/ });
    expect(screen.getByRole('button', { name: /via Joe Thuney/ })).toHaveAttribute(
      'aria-current',
      'true',
    );
    fireEvent.click(second);
    expect(second).toHaveAttribute('aria-current', 'true');
    expect(screen.getByText('Chain 2 of 4')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Tom Brady to Patrick Mahomes' })).toHaveFocus();
  });

  it('swaps the two people and asks again', async () => {
    const f = connections(`/explore/ask/connections?a=${MAHOMES}&b=${KELCE}`, {
      [cx(MAHOMES, KELCE, 1)]: CX_TEAMMATES,
      [cx(KELCE, MAHOMES, 1)]: CX_TEAMMATES,
    });
    await screen.findByRole('article');
    fireEvent.click(screen.getByRole('button', { name: 'Swap the two people' }));
    await waitFor(() => expect(calls(f)).toContain(cx(KELCE, MAHOMES, 1)));
    expect(
      screen.getByRole('button', { name: 'Change from: clear Travis Kelce' }),
    ).toBeInTheDocument();
  });

  it('Back and Forward put the mode buttons and the explanation back with the chain shown', async () => {
    const f = connections(
      `/explore/ask/connections?a=${MAHOMES}&b=${KELCE}&mode=fewest`,
      {
        [cx(MAHOMES, KELCE, 1)]: CX_TEAMMATES,
        [cx(MAHOMES, KELCE, 1, 'strongest')]: cxStrongest,
      },
      <WithHistory />,
    );
    const fewest = () => screen.getByRole('button', { name: 'Fewest links' });
    const strongest = () => screen.getByRole('button', { name: 'Strongest links' });
    const why = () => document.getElementById('cxWhy')!;
    await screen.findByRole('article');
    expect(fewest()).toHaveAttribute('aria-pressed', 'true');
    expect(why()).toHaveTextContent(/^Fewest links:/);

    fireEvent.click(strongest());
    await waitFor(() => expect(calls(f)).toContain(cx(MAHOMES, KELCE, 1, 'strongest')));
    await waitFor(() => expect(screen.getByText('strongest links')).toBeInTheDocument()); // the chain's Mode chip
    expect(strongest()).toHaveAttribute('aria-pressed', 'true');
    expect(why()).toHaveTextContent(/^Strongest links:/);

    fireEvent.click(screen.getByRole('button', { name: 'browser back' }));
    await waitFor(() => expect(screen.getByText('fewest links')).toBeInTheDocument());
    expect(fewest()).toHaveAttribute('aria-pressed', 'true');
    expect(strongest()).toHaveAttribute('aria-pressed', 'false');
    expect(why()).toHaveTextContent(/^Fewest links:/);
    // the people stay, names and all
    expect(
      screen.getByRole('button', { name: 'Change from: clear Patrick Mahomes' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change to: clear Travis Kelce' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'browser forward' }));
    await waitFor(() => expect(screen.getByText('strongest links')).toBeInTheDocument());
    expect(strongest()).toHaveAttribute('aria-pressed', 'true');
    expect(why()).toHaveTextContent(/^Strongest links:/);
  });

  it('Back from another pair puts the first pair’s people back in the pickers', async () => {
    connections(
      `/explore/ask/connections?a=${MAHOMES}&b=${KELCE}`,
      {
        [cx(MAHOMES, KELCE, 1)]: CX_TEAMMATES,
        [cx(BRADY, MAHOMES, 1)]: cxBradyMahomes,
      },
      <WithHistory to={`/explore/ask/connections?a=${BRADY}&b=${MAHOMES}`} />,
    );
    const from = (name: string) =>
      screen.getByRole('button', { name: `Change from: clear ${name}` });
    const to = (name: string) => screen.getByRole('button', { name: `Change to: clear ${name}` });
    await screen.findByRole('article');
    expect(from('Patrick Mahomes')).toBeInTheDocument();
    expect(to('Travis Kelce')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'open another pair' }));
    expect(
      await screen.findByRole('heading', { name: 'Tom Brady to Patrick Mahomes' }),
    ).toBeInTheDocument();
    expect(from('Tom Brady')).toBeInTheDocument();
    expect(to('Patrick Mahomes')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'browser back' }));
    expect(
      await screen.findByRole('heading', { name: 'Patrick Mahomes to Travis Kelce' }),
    ).toBeInTheDocument();
    expect(from('Patrick Mahomes')).toBeInTheDocument();
    expect(to('Travis Kelce')).toBeInTheDocument();
  });

  it('Back after a swap puts the two people back in their first order', async () => {
    connections(
      `/explore/ask/connections?a=${MAHOMES}&b=${KELCE}`,
      {
        [cx(MAHOMES, KELCE, 1)]: CX_TEAMMATES,
        [cx(KELCE, MAHOMES, 1)]: CX_TEAMMATES,
      },
      <WithHistory />,
    );
    await screen.findByRole('article');
    fireEvent.click(screen.getByRole('button', { name: 'Swap the two people' }));
    expect(
      await screen.findByRole('button', { name: 'Change from: clear Travis Kelce' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change to: clear Patrick Mahomes' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'browser back' }));
    expect(
      await screen.findByRole('button', { name: 'Change from: clear Patrick Mahomes' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change to: clear Travis Kelce' }),
    ).toBeInTheDocument();
  });

  it('leaves a pick that isn’t asked yet alone, and keeps the people when Connect puts them in the URL', async () => {
    const f = connections(
      `/explore/ask/connections?a=${MAHOMES}&b=${KELCE}`,
      {
        [cx(MAHOMES, KELCE, 1)]: CX_TEAMMATES,
        [cx(MAHOMES, BELICHICK, 1, 'strongest')]: cxStrongest,
      },
      <WithHistory />,
    );
    await screen.findByRole('article');
    fireEvent.click(screen.getByRole('button', { name: 'Change to: clear Travis Kelce' }));
    // a re-render with the URL unchanged (the mode button) neither refills the box nor undoes the mode
    fireEvent.click(screen.getByRole('button', { name: 'Strongest links' }));
    expect(screen.getByRole('combobox', { name: 'To' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Strongest links' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(calls(f)).not.toContain(cx(MAHOMES, KELCE, 1, 'strongest'));

    await pick('To', 'bel', 'Bill Belichick');
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(calls(f)).toContain(cx(MAHOMES, BELICHICK, 1, 'strongest')));
    await waitFor(() => expect(screen.getByText('strongest links')).toBeInTheDocument());
    // the asked pair is in the URL now: the people picked here stay as picked
    expect(
      screen.getByRole('button', { name: 'Change from: clear Patrick Mahomes' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change to: clear Bill Belichick' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Strongest links' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('says plainly when no chain joins them', async () => {
    connections(`/explore/ask/connections?a=${MAHOMES}&b=${KELCE}`, {
      [cx(MAHOMES, KELCE, 1)]: CX_NO_CHAIN,
    });
    expect(
      await screen.findByRole('heading', { name: 'No chain of person links joins them.' }),
    ).toBeInTheDocument();
  });

  it.each([
    ['history_down', 'The history graph isn’t running.', 'uv run nfl history-graph start'],
    ['busy', 'Busy.', null],
    ['not_found', 'Not in the history graph.', null],
    ['same_person', 'That pair can’t be connected.', null],
  ] as const)('shows the %s refusal with its fixed text', async (key, title, command) => {
    const e = CX_ERRORS[key];
    connections(`/explore/ask/connections?a=${MAHOMES}&b=${KELCE}`, {
      [cx(MAHOMES, KELCE, 1)]: withStatus(e.status, e.body),
    });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(title);
    expect(alert).toHaveTextContent(`(HTTP ${e.status})`);
    if (command) expect(within(alert).getByText(command)).toBeInTheDocument();
    if (key === 'busy')
      expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

function lab(view: string, query = '', extra: Record<string, unknown> = {}) {
  const fetchMock = mockApi({
    '/api/ask/graph-lab': STATUS,
    '/api/ask/lab/neighbours/map': MAP,
    ...extra,
  });
  renderApp(<LabPage />, {
    route: `/explore/ask/lab/${view}${query}`,
    path: '/explore/ask/lab/:view',
  });
  return fetchMock;
}

describe('Graph lab: Neighbours', () => {
  it('shows a player’s two lists, the map, the as-of line and the W&B link', async () => {
    lab('neighbours', '?player=00-0030506', {
      '/api/ask/lab/neighbours?player=player:00-0030506': NB_KELCE,
    });
    expect(await screen.findByText('8,761 snaps since 2018', { exact: false })).toBeInTheDocument();
    const same = screen.getByRole('region', { name: 'Same position (TE)' });
    expect(within(same).getByRole('button', { name: 'Noah Gray' })).toBeInTheDocument();
    expect(within(same).getByText(/1,910 snaps together/)).toBeInTheDocument();
    const anyone = screen.getByRole('region', { name: 'Anyone' });
    expect(within(anyone).getByText(/never on the field together/)).toBeInTheDocument();
    expect(within(anyone).getAllByRole('img', { name: /^similarity / }).length).toBe(3);
    expect(screen.getByRole('link', { name: 'W&B run ↗' })).toHaveAttribute(
      'href',
      'https://wandb.ai/e/nfl-analytics-engine/runs/fixture1',
    );
    expect(
      screen.getByRole('img', {
        name: /A map of \d+ players.*Travis Kelce and his neighbours are ringed/,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/keep only 13% of the variation \(8\.2% \+ 5\.1%\)/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zoom to Travis Kelce' })).toBeInTheDocument();
    // a group highlight is a toggle
    const qb = screen.getByRole('button', { name: 'QB' });
    fireEvent.click(qb);
    expect(qb).toHaveAttribute('aria-pressed', 'true');
  });

  it('a neighbour’s name picks him', async () => {
    const f = lab('neighbours', '?player=00-0030506', {
      '/api/ask/lab/neighbours?player=player:00-0030506': NB_KELCE,
      '/api/ask/lab/neighbours?player=player:00-0036637': NB_KELCE,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Noah Gray' }));
    await waitFor(() =>
      expect(calls(f)).toContain('/api/ask/lab/neighbours?player=player:00-0036637'),
    );
  });

  it('says why a player isn’t in the map', async () => {
    lab('neighbours', '?player=00-0034229', {
      '/api/ask/lab/neighbours?player=player:00-0034229': NB_NOT_IN_MAP,
    });
    expect(await screen.findByText(/isn’t in the map\./)).toBeInTheDocument();
    expect(screen.getByText(/at least 100 snaps since 2018/)).toBeInTheDocument();
  });

  it('shows the not-run-yet card with the command when the job hasn’t run', async () => {
    mockApi({
      '/api/ask/graph-lab': STATUS,
      '/api/ask/lab/neighbours/map': notBuilt(
        'No neighbours results yet: run `uv run nfl history-graph lab`.',
      ),
    });
    renderApp(<LabPage />, {
      route: '/explore/ask/lab/neighbours',
      path: '/explore/ask/lab/:view',
    });
    expect(await screen.findByRole('heading', { name: 'Not run yet' })).toBeInTheDocument();
    expect(screen.getByText('uv run nfl history-graph lab')).toHaveClass('cmd');
  });

  it('warns when the lab ran on older person links', async () => {
    const older = { ...MAP, asof: { ...MAP.asof!, current: false } };
    lab('neighbours', '', { '/api/ask/lab/neighbours/map': older });
    expect(await screen.findByText('Made on older person links.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Pick a player' })).toBeInTheDocument();
  });
});

describe('Graph lab: Families', () => {
  it('lists the families, filters them, and opens one', async () => {
    const pick = FAMILY_OF_REID.family!;
    const f = lab('families', '', {
      '/api/ask/lab/families': FAMILIES,
      [`/api/ask/lab/families?family=${pick}`]: FAMILY_OF_REID,
    });
    const list = await screen.findByRole('region', { name: 'Families' });
    const n = FAMILIES.families.length;
    expect(within(list).getAllByRole('button')).toHaveLength(n);
    fireEvent.change(screen.getByRole('searchbox', { name: 'Filter families' }), {
      target: { value: 'kelce' },
    });
    expect(within(list).getAllByRole('button')).toHaveLength(1);
    fireEvent.click(within(list).getByRole('button'));
    await waitFor(() => expect(calls(f)).toContain(`/api/ask/lab/families?family=${pick}`));
    const name = FAMILY_OF_REID.families.find((x) => x.community === pick)!.name;
    expect(await screen.findByRole('heading', { name })).toHaveFocus();
    const table = screen.getByRole('table');
    expect(
      within(table).getByRole('columnheader', { name: 'Snaps in this era' }),
    ).toBeInTheDocument();
    expect(within(table).getByText('Andy Reid')).toBeInTheDocument();
    expect(within(table).getByText(/Coaches · 1/)).toBeInTheDocument();
  });

  it('finds someone’s family and highlights them', async () => {
    lab('families', '?person=coach:andy-reid', {
      '/api/ask/lab/families?person=coach%3Aandy-reid': FAMILY_OF_REID,
    });
    const row = (await screen.findByText('(the person you searched)', { exact: false })).closest(
      'tr',
    );
    expect(row).toHaveClass('me');
    expect(row).toHaveTextContent('Andy Reid');
  });
});

describe('Graph lab: Chemistry', () => {
  it('ranks the duos, sorts by a header, switches the minimum, and lists PageRank', async () => {
    const f = lab('chemistry', '', {
      '/api/ask/lab/chemistry?min_targets=100': CHEMISTRY,
      '/api/ask/lab/chemistry?min_targets=300': CHEMISTRY_300,
    });
    const table = await screen.findByRole('table');
    const first = () => within(table).getAllByRole('row')[1];
    expect(first()).toHaveTextContent(CHEMISTRY.duos[0].qb);
    const targets = within(table).getByRole('button', { name: 'Targets' });
    fireEvent.click(targets);
    expect(targets.closest('th')).toHaveAttribute('aria-sort', 'descending');
    const most = [...CHEMISTRY.duos].sort((a, b) => b.targets - a.targets)[0];
    expect(first()).toHaveTextContent(most.receiver);
    fireEvent.click(screen.getByRole('button', { name: '300' }));
    await waitFor(() => expect(calls(f)).toContain('/api/ask/lab/chemistry?min_targets=300'));
    expect(await screen.findByText(/with 300\+ targets/)).toBeInTheDocument();
    const rec = screen.getByRole('region', { name: 'Most central receivers' });
    expect(within(rec).getAllByText(/QBs threw to him/)).toHaveLength(CHEMISTRY.receivers.length);
    const qbs = screen.getByRole('region', { name: 'Most central quarterbacks' });
    expect(within(qbs).getAllByText(/threw to \d+ receivers/)).toHaveLength(CHEMISTRY.qbs.length);
  });
});

describe('Graph lab: Benchmark', () => {
  it('draws the chart and the table of a run, a skipped run included', async () => {
    lab('benchmark', '', { '/api/ask/lab/benchmark': BENCHMARK });
    expect(
      await screen.findByRole('group', { name: /Median milliseconds by number of people/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('smoke run')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Cypher SHORTEST \(two-way BFS\)/ }),
    ).toBeInTheDocument();
    const table = screen.getByRole('table');
    expect(within(table).getAllByText('Floyd–Warshall (numba)').length).toBeGreaterThan(0);
    expect(within(table).getByText('skipped')).toBeInTheDocument();
    expect(
      screen.getByText(/on samples of 1,000 people and the whole graph \(5,665 people/),
    ).toBeInTheDocument();
    // a legend entry highlights its line
    const leg = screen.getByRole('button', { name: /scipy Dijkstra/ });
    fireEvent.click(leg);
    expect(leg).toHaveAttribute('aria-pressed', 'true');
  });

  it('says the benchmark hasn’t run yet', async () => {
    lab('benchmark', '', {
      '/api/ask/lab/benchmark': notBuilt(
        'No benchmark yet: run `uv run nfl history-graph bench-paths`.',
      ),
    });
    expect(
      await screen.findByRole('heading', { name: 'The benchmark hasn’t run yet' }),
    ).toBeInTheDocument();
    expect(screen.getByText('uv run nfl history-graph bench-paths')).toBeInTheDocument();
  });

  it('an unknown lab view goes to Neighbours', async () => {
    lab('nope', '', {});
    expect(await screen.findByRole('heading', { name: 'Neighbours' })).toBeInTheDocument();
  });
});
