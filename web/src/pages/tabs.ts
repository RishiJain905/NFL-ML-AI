// The week tabs, in the mockup's order (D96); Game day comes after Graph (LD02, D108), Play calls
// after Game day (PC01, D108), Availability and Outcomes after Play calls (FL02, D158).

export const TABS = [
  ['pipeline', 'Pipeline'],
  ['digest', 'Digest'],
  ['games', 'Games'],
  ['players', 'Players'],
  ['results', 'Results'],
  ['mlops', 'MLOps'],
  ['graph', 'Graph'],
  ['game-day', 'Game day'],
  ['play-calls', 'Play calls'],
  ['availability', 'Availability'],
  ['outcomes', 'Outcomes'],
] as const;
export type TabKey = (typeof TABS)[number][0];
