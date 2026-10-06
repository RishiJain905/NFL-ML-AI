// The week tabs, in the mockup's order (D96).

export const TABS = [
  ['pipeline', 'Pipeline'],
  ['digest', 'Digest'],
  ['games', 'Games'],
  ['players', 'Players'],
  ['results', 'Results'],
  ['mlops', 'MLOps'],
  ['graph', 'Graph'],
] as const;
export type TabKey = (typeof TABS)[number][0];
