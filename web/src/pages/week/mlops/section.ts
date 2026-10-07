// Which MLOps section is open, remembered while moving between weeks and tabs. The module
// variable survives a tab switch; sessionStorage survives a reload of the page. Both are
// conveniences: the page renders correctly when storage is blocked.

import type { MlopsSection } from '../../../api/client';

const KEY = 'cr.mlopsSection';
const SECTIONS: readonly string[] = ['health', 'wandb', 'artifacts'];

let memory: MlopsSection = 'health';

export function readSection(): MlopsSection {
  try {
    const v = window.sessionStorage.getItem(KEY);
    if (v && SECTIONS.includes(v)) memory = v as MlopsSection;
  } catch {
    /* storage blocked: keep the in-memory choice */
  }
  return memory;
}

export function rememberSection(section: MlopsSection): void {
  memory = section;
  try {
    window.sessionStorage.setItem(KEY, section);
  } catch {
    /* ignore */
  }
}

/** Tests only. */
export function resetSection(): void {
  memory = 'health';
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
