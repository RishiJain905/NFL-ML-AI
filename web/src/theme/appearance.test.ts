import { describe, expect, it } from 'vitest';
import { setSystemDark } from '../test/setup';
import { applyAppearance, loadAppearance, resolvedMode, saveAppearance } from './appearance';

describe('appearance', () => {
  it('defaults to Turf & Pylon following the system', () => {
    expect(loadAppearance()).toEqual({ palette: 'turf', mode: 'system' });
  });

  it('saves and loads a choice', () => {
    saveAppearance({ palette: 'playbook', mode: 'light' });
    expect(loadAppearance()).toEqual({ palette: 'playbook', mode: 'light' });
  });

  it('ignores junk in storage', () => {
    localStorage.setItem('cr.appearance', '{"palette":"floodlight","mode":"neon"}');
    expect(loadAppearance()).toEqual({ palette: 'turf', mode: 'system' });
    localStorage.setItem('cr.appearance', 'not json');
    expect(loadAppearance()).toEqual({ palette: 'turf', mode: 'system' });
  });

  it('resolves system mode from the OS and applies it to <html>', () => {
    setSystemDark(false);
    expect(resolvedMode('system')).toBe('light');
    setSystemDark(true);
    expect(resolvedMode('system')).toBe('dark');
    applyAppearance({ palette: 'playbook', mode: 'light' });
    expect(document.documentElement.dataset.pal).toBe('playbook');
    expect(document.documentElement.dataset.mode).toBe('light');
  });
});
