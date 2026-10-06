import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { applyAppearance, loadAppearance, saveAppearance, type Appearance } from './appearance';

interface AppearanceContextValue {
  appearance: Appearance;
  setAppearance: (a: Appearance) => void;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [appearance, setState] = useState<Appearance>(loadAppearance);

  const setAppearance = useCallback((a: Appearance) => {
    saveAppearance(a);
    applyAppearance(a);
    setState(a);
  }, []);

  // "System" follows the OS setting live.
  useEffect(() => {
    applyAppearance(appearance);
    if (appearance.mode !== 'system') return;
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia('(prefers-color-scheme: dark)');
    } catch {
      return;
    }
    const onChange = () => applyAppearance(appearance);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [appearance]);

  const value = useMemo(() => ({ appearance, setAppearance }), [appearance, setAppearance]);
  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAppearance(): AppearanceContextValue {
  const ctx = useContext(AppearanceContext);
  if (!ctx) throw new Error('useAppearance needs <AppearanceProvider>');
  return ctx;
}
