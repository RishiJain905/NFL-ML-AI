import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
  delete document.documentElement.dataset.pal;
  delete document.documentElement.dataset.mode;
});

// jsdom has neither matchMedia nor ResizeObserver (Radix's popover measures its trigger).
let prefersDark = true;
export function setSystemDark(dark: boolean) {
  prefersDark = dark;
}
window.matchMedia = (query: string) =>
  ({
    matches: query.includes('dark') ? prefersDark : false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver ??= NoopResizeObserver;
