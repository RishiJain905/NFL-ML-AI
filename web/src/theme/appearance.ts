// Theme and mode (D96): Turf & Pylon or Playbook, each dark / light, or "system" (follows the
// OS). Saved in this browser only; every storage call is wrapped, so a blocked localStorage
// just means the defaults.

export type Palette = 'turf' | 'playbook';
export type Mode = 'system' | 'dark' | 'light';
export interface Appearance {
  palette: Palette;
  mode: Mode;
}

export const PALETTES: { id: Palette; name: string; note: string; swatch: [string, string, string] }[] = [
  { id: 'turf', name: 'Turf & Pylon', note: 'field green, end-zone pylon orange', swatch: ['#0D1B14', '#FF6B2C', '#4FD98F'] },
  { id: 'playbook', name: 'Playbook', note: 'whiteboard by day, chalkboard by night', swatch: ['#FFFFFF', '#1F5FD1', '#CC2F2F'] },
];
export const MODES: Mode[] = ['system', 'dark', 'light'];
export const DEFAULT_APPEARANCE: Appearance = { palette: 'turf', mode: 'system' };
const KEY = 'cr.appearance';

export function loadAppearance(): Appearance {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<Appearance>;
    return {
      palette: PALETTES.some((p) => p.id === raw.palette) ? (raw.palette as Palette) : DEFAULT_APPEARANCE.palette,
      mode: MODES.includes(raw.mode as Mode) ? (raw.mode as Mode) : DEFAULT_APPEARANCE.mode,
    };
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
}

export function saveAppearance(a: Appearance): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(a));
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
}

export function systemPrefersDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  } catch {
    return true;
  }
}

export function resolvedMode(mode: Mode): 'dark' | 'light' {
  return mode === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : mode;
}

export function applyAppearance(a: Appearance): void {
  const root = document.documentElement;
  root.dataset.pal = a.palette;
  root.dataset.mode = resolvedMode(a.mode);
}
