// Small synthetic play-calling answers for the web tests (PC01). Made up: never real API dumps.

import type {
  PlayCallsResponse,
  PlaycallCell,
  PlaycallGridColumn,
  PlaycallHistoryResponse,
  PlaycallMeta,
  PlaycallMetric,
  PlaycallTeamResponse,
  PlaycallTeamsResponse,
  PlaycallWindows,
} from '../../api/types';

export const MIN_N = 20;

const metric = (m: Partial<PlaycallMetric> & Pick<PlaycallMetric, 'metric' | 'label' | 'short'>): PlaycallMetric => ({
  unit: 'share',
  digits: 1,
  source: 'pbp',
  situation: 'all',
  per: 'play',
  caller: 'offense',
  help: null,
  ...m,
});

export const PROE = metric({ metric: 'proe', label: 'Pass rate over expected (neutral)', short: 'PROE', unit: 'over_expected', situation: 'neutral' });
export const PLAY_ACTION = metric({ metric: 'play_action_rate', label: 'Play-action per dropback', short: 'Play-action', source: 'ftn', per: 'dropback', help: 'The QB fakes a handoff before throwing.' });
export const SHOTGUN = metric({ metric: 'shotgun_rate', label: 'Shotgun (incl. pistol)', short: 'Shotgun' });
export const ADOT = metric({ metric: 'adot', label: 'Average depth of target', short: 'aDOT', unit: 'mean', per: 'attempt' });
export const BLITZ = metric({ metric: 'blitz_rate', label: 'Blitz per dropback', short: 'Blitz', source: 'ftn', per: 'dropback', caller: 'defense' });
export const DEEP = metric({ metric: 'deep_shot_rate', label: 'Deep shots per attempt', short: 'Deep shots', per: 'attempt' });

export const cell = (value: number | null, league: number, n: number, pct = 50, games: number | null = 4): PlaycallCell => ({
  n,
  games,
  value,
  league,
  diff: value == null ? null : value - league,
  pct: value == null ? null : pct,
  small: n < MIN_N,
});

const wins = (season: PlaycallCell, last4?: PlaycallCell | null, last?: PlaycallCell | null): PlaycallWindows => ({
  season,
  last4: last4 ?? season,
  last_season: last ?? null,
});

export const META: PlaycallMeta = {
  status: 'ok',
  message: null,
  season: 2026,
  seasons: [2026, 2025],
  as_of_week: 5,
  through_week: 4,
  built_at: '2026-10-10T12:00:00+00:00',
  min_n: MIN_N,
  ftn_waiting: ['ATL at NO, week 4'],
};

// ---- the grid: 32 teams, PROE rising with the alphabet, blitz falling ----

const TEAM_CODES = ['ARI', 'ATL', 'BAL', 'BUF', 'CAR', 'CHI', 'CIN', 'CLE', 'DAL', 'DEN', 'DET', 'GB', 'HOU', 'IND', 'JAX', 'KC', 'LA', 'LAC', 'LV', 'MIA', 'MIN', 'NE', 'NO', 'NYG', 'NYJ', 'PHI', 'PIT', 'SEA', 'SF', 'TB', 'TEN', 'WAS'];

const col = (m: PlaycallMetric, side: 'offense' | 'defense', signature: boolean): PlaycallGridColumn => ({
  ...m,
  id: `${side}.${m.metric}`,
  side,
  signature,
});

export const TEAMS: PlaycallTeamsResponse = {
  ...META,
  window: 'season',
  columns: [col(PROE, 'offense', true), col(PLAY_ACTION, 'offense', false), col(BLITZ, 'defense', true)],
  teams: TEAM_CODES.map((team, i) => ({
    team,
    games: 4,
    cells: {
      'offense.proe': cell(-0.1 + i * 0.006, -0.019, 140, (i / 31) * 100),
      // MIN's play-action rate rests on 12 dropbacks: a small sample
      'offense.play_action_rate': cell(0.15 + (i % 7) * 0.03, 0.241, team === 'MIN' ? 12 : 130, 40),
      'defense.blitz_rate': cell(0.5 - i * 0.01, 0.311, 130, 100 - (i / 31) * 100),
    },
  })),
};

// ---- a team page (KC offense) ----

const ZONES = (['short', 'deep'] as const).flatMap((depth, d) =>
  (['left', 'middle', 'right'] as const).map((direction, k) => ({
    metric: `pass_${depth}_${direction}`,
    depth,
    direction,
    windows: wins(cell([0.24, 0.17, 0.26, 0.12, 0.08, 0.13][d * 3 + k], [0.25, 0.18, 0.23, 0.12, 0.08, 0.14][d * 3 + k], 110)),
  })),
);

const LANES = (
  [
    ['left_end', 'Left end', 0.15],
    ['left_tackle', 'Left tackle', 0.08],
    ['left_guard', 'Left guard', 0.1],
    ['middle', 'Middle', 0.27],
    ['right_guard', 'Right guard', 0.12],
    ['right_tackle', 'Right tackle', 0.1],
    ['right_end', 'Right end', 0.18],
  ] as const
).map(([lane, label, v]) => ({ lane, metric: `run_${lane}`, label, windows: wins(cell(v, 0.11, 95)) }));

export const TEAM: PlaycallTeamResponse = {
  ...META,
  team: 'KC',
  side: 'offense',
  games: 6,
  summary: ['KC passes 5.6 points more than expected in neutral spots (league −1.9).', 'Play-action on 36.6% of dropbacks, the most in the league.'],
  identity: [
    {
      id: 'pass_run',
      title: 'Pass or run',
      note: null,
      rows: [{ ...PROE, windows: wins(cell(0.037, -0.019, 140, 88), cell(0.01, -0.019, 70, 60), cell(0.02, -0.02, 600, 80, 17)) }],
    },
    {
      id: 'pass_game',
      title: 'The pass game',
      note: null,
      rows: [
        { ...PLAY_ACTION, windows: wins(cell(0.366, 0.241, 130, 97), cell(0.4, 0.24, 12, 99)) },
        { ...ADOT, windows: wins(cell(7.7, 8.02, 120, 30)) },
      ],
    },
  ],
  situations: {
    metrics: [PROE, PLAY_ACTION],
    baseline: { situation: 'all', label: 'All plays', cells: { proe: wins(cell(0.037, -0.019, 140)), play_action_rate: wins(cell(0.366, 0.241, 130)) } },
    families: [
      {
        family: 'down_distance',
        title: 'Down & distance',
        rows: [
          { situation: '1st_down', label: '1st down', cells: { proe: wins(cell(0.05, -0.02, 60)), play_action_rate: wins(cell(0.45, 0.39, 30)) } },
          { situation: '4th_down', label: '4th down', cells: { proe: wins(cell(0.2, 0.0, 4)), play_action_rate: wins(cell(0.5, 0.05, 2)) } },
        ],
      },
      {
        family: 'field_zone',
        title: 'Field zone',
        rows: [{ situation: 'red_zone', label: 'Red zone', cells: { proe: wins(cell(-0.04, -0.03, 30)), play_action_rate: wins(cell(0.3, 0.25, 21)) } }],
      },
    ],
  },
  field: {
    zones: ZONES,
    directions: (['left', 'middle', 'right'] as const).map((direction, i) => ({
      metric: `pass_${direction}`,
      depth: null,
      direction,
      windows: wins(cell([0.36, 0.25, 0.39][i], [0.37, 0.26, 0.37][i], 110)),
    })),
    depth: [{ ...DEEP, windows: wins(cell(0.134, 0.121, 120, 68)) }],
  },
  runs: LANES,
  weekly: {
    games: [1, 2, 3, 4].map((week) => ({ week, game_id: `2026_0${week}_KC_X`, opponent: ['LAC', 'PHI', 'NYG', 'BAL'][week - 1], home: week % 2 === 0 })),
    series: [{ ...PROE, league: -0.019, points: [0.02, 0.06, -0.01, 0.08].map((value, i) => ({ week: i + 1, value, n: i === 2 ? 14 : 36 })) }],
  },
  next_game: { season: 2026, week: 5, game_id: '2026_05_KC_JAX', opponent: 'JAX', home: false, kickoff: '2026-10-11T17:00:00Z' },
  notes: ['FTN rates count a game 48 hours after kickoff.'],
};

export const TEAM_DEFENSE: PlaycallTeamResponse = {
  ...TEAM,
  side: 'defense',
  summary: ['KC blitzes on 39.0% of dropbacks (league 31.1%).'],
  identity: [
    {
      id: 'pressure',
      title: 'Pressure and the box',
      note: null,
      rows: [{ ...BLITZ, windows: wins(cell(0.39, 0.311, 130, 90)) }],
    },
    {
      id: 'faced',
      title: 'What offenses do against it',
      note: 'Depends on the offenses it faced.',
      rows: [{ ...PROE, windows: wins(cell(-0.06, -0.019, 140, 12)) }],
    },
  ],
};

export const HISTORY: PlaycallHistoryResponse = {
  status: 'ok',
  message: null,
  team: 'KC',
  side: 'offense',
  seasons: [2023, 2024, 2025],
  research: true,
  source_note: 'nflverse participation (research data): 2026 arrives about February 2027.',
  min_n: MIN_N,
  groups: [
    {
      id: 'personnel',
      title: 'Personnel',
      kind: 'stack',
      note: null,
      rows: [
        { ...metric({ metric: 'personnel_11', label: '11 personnel', short: '11', per: 'snap' }), seasons: { '2023': cell(0.6, 0.6, 1000), '2024': cell(0.58, 0.61, 1000), '2025': cell(0.58, 0.6, 1000) } },
        { ...metric({ metric: 'personnel_12', label: '12 personnel', short: '12', per: 'snap' }), seasons: { '2023': cell(0.25, 0.22, 1000), '2024': cell(0.3, 0.22, 1000), '2025': cell(0.31, 0.22, 1000) } },
      ],
    },
  ],
};

export const PLAY_CALLS: PlayCallsResponse = {
  ...META,
  ftn_waiting: [],
  week: 5,
  window: 'season',
  note: "Each offense's season rates next to what the other defense allows. The forecast arrives in PC02.",
  metrics: [PROE, PLAY_ACTION, BLITZ],
  games: [
    {
      game_id: '2026_05_KC_JAX',
      kickoff: '2026-10-11T17:00:00Z',
      away: 'KC',
      home: 'JAX',
      matchups: [
        {
          offense: 'KC',
          defense: 'JAX',
          rows: [
            { metric: 'proe', offense: cell(0.037, -0.019, 140), defense: cell(0.01, -0.019, 140), league: -0.019, shift: 0.8, same_way: true },
            { metric: 'play_action_rate', offense: cell(0.366, 0.241, 130), defense: cell(0.33, 0.241, 12), league: 0.241, shift: 2.1, same_way: true },
            { metric: 'blitz_rate', offense: cell(0.3, 0.311, 130), defense: cell(0.18, 0.311, 130), league: 0.311, shift: -2.4, same_way: false },
          ],
        },
        {
          offense: 'JAX',
          defense: 'KC',
          rows: [{ metric: 'proe', offense: cell(-0.05, -0.019, 140), defense: cell(-0.06, -0.019, 140), league: -0.019, shift: -1.0, same_way: true }],
        },
      ],
    },
  ],
  shifts: [
    { game_id: '2026_05_KC_JAX', offense: 'KC', defense: 'JAX', metric: 'play_action_rate', shift: 2.1, text: "KC's play-action meets JAX, which allows the league's second-most (33.0%, league 24.1%)." },
  ],
};

const NOT_BUILT_MESSAGE = 'No play-calling tables for 2026 yet: run `uv run nfl playcalling build --season 2026`.';

export const TEAMS_NOT_BUILT: PlaycallTeamsResponse = { ...TEAMS, status: 'not_built', message: NOT_BUILT_MESSAGE, seasons: [], as_of_week: null, through_week: null, ftn_waiting: [], columns: [], teams: [] };
export const TEAM_NOT_BUILT: PlaycallTeamResponse = { ...TEAM, status: 'not_built', message: NOT_BUILT_MESSAGE, seasons: [], summary: [], identity: [], situations: null, field: null, runs: [], weekly: null, next_game: null };
export const PLAY_CALLS_NOT_BUILT: PlayCallsResponse = { ...PLAY_CALLS, status: 'not_built', message: NOT_BUILT_MESSAGE, games: [], shifts: [] };
export const PLAY_CALLS_NOT_YET: PlayCallsResponse = {
  ...PLAY_CALLS,
  status: 'not_yet',
  week: 7,
  message: 'Week 7 is counted once week 6 is played and `nfl playcalling build --season 2026` runs.',
  games: [],
  shifts: [],
};
