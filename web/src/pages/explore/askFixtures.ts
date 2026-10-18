// Small synthetic Ask the Engine answers for the web tests (AE01), shaped like the real API's
// (`AskResponse`, `AskExamplesResponse`). Made up: never a pasted dump.

import type { AskExamplesResponse, AskResponse } from '../../api/types';

export const EXAMPLES: AskExamplesResponse = {
  sources: {
    graph: { on: true, status: 'up', season: 2026, week: 6, label: 'weekly graph as of 2026 week 6' },
    sql: { on: true, status: 'up', season: 2026, week: 5, rebuilt: '2026-10-14', label: 'curated data through 2026 week 5' },
  },
  model: { name: 'vendor/test-model', label: 'test-model', effort: 'max', provider: 'openrouter', timeout_s: 120 },
  limits: { question_chars: 500, per_minute: 10, rows: 500, query_timeout_s: 10, corrections: 2 },
  busy: false,
  featured: [
    { question: 'Who threw the most touchdowns in 2024?', template: 'season_leaders', title: 'Season stat leaders', source: 'sql', this_week: false },
    { question: 'Who is facing his former team this week?', template: 'revenge_games_this_week', title: 'Players facing a former team this week', source: 'graph', this_week: true },
  ],
  groups: [
    {
      source: 'graph',
      label: 'The weekly graph',
      examples: [
        { question: 'Which starters are out this week?', template: 'starters_out_this_week', title: 'Starters out this week', source: 'graph', this_week: true },
      ],
    },
    {
      source: 'sql',
      label: 'The curated tables',
      examples: [
        { question: 'What is the Jets record against the Dolphins since 2019?', template: 'head_to_head', title: 'Head-to-head record', source: 'sql', this_week: false },
      ],
    },
  ],
};

export const examplesWith = (over: Partial<AskExamplesResponse>, graph?: Partial<AskExamplesResponse['sources']['graph']>): AskExamplesResponse => ({
  ...EXAMPLES,
  ...over,
  sources: { ...EXAMPLES.sources, graph: { ...EXAMPLES.sources.graph, ...graph } },
});

const BASE: AskResponse = {
  question: 'Who did Joe Example throw to the most in 2025?',
  asked_at: '2026-10-18T19:00:00+00:00',
  status: 'answered',
  answer: 'Joe Example threw to Sam Sample the most in 2025: 31 targets, 22 catches.',
  answer_kind: 'model',
  columns: ['qb', 'receiver', 'targets', 'catches', 'yards', 'seasons'],
  rows: [
    ['Joe Example', 'Sam Sample', 31, 22, 301, [2025]],
    ['Joe Example', 'Alex Alpha', 18, 12, 140, [2024, 2025]],
    ['Joe Example', 'Bo Beta', 25, null, 220, [2025]],
  ],
  row_count: 3,
  truncated: false,
  query: 'MATCH (q:Player {player_id: $player})-[t:THREW_TO]->(r:Player)\nRETURN q.name AS qb, r.name AS receiver',
  language: 'cypher',
  params: [
    { name: 'player', value: '00-0000001' },
    { name: 'first_season', value: 2025 },
  ],
  source: { kind: 'graph', season: 2026, week: 6, label: 'weekly graph as of 2026 week 6' },
  route: 'template',
  route_label: 'template: qb_receivers',
  template: { id: 'qb_receivers', title: "A quarterback's receivers" },
  retries: 0,
  refusal: null,
  choices: [],
  error: null,
  timing: { total_s: 2.4, route_s: 1.0, generate_s: null, run_s: 0.08, answer_s: 1.3 },
  cost: 0.00021,
  llm: {
    model: 'test-model',
    calls: [
      { purpose: 'route', provider: 'ProviderA', seconds: 1.0, cost: 0.0001, ok: true },
      { purpose: 'answer', provider: 'ProviderA', seconds: 1.3, cost: 0.00011, ok: true },
    ],
    providers: ['ProviderA'],
  },
};

const answer = (over: Partial<AskResponse>): AskResponse => ({ ...BASE, ...over });
const noRows: Pick<AskResponse, 'columns' | 'rows' | 'row_count' | 'query' | 'language' | 'params'> = {
  columns: [],
  rows: [],
  row_count: 0,
  query: null,
  language: null,
  params: [],
};

export const ANSWERED = BASE;

export const FREEFORM = answer({
  question: 'Which kickers made the most 50-yard field goals in 2023?',
  answer: 'Kim Kicker made the most: 9.',
  columns: ['kicker', 'team', 'fg_50_plus'],
  rows: [['Kim Kicker', 'AAA', 9]],
  row_count: 1,
  query: 'SELECT kicker, team, fg_50_plus FROM player_games LIMIT 1',
  language: 'sql',
  params: [],
  source: { kind: 'sql', season: 2026, week: 5, rebuilt: '2026-10-14', label: 'curated data through 2026 week 5' },
  route: 'generated',
  route_label: 'free-form, 1 retry',
  template: null,
  retries: 1,
  timing: { total_s: 14.2, route_s: 1.2, generate_s: 11.5, run_s: 0.4, answer_s: 1.1 },
  cost: 0.0024,
});

export const FALLBACK = answer({
  question: 'How many yards did Sam Sample have in 2025?',
  answer: 'The query returned 3 rows: the numbers are in the table below.',
  answer_kind: 'fallback',
});

export const TRUNCATED = answer({ question: 'List every play from 2025', row_count: 500, truncated: true });

export const REFUSED_CODE = answer({
  question: 'Remove every game from 2020',
  status: 'refused',
  answer: 'Ask the Engine only reads the data: it can not change anything.',
  answer_kind: 'fixed',
  ...noRows,
  source: null,
  route: 'refused',
  route_label: 'refused',
  template: null,
  refusal: { by: 'code', rule: 'write', message: 'Ask the Engine only reads the data: it can not change anything.' },
  timing: { total_s: 0, route_s: null, generate_s: null, run_s: null, answer_s: null },
  cost: 0,
  llm: { model: 'test-model', calls: [], providers: [] },
});

export const REFUSED_ROUTER = answer({
  question: 'Will it rain in Chicago tomorrow?',
  status: 'refused',
  answer: 'Ask the Engine answers questions about NFL data only.',
  answer_kind: 'fixed',
  ...noRows,
  source: null,
  route: 'refused',
  route_label: 'refused',
  template: null,
  refusal: { by: 'router', rule: 'not_nfl', message: 'Ask the Engine answers questions about NFL data only.' },
  timing: { total_s: 0.8, route_s: 0.8, generate_s: null, run_s: null, answer_s: null },
  cost: 0.0001,
  llm: { model: 'test-model', calls: [{ purpose: 'route', provider: 'ProviderA', seconds: 0.8, cost: 0.0001, ok: true }], providers: ['ProviderA'] },
});

export const GRAPH_DOWN = answer({
  question: 'Who coached under Pat Coach?',
  status: 'refused',
  answer: 'The weekly graph is not answering right now. Questions about the tables still work.',
  answer_kind: 'fixed',
  ...noRows,
  language: 'cypher',
  route: 'refused',
  route_label: 'refused',
  refusal: { by: 'source', rule: 'source.down', message: 'The weekly graph is not answering right now. Questions about the tables still work.' },
  cost: 0.00008,
});

export const CLARIFY = answer({
  question: 'How many catches did Chris Common have in 2019?',
  status: 'clarify',
  answer: 'More than one player matches "Chris Common": which one?',
  answer_kind: 'fixed',
  ...noRows,
  source: null,
  route: 'clarify',
  route_label: 'needs a choice',
  template: null,
  choices: [
    {
      text: 'Chris Common',
      kind: 'player',
      options: [
        { value: '00-0000011', label: 'Chris Common (WR, AAA, 2015-2022)' },
        { value: '00-0000012', label: 'Chris Common (LB, BBB, 2012-2018)' },
      ],
    },
  ],
  timing: { total_s: 0, route_s: null, generate_s: null, run_s: null, answer_s: null },
  cost: 0,
  llm: { model: 'test-model', calls: [], providers: [] },
});

export const PICKED = answer({
  question: 'How many catches did Chris Common have in 2019?',
  answer: 'Chris Common caught 88 passes in 2019.',
  columns: ['player', 'season', 'receptions'],
  rows: [['Chris Common', 2019, 88]],
  row_count: 1,
  language: 'sql',
  query: 'SELECT player, season, receptions FROM player_games WHERE player_id = $player',
  params: [{ name: 'player', value: '00-0000011' }],
});

export const EMPTY = answer({
  question: 'Who are the former teammates in this week’s AAA-BBB game?',
  status: 'empty',
  answer: 'No rows matched.',
  answer_kind: 'fixed',
  columns: ['qb', 'receiver', 'games_together'],
  rows: [],
  row_count: 0,
});

export const LLM_ERROR = answer({
  question: 'Who led the league in rushing in 2022?',
  status: 'error',
  answer: null,
  answer_kind: 'fixed',
  ...noRows,
  source: null,
  route: 'none',
  route_label: null,
  template: null,
  error: { code: 'llm', message: "The model didn't give a usable reply: gave up after 2 attempts (HTTP 503).", stage: 'route', provider: 'openrouter', model: 'test-model' },
  timing: { total_s: 1, route_s: null, generate_s: null, run_s: null, answer_s: null },
  cost: null,
  llm: { model: 'test-model', calls: [{ purpose: 'route', provider: null, seconds: 1, cost: null, ok: false }], providers: [] },
});

export const OUT_OF_CREDITS = answer({
  ...LLM_ERROR,
  error: { code: 'out_of_credits', message: 'OpenRouter is out of credits: top up the account, then ask again.', stage: 'route', provider: 'openrouter', model: 'test-model' },
});

export const QUERY_FAILED = answer({
  ...FREEFORM,
  status: 'error',
  answer: null,
  answer_kind: 'fixed',
  columns: [],
  rows: [],
  row_count: 0,
  retries: 2,
  route_label: 'free-form, 2 retries',
  error: { code: 'query_failed', message: 'The query still failed after 2 corrections (binder_error).', stage: null, provider: 'ProviderA', model: 'test-model' },
});

export const errorBody = (code: string, message: string) => ({ error: { code, message } });
