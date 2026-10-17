// PC03 test fixtures: small SYNTHETIC answers in the shapes of web/src/api/types.ts (PC03 block).
// Never real dumps (no real plays, no Big Data Bowl data).

import type {
  PlayDiagram,
  PlayDiagramResponse,
  PlaySummary,
  PlaycallPlaysResponse,
  PlaycallPlaysWeek,
  RealClipResponse,
} from '../../api/types';

export const LABEL = 'Reconstructed from charting data, not player tracking';
export const REAL_LABEL = 'Real play: 2022 week 6, Big Data Bowl data';

const result = (o: Partial<PlaySummary['result']>): PlaySummary['result'] => ({
  kind: 'run',
  yards: 0,
  first_down: false,
  touchdown: false,
  turnover: false,
  fumble_lost: false,
  text: 'Run, 0',
  ...o,
});

export const play = (o: Partial<PlaySummary>): PlaySummary => ({
  game_id: '2026_02_IND_KC',
  play_id: 100,
  week: 2,
  offense: 'KC',
  defense: 'IND',
  quarter: 1,
  quarter_text: 'Q1',
  clock: '14:10',
  down: 1,
  distance: 10,
  down_text: '1st & 10',
  yards_to_goal: 75,
  spot: 'KC 25',
  type: 'pass',
  tags: [],
  result: result({}),
  epa: 0,
  explosive: false,
  charted: true,
  concept: null,
  desc: 'Synthetic play.',
  ...o,
});

export const PASS = play({
  play_id: 100,
  tags: ['play_action', 'deep_shot'],
  result: result({ kind: 'complete', yards: 24, first_down: true, text: 'Complete, +24 (22 air yards), first down' }),
  epa: 1.8,
  explosive: true,
  concept: 'play_action_deep',
  desc: '15-A.Passer pass deep middle to 87-B.Catcher to KC 49 for 24 yards.',
});
export const RUN = play({
  play_id: 120,
  clock: '13:30',
  down: 2,
  distance: 6,
  down_text: '2nd & 6',
  spot: 'KC 29',
  type: 'run',
  tags: ['motion'],
  result: result({ kind: 'run', yards: 4, text: 'Run, +4' }),
  epa: -0.12,
  concept: 'inside_zone',
  desc: '25-C.Runner up the middle to KC 33 for 4 yards.',
});
export const INC = play({
  play_id: 140,
  clock: '12:58',
  down: 3,
  distance: 2,
  down_text: '3rd & 2',
  spot: 'KC 33',
  type: 'pass',
  result: result({ kind: 'incomplete', yards: 0, text: 'Incomplete' }),
  epa: -1.1,
  charted: false,
  desc: '15-A.Passer pass incomplete short right.',
});

const WEEKS: PlaycallPlaysWeek[] = [
  { week: 1, game_id: '2026_01_KC_DEN', opponent: 'DEN', home: false, plays: 2, label: 'Week 1 · at DEN' },
  { week: 2, game_id: '2026_02_IND_KC', opponent: 'IND', home: true, plays: 3, label: 'Week 2 · vs IND' },
];

export const PLAYS: PlaycallPlaysResponse = {
  status: 'ok',
  message: null,
  season: 2026,
  seasons: [2026, 2025],
  team: 'KC',
  side: 'offense',
  label: LABEL,
  weeks: WEEKS,
  week: 2,
  game: WEEKS[1],
  filters: { down: null, type: null, result: null },
  options: {
    down: [1, 2, 3, 4],
    type: [
      { value: 'run', label: 'Runs' },
      { value: 'pass', label: 'Passes' },
    ],
    result: [
      { value: 'first_down', label: 'First downs' },
      { value: 'touchdown', label: 'Touchdowns' },
    ],
  },
  counts: { total: 3, shown: 3 },
  plays: [PASS, RUN, INC],
  library: false,
};

export const PLAYS_WEEK1: PlaycallPlaysResponse = {
  ...PLAYS,
  week: 1,
  game: WEEKS[0],
  counts: { total: 2, shown: 2 },
  plays: [
    play({ game_id: '2026_01_KC_DEN', play_id: 55, week: 1, defense: 'DEN', desc: 'Week one pass.' }),
    play({ game_id: '2026_01_KC_DEN', play_id: 75, week: 1, defense: 'DEN', type: 'run', desc: 'Week one run.' }),
  ],
};

export const PLAYS_RUNS: PlaycallPlaysResponse = { ...PLAYS, filters: { down: null, type: 'run', result: null }, counts: { total: 3, shown: 1 }, plays: [RUN] };
export const PLAYS_ZERO: PlaycallPlaysResponse = {
  ...PLAYS,
  filters: { down: 4, type: null, result: null },
  counts: { total: 3, shown: 0 },
  plays: [],
};
export const PLAYS_NOT_BUILT: PlaycallPlaysResponse = {
  ...PLAYS,
  status: 'not_built',
  message: 'No play-by-play tables for 2026 yet (`uv run nfl playcalling build --season 2026`).',
  weeks: [],
  week: null,
  game: null,
  counts: { total: 0, shown: 0 },
  plays: [],
};
export const PLAYS_NO_WEEK: PlaycallPlaysResponse = {
  ...PLAYS,
  status: 'no_week',
  message: 'KC has no plays in week 5 of 2026 (a bye, or not played yet).',
  week: null,
  game: null,
  counts: { total: 0, shown: 0 },
  plays: [],
};
export const PLAYS_DEFENSE: PlaycallPlaysResponse = {
  ...PLAYS,
  side: 'defense',
  counts: { total: 1, shown: 1 },
  plays: [play({ game_id: '2026_02_IND_KC', play_id: 300, offense: 'IND', defense: 'KC', spot: 'IND 40', desc: 'IND pass against KC.' })],
};
export const PLAYS_LIBRARY: PlaycallPlaysResponse = { ...PLAYS, library: true };

/** A pass: presnap 0, snap 0.8, throw 1.6, catch 2.4, end 2.8; one measured and one approximate receiver. */
export const DIAGRAM: PlayDiagram = {
  version: 1,
  kind: 'reconstruction',
  label: LABEL,
  play: { ...PASS, game_label: 'IND at KC, week 2', season: 2026 },
  field: { los: 25, first_down: 35, goal: 100, ball_y: 29.75, hash: 'right', width: 53.33, view: [13, 45] },
  players: [
    { id: 'o_c', side: 'offense', role: 'ol', label: 'C', approx: false, rusher: false, target: false, carrier: false, track: [[0, 24.4, 29.75]] },
    {
      id: 'o_qb',
      side: 'offense',
      role: 'qb',
      label: 'QB',
      approx: false,
      rusher: false,
      target: false,
      carrier: false,
      track: [
        [0, 24, 29.75],
        [0.8, 24, 29.75],
        [1.6, 18, 29.75],
      ],
    },
    {
      id: 'o_r1',
      side: 'offense',
      role: 'receiver',
      label: 'R',
      approx: true,
      rusher: false,
      target: true,
      carrier: true,
      track: [
        [0, 24.5, 46],
        [0.8, 24.5, 46],
        [2.4, 47, 26.67],
        [2.8, 49, 26.67],
      ],
    },
    { id: 'd_dl1', side: 'defense', role: 'dl', label: 'DL', approx: true, rusher: true, target: false, carrier: false, track: [[0, 26, 29], [0.8, 26, 29], [2, 19, 29.5]] },
    { id: 'd_cb1', side: 'defense', role: 'db', label: 'CB', approx: true, rusher: false, target: false, carrier: false, track: [[0, 31, 46]] },
  ],
  ball: [
    [0, 25, 29.75, 0],
    [0.8, 25, 29.75, 0],
    [1, 24, 29.75, 1],
    [1.6, 18, 29.75, 1],
    [2, 32, 28, 6],
    [2.4, 47, 26.67, 1],
    [2.8, 49, 26.67, 1],
  ],
  marks: [
    { kind: 'fake', points: [[24, 29.75], [21.5, 30.5]], t: 1, approx: true, text: 'Play-action fake' },
    { kind: 'throw', points: [[18, 29.75], [47, 26.67]], t: 1.6, approx: false, text: null },
    { kind: 'end', points: [[49, 26.67]], t: 2.8, approx: false, text: 'Complete, +24 (22 air yards), first down' },
  ],
  phases: [
    { name: 'presnap', t: 0, text: 'Before the snap: QB under center, ball on the right hash' },
    { name: 'snap', t: 0.8, text: 'Snap' },
    { name: 'throw', t: 1.6, text: 'Throw: 22 air yards, middle' },
    { name: 'catch', t: 2.4, text: 'Caught at the KC 47' },
    { name: 'end', t: 2.8, text: 'Complete, +24 (22 air yards), first down' },
  ],
  duration: 3.4,
  known: ['The ball on the right hash (FTN)', 'The throw: 22 air yards, deep middle (play-by-play)'],
  approximate: ['Where the receivers line up: generic spots'],
  unknown: ['Routes of the receivers who weren\'t targeted'],
  sources: ['nflverse play-by-play', 'FTN charting (via nflverse)'],
  concept: 'play_action_deep',
  real_example: null,
};
export const DIAGRAM_RES: PlayDiagramResponse = { status: 'ok', diagram: DIAGRAM };
export const DIAGRAM_WITH_REAL: PlayDiagramResponse = {
  status: 'ok',
  diagram: { ...DIAGRAM, real_example: { concept: 'play_action_deep', name: 'Play-action deep shot', match: 'a play-action throw 20+ air yards', count: 2 } },
};
/** A run: a lane at the line and an approximate run path; goal to go (no first-down line). */
export const DIAGRAM_RUN: PlayDiagramResponse = {
  status: 'ok',
  diagram: {
    ...DIAGRAM,
    play: { ...RUN, game_label: 'IND at KC, week 2', season: 2026 },
    field: { ...DIAGRAM.field, first_down: null, hash: null },
    marks: [
      { kind: 'handoff', points: [[23, 29]], t: 1.2, approx: true, text: 'Handoff' },
      { kind: 'lane', points: [[24.5, 26.67], [26.5, 26.67]], t: 1.6, approx: false, text: 'Run up the middle' },
      { kind: 'run', points: [[26.5, 26.67], [29, 26.67]], t: 1.6, approx: true, text: null },
      { kind: 'end', points: [[29, 26.67]], t: 2.2, approx: false, text: 'Run, +4' },
    ],
    phases: [
      { name: 'presnap', t: 0, text: 'Before the snap' },
      { name: 'snap', t: 0.8, text: 'Snap' },
      { name: 'handoff', t: 1.2, text: 'Handoff' },
      { name: 'end', t: 2.2, text: 'Run, +4' },
    ],
    duration: 2.8,
    concept: 'inside_zone',
  },
};

export const CLIP: RealClipResponse = {
  status: 'ok',
  message: null,
  concept: 'play_action_deep',
  name: 'Play-action deep shot',
  match: 'a play-action throw 20+ air yards',
  note: 'NFL Big Data Bowl data (Kaggle): non-commercial use, kept on this computer.',
  examples: [
    { index: 0, label: '2022 week 6 · AAA vs BBB · 1st & 10' },
    { index: 1, label: '2022 week 7 · CCC vs DDD · 2nd & 5' },
  ],
  index: 0,
  diagram: {
    ...DIAGRAM,
    kind: 'real',
    label: REAL_LABEL,
    players: DIAGRAM.players.map((p) => ({ ...p, approx: false })),
    known: ["Every player's position, 10 times a second (tracking)"],
    approximate: [],
    unknown: [],
    sources: ['NFL Big Data Bowl'],
    real_example: null,
  },
};
export const CLIP_2: RealClipResponse = { ...CLIP, index: 1, diagram: { ...CLIP.diagram!, label: 'Real play: 2022 week 7, Big Data Bowl data' } };
export const CLIP_NO_LIBRARY: RealClipResponse = {
  ...CLIP,
  status: 'no_library',
  message: "The Big Data Bowl library isn't built on this computer (`uv run nfl playcalling library`, after the data is downloaded).",
  examples: [],
  index: null,
  diagram: null,
};
