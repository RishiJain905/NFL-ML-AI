import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AskRequest, AskResponse } from '../../api/types';
import { Sidebar } from '../../components/Sidebar';
import { mockApi, renderApp, withStatus } from '../../test/utils';
import { AskPage } from './AskPage';
import {
  ANSWERED,
  CLARIFY,
  EMPTY,
  EXAMPLES,
  FALLBACK,
  FREEFORM,
  GRAPH_DOWN,
  LLM_ERROR,
  OUT_OF_CREDITS,
  PICKED,
  QUERY_FAILED,
  REFUSED_CODE,
  REFUSED_ROUTER,
  TRUNCATED,
  errorBody,
  examplesWith,
  examplesWithHistory,
  HISTORY_ANSWER,
  HISTORY_DOWN,
} from './askFixtures';
import { SESSION_KEY, loadSession, saveSession, type SessionItem } from './ask/session';

type Reply = AskResponse | ReturnType<typeof withStatus> | ((b: AskRequest) => unknown);

function setup(reply?: Reply, examples = EXAMPLES) {
  const posts: AskRequest[] = [];
  const fetchMock = mockApi({
    '/api/session': { token: 'test-token' },
    '/api/ask/examples': examples,
    'POST /api/ask': (b: AskRequest) => {
      posts.push(b);
      return typeof reply === 'function' ? reply(b) : reply;
    },
  });
  renderApp(<AskPage />);
  return { posts, fetchMock };
}

const box = () => screen.getByRole('textbox', { name: 'Ask a question about NFL data' });
async function askIt(q: string) {
  fireEvent.change(box(), { target: { value: q } });
  fireEvent.keyDown(box(), { key: 'Enter' });
}
const answerCard = () => screen.getByRole('article');

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('AskPage: the question box', () => {
  it('shows the examples, the sources and the model; an example fills the box without asking', async () => {
    const { posts } = setup(ANSWERED);
    expect(await screen.findByText('weekly graph as of 2026 week 6 · Neo4j up')).toBeInTheDocument();
    expect(screen.getByText('curated data through 2026 week 5')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Model test-model, effort max' })).toBeInTheDocument();
    const chip = screen.getByRole('button', { name: /Who is facing his former team this week\?/ });
    expect(chip).toHaveTextContent('this week');
    fireEvent.click(chip);
    expect(box()).toHaveValue('Who is facing his former team this week?');
    expect(box()).toHaveFocus();
    // "More examples" lists the groups
    const more = screen.getByText('More examples (2)');
    (more.closest('details') as HTMLDetailsElement).open = true;
    fireEvent.click(screen.getByRole('button', { name: /^Which starters are out this week\?/ }));
    expect(box()).toHaveValue('Which starters are out this week?');
    expect(posts).toHaveLength(0);
    expect(screen.getByRole('heading', { name: 'Ask about players, teams, games or coaches' })).toBeInTheDocument();
  });

  it('caps the question at 500 characters', async () => {
    const { posts } = setup(ANSWERED);
    await screen.findByText('More examples (2)');
    fireEvent.change(box(), { target: { value: 'x'.repeat(501) } });
    expect(screen.getByText('501 / 500 · too long')).toBeInTheDocument();
    expect(box()).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled();
    fireEvent.keyDown(box(), { key: 'Enter' });
    fireEvent.change(box(), { target: { value: 'x'.repeat(500) } });
    expect(screen.getByText('500 / 500')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled();
    expect(posts).toHaveLength(0);
  });

  it('asks on Enter, not on Shift+Enter, and sends the source picked', async () => {
    const { posts } = setup(ANSWERED);
    await screen.findByText('More examples (2)');
    fireEvent.change(box(), { target: { value: 'Who did Joe Example throw to?' } });
    fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true });
    expect(posts).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Tables' }));
    fireEvent.keyDown(box(), { key: 'Enter' });
    await waitFor(() => expect(posts).toEqual([{ question: 'Who did Joe Example throw to?', source: 'sql' }]));
    expect(await screen.findByRole('article')).toBeInTheDocument();
  });

  it('works when the examples fail to load', async () => {
    mockApi({ '/api/session': { token: 't' }, 'POST /api/ask': ANSWERED });
    renderApp(<AskPage />);
    expect(await screen.findByText("The examples didn't load; you can still ask.")).toBeInTheDocument();
    await askIt('Who did Joe Example throw to the most in 2025?');
    expect(await screen.findByRole('article')).toHaveTextContent('Sam Sample');
  });
});

describe('AskPage: the answer card', () => {
  it('shows an answered template: sentences, the number check, chips, rows and the query', async () => {
    setup(ANSWERED);
    await askIt(ANSWERED.question);
    const card = await screen.findByRole('article');
    expect(within(card).getByRole('heading', { name: ANSWERED.question })).toHaveFocus();
    expect(card).toHaveTextContent(ANSWERED.answer!);
    expect(within(card).getByText('Every number checked against the rows')).toBeInTheDocument();
    expect(within(card).getByRole('img', { name: 'Source: weekly graph as of 2026 week 6' })).toBeInTheDocument();
    const route = within(card).getByRole('img', { name: /^Route: template: qb_receivers\. A quarterback's receivers/ });
    expect(route).toHaveTextContent('template: qb_receivers');
    expect(within(card).getByRole('img', { name: /^Time: 2\.4 s\. Route: 1\.0 s, Run the query: 0\.08 s, Write the answer: 1\.3 s/ })).toBeInTheDocument();
    expect(within(card).getByRole('img', { name: /^Cost: \$0\.0002\. 2 model calls/ })).toBeInTheDocument();
    // hover / focus opens the breakdown
    fireEvent.focus(within(card).getByRole('img', { name: /^Time:/ }));
    expect(screen.getByRole('tooltip')).toHaveTextContent('Took 2.4 s in all');
    expect(card).toHaveTextContent('Showing 3 of 3 rows');
    const table = within(card).getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    const first = within(table).getAllByRole('row')[1];
    expect(within(first).getByText('31')).toHaveClass('r');
    expect(within(first).getByText('2025').closest('.ask-li')).not.toBeNull();
    // the query block: language, copy, bound values
    expect(within(card).getByText('Cypher')).toBeInTheDocument();
    expect(card.querySelector('pre.ask-code')).toHaveTextContent('MATCH (q:Player {player_id: $player})');
    expect(within(card).getByText('$player')).toBeInTheDocument();
    expect(within(card).getByText('00-0000001')).toBeInTheDocument();
  });

  it('copies the query', async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    setup(ANSWERED);
    await askIt(ANSWERED.question);
    fireEvent.click(within(await screen.findByRole('article')).getByRole('button', { name: 'Copy' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(ANSWERED.query));
    expect(await screen.findByText('Copied')).toBeInTheDocument();
  });

  it('sorts the rows by a header: ascending, descending, then the server order; empty cells last', async () => {
    setup(ANSWERED);
    await askIt(ANSWERED.question);
    const table = within(await screen.findByRole('article')).getByRole('table');
    const receivers = () => within(table).getAllByRole('row').slice(1).map((r) => r.children[1].textContent);
    const catches = within(table).getByRole('button', { name: /catches/ });
    expect(receivers()).toEqual(['Sam Sample', 'Alex Alpha', 'Bo Beta']);
    fireEvent.click(catches);
    expect(catches.closest('th')).toHaveAttribute('aria-sort', 'ascending');
    expect(receivers()).toEqual(['Alex Alpha', 'Sam Sample', 'Bo Beta']);
    fireEvent.click(catches);
    expect(catches.closest('th')).toHaveAttribute('aria-sort', 'descending');
    expect(receivers()).toEqual(['Sam Sample', 'Alex Alpha', 'Bo Beta']);
    fireEvent.click(within(table).getByRole('button', { name: /targets/ }));
    expect(receivers()).toEqual(['Alex Alpha', 'Bo Beta', 'Sam Sample']);
  });

  it('shows a free-form answer with its retry, time and cost', async () => {
    setup(FREEFORM);
    await askIt(FREEFORM.question);
    const card = await screen.findByRole('article');
    const route = within(card).getByRole('img', { name: /^Route: free-form, 1 retry/ });
    expect(route).toHaveClass('run');
    expect(route.getAttribute('aria-label')).toContain('Retries: 1');
    expect(within(card).getByRole('img', { name: /^Time: 14 s/ })).toBeInTheDocument();
    expect(within(card).getByRole('img', { name: /^Cost: \$0\.0024/ })).toBeInTheDocument();
    expect(within(card).getByText('SQL')).toBeInTheDocument();
    expect(card.querySelector('.ask-params')).toBeNull();
  });

  it('says when the model’s sentences failed the number check', async () => {
    setup(FALLBACK);
    await askIt(FALLBACK.question);
    const card = await screen.findByRole('article');
    expect(card).toHaveTextContent('Only the table is shown.');
    expect(within(card).queryByText('Every number checked against the rows')).toBeNull();
  });

  it('says when more rows existed than the cap', async () => {
    setup(TRUNCATED);
    await askIt(TRUNCATED.question);
    expect(await screen.findByText(/Showing 3 of 500 rows · more rows existed than the 3-row cap/)).toBeInTheDocument();
  });

  it('shows a refusal by code (no model call) and by the router', async () => {
    let next: AskResponse = REFUSED_CODE;
    setup(() => next);
    await askIt(REFUSED_CODE.question);
    let card = await screen.findByRole('article');
    expect(within(card).getByRole('heading', { name: REFUSED_CODE.refusal!.message })).toBeInTheDocument();
    expect(card).toHaveTextContent('Refused before any model call · rule write');
    expect(card).toHaveTextContent('Read only');
    expect(within(card).getByRole('img', { name: /^Cost: no model call/ })).toBeInTheDocument();
    expect(within(card).getByRole('img', { name: /^Time: under 0\.1 s/ })).toBeInTheDocument();
    next = REFUSED_ROUTER;
    await askIt(REFUSED_ROUTER.question);
    await screen.findByRole('heading', { name: REFUSED_ROUTER.question });
    card = answerCard();
    expect(card).toHaveTextContent('Refused by the router (the model’s first step)');
    expect(card).toHaveTextContent('NFL only');
  });

  it('asks again with the pick from a which-one card', async () => {
    const { posts } = setup((b) => (b.picks ? PICKED : CLARIFY));
    fireEvent.click(await screen.findByRole('button', { name: 'Tables' }));
    await askIt(CLARIFY.question);
    const card = await screen.findByRole('article');
    expect(within(card).getByRole('heading', { name: 'Which Chris Common?' })).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: /Chris Common \(LB, BBB, 2012-2018\)/ }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]).toEqual({
      question: CLARIFY.question,
      source: 'sql',
      picks: [{ text: 'Chris Common', kind: 'player', value: '00-0000012' }],
    });
    expect(await screen.findByText('Chris Common caught 88 passes in 2019.')).toBeInTheDocument();
  });

  it('shows no rows with the query and the columns it would show', async () => {
    setup(EMPTY);
    await askIt(EMPTY.question);
    const card = await screen.findByRole('article');
    expect(within(card).getByRole('heading', { name: 'No rows matched.' })).toBeInTheDocument();
    expect(within(card).getByLabelText('Columns it would show')).toHaveTextContent('games_together');
    expect(card.querySelector('pre.ask-code')).not.toBeNull();
    expect(within(card).queryByRole('table')).toBeNull();
  });

  it('shows the error cards: the model, out of credits, a query that kept failing', async () => {
    let next: AskResponse = LLM_ERROR;
    setup(() => next);
    await askIt(LLM_ERROR.question);
    let card = await screen.findByRole('article');
    expect(within(card).getByRole('heading', { name: 'The model didn’t answer' })).toBeInTheDocument();
    expect(card).toHaveTextContent('gave up after 2 attempts (HTTP 503)');
    expect(card).toHaveTextContent('Routing (the first model call)');
    expect(card).toHaveTextContent('openrouter');
    expect(within(card).getByRole('img', { name: /^Cost: cost unknown/ })).toBeInTheDocument();
    next = OUT_OF_CREDITS;
    await askIt('Again?');
    await screen.findByRole('heading', { name: 'Out of credits' });
    expect(answerCard()).toHaveTextContent('top up the account');
    next = QUERY_FAILED;
    await askIt('And again?');
    await screen.findByRole('heading', { name: 'The query kept failing' });
    card = answerCard();
    expect(card).toHaveTextContent('The last query tried');
    expect(card.querySelector('pre.ask-code')).toHaveTextContent('SELECT kicker');
  });
});

describe('AskPage: banners and refused requests', () => {
  it('shows the graph-down banner and a graph-down refusal', async () => {
    setup(GRAPH_DOWN, examplesWith({}, { status: 'down' }));
    expect(await screen.findByText(/questions about the tables still work/)).toBeInTheDocument();
    expect(screen.getByText(/weekly graph as of 2026 week 6 · Neo4j down/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Weekly graph (down)' })).toBeEnabled();
    await askIt(GRAPH_DOWN.question);
    const card = await screen.findByRole('article');
    expect(card).toHaveTextContent('Stopped: the data source isn’t available (the graph is down)');
    expect(card).toHaveTextContent('Graph down');
  });

  it('shows the weekly-refresh banner', async () => {
    setup(ANSWERED, examplesWith({ busy: true }));
    expect(await screen.findByText('The weekly refresh is running.')).toBeInTheDocument();
  });

  it.each([
    [409, 'busy', 'A question is already running: wait for its answer, then ask again.', 'Still finishing the last question.'],
    [429, 'rate_limited', "That's the limit of 10 questions a minute: wait 40 s, then ask again.", 'Too many questions in a minute.'],
    [422, 'bad_request', 'Ask a question of 1-500 characters (source: auto, graph, sql or history; up to 5 picks from a which-one card).', 'That request wasn’t accepted.'],
  ])('shows a %i notice with the server’s message and keeps the page usable', async (status, code, message, title) => {
    let refuse = true;
    setup(() => (refuse ? withStatus(status, errorBody(code, message)) : ANSWERED));
    await askIt('Who did Joe Example throw to the most in 2025?');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(title);
    expect(alert).toHaveTextContent(message);
    expect(alert).toHaveTextContent(`HTTP ${status}`);
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled();
    fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).toBeNull();
    refuse = false;
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(await screen.findByRole('article')).toHaveTextContent('Sam Sample');
  });
});

describe('AskPage: running and Cancel', () => {
  function pendingAsk() {
    const signals: AbortSignal[] = [];
    let release: (r: Response) => void = () => {};
    const base = mockApi({ '/api/session': { token: 't' }, '/api/ask/examples': EXAMPLES });
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === '/api/ask' && init?.method === 'POST') {
          const signal = init.signal as AbortSignal;
          signals.push(signal);
          return new Promise<Response>((resolve, reject) => {
            release = resolve;
            signal.addEventListener('abort', () => reject(new DOMException('The user aborted a request.', 'AbortError')));
          });
        }
        return base(input, init);
      }),
    );
    renderApp(<AskPage />);
    return { signals, release: (a: AskResponse) => release(new Response(JSON.stringify(a), { status: 200 })) };
  }

  it('shows the running card with elapsed seconds; Cancel stops waiting and leaves the page usable', async () => {
    const { signals } = pendingAsk();
    await askIt('A slow question?');
    expect(await screen.findByText('A slow question?', { selector: '.q' })).toBeInTheDocument();
    expect(screen.getByText('0 s')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Asking…' })).toBeDisabled();
    // one question at a time: Enter does nothing while one runs
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(signals).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(signals[0].aborted).toBe(true);
    await waitFor(() => expect(screen.queryByText('A slow question?', { selector: '.q' })).toBeNull());
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled();
    expect(box()).toHaveFocus();
    expect(screen.getByText('Questions you ask stay here until you clear them.')).toBeInTheDocument();
  });

  it('Escape cancels; an answer that arrives later is ignored', async () => {
    const { signals, release } = pendingAsk();
    await askIt('Another slow one?');
    await screen.findByText('Another slow one?', { selector: '.q' });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(signals[0].aborted).toBe(true);
    await act(async () => release(ANSWERED));
    expect(screen.queryByRole('article')).toBeNull();
  });
});

describe('AskPage: the session list', () => {
  const item = (id: string, answer: AskResponse): SessionItem => ({ id, request: { question: answer.question, source: 'auto' }, answer });

  it('restores this session from localStorage, shows an earlier answer and clears', async () => {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ v: 1, items: [item('b', FREEFORM), item('a', REFUSED_CODE), { junk: true }] }));
    setup(ANSWERED);
    const list = await screen.findByRole('list', { name: 'Questions asked, newest first' });
    const rows = within(list).getAllByRole('button');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent(FREEFORM.question);
    expect(rows[0]).toHaveTextContent('free-form, 1 retry');
    expect(rows[0]).toHaveAttribute('aria-current', 'true');
    expect(rows[1]).toHaveTextContent('refused');
    expect(answerCard()).toHaveTextContent(FREEFORM.answer!);
    fireEvent.click(rows[1]);
    expect(within(answerCard()).getByRole('heading', { name: REFUSED_CODE.question })).toHaveFocus();
    expect(box()).toHaveValue(REFUSED_CODE.question); // the box gets that question back, as in the mockup
    // a new answer goes on top and is saved
    await askIt(ANSWERED.question);
    await screen.findByRole('heading', { name: ANSWERED.question });
    expect(within(list).getAllByRole('button')[0]).toHaveTextContent(ANSWERED.question);
    expect(loadSession().map((i) => i.answer.question)).toEqual([ANSWERED.question, FREEFORM.question, REFUSED_CODE.question]);
    fireEvent.click(screen.getByRole('button', { name: 'Clear session' }));
    expect(screen.getByText('Questions you ask stay here until you clear them.')).toBeInTheDocument();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    expect(screen.queryByRole('article')).toBeNull();
  });

  it('works when localStorage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    setup(ANSWERED);
    await askIt(ANSWERED.question);
    expect(await screen.findByRole('article')).toHaveTextContent('Sam Sample');
    const list = screen.getByRole('list', { name: 'Questions asked, newest first' });
    expect(within(list).getAllByRole('button')).toHaveLength(1);
    expect(screen.getByText(/Browser storage is off/)).toBeInTheDocument();
  });

  it('drops the oldest questions when the storage quota is full, and caps the list at 50', () => {
    const many = Array.from({ length: 60 }, (_, i) => item(`q${i}`, { ...ANSWERED, question: `Question ${i}` }));
    expect(saveSession(many)).toBe(50);
    expect(loadSession()).toHaveLength(50);
    const real = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, k: string, v: string) {
      if (v.length > 6000) throw new DOMException('full', 'QuotaExceededError');
      real.call(this, k, v);
    });
    const kept = saveSession(many);
    expect(kept).toBeGreaterThan(0);
    expect(kept).toBeLessThan(50);
    expect(loadSession().map((i) => i.answer.question)[0]).toBe('Question 0');
  });
});

describe('Sidebar: Explore', () => {
  it('links Ask the Engine after Play calling', async () => {
    mockApi();
    renderApp(<Sidebar />);
    const explore = await screen.findByRole('navigation', { name: 'Explore' });
    expect(within(explore).getAllByRole('link').map((l) => l.textContent)).toEqual(['Play calling', 'Ask the Engine']);
    expect(within(explore).getByRole('link', { name: 'Ask the Engine' })).toHaveAttribute('href', '/explore/ask');
  });
});

describe('AskPage: the history graph (AE02)', () => {
  it('has no History graph option while it is switched off or not built', async () => {
    setup(ANSWERED);
    await screen.findByText('More examples (2)');
    expect(screen.queryByRole('button', { name: /History graph/ })).toBeNull();
    expect(screen.queryByText(/history graph/i)).toBeNull();
  });

  it('has no History graph option before the first build', async () => {
    setup(ANSWERED, examplesWithHistory({ status: 'none', label: null }));
    await screen.findByText('More examples (3)');
    expect(screen.queryByRole('button', { name: /^History graph/ })).toBeNull();
    expect(screen.getByText('history graph · not built yet')).toBeInTheDocument();
  });

  it('offers the history graph when it is up, sends source history and labels the answer', async () => {
    const { posts } = setup(HISTORY_ANSWER, examplesWithHistory());
    expect(await screen.findByText('history graph 2018-2025 + 2026 week 5 · running')).toBeInTheDocument();
    const more = screen.getByText('More examples (3)');
    (more.closest('details') as HTMLDetailsElement).open = true;
    expect(screen.getByRole('heading', { name: /The history graph/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Who has caught the most 3rd-down passes from Joe Example\?/ }));
    fireEvent.click(screen.getByRole('button', { name: 'History graph' }));
    fireEvent.keyDown(box(), { key: 'Enter' });
    await waitFor(() =>
      expect(posts).toEqual([{ question: 'Who has caught the most 3rd-down passes from Joe Example?', source: 'history' }]),
    );
    const card = await screen.findByRole('article');
    expect(within(card).getByRole('img', { name: 'Source: history graph 2018-2025 + 2026 week 5' })).toBeInTheDocument();
    expect(within(card).getByText('Cypher')).toBeInTheDocument();
  });

  it('says how to start it when it is down', async () => {
    setup(HISTORY_DOWN, examplesWithHistory({ status: 'down' }));
    expect(await screen.findByText('history graph 2018-2025 + 2026 week 5 · not running')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'History graph (not running)' }));
    await askIt(HISTORY_DOWN.question);
    const card = await screen.findByRole('article');
    expect(card).toHaveTextContent('Stopped: the history graph isn’t running');
    expect(card).toHaveTextContent('History down');
  });
});
