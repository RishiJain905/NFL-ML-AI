// The week tabs, in the mockup's order (D96); Game day comes after Graph (LD02, D108).

export const TABS = [
  ['pipeline', 'Pipeline'],
  ['digest', 'Digest'],
  ['games', 'Games'],
  ['players', 'Players'],
  ['results', 'Results'],
  ['mlops', 'MLOps'],
  ['graph', 'Graph'],
  ['game-day', 'Game day'],
] as const;
export type TabKey = (typeof TABS)[number][0];
