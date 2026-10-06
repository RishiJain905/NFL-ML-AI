// The run controls' shared state and the toaster (mockup: `toast`). Mounted once in Shell.
// Toasts stack (at most three), failures get a red left border, and each one goes after ~6 s.

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { newRunUi, RunUiContext, type ToastInput } from './runUi';
import './run.css';

const TOAST_MS = 6000;
const MAX_TOASTS = 3;

interface ShownToast extends ToastInput {
  id: number;
}

export function RunUiProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ShownToast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);
  const toast = useCallback(
    (t: ToastInput) => {
      const id = nextId.current++;
      setToasts((ts) => [...ts, { ...t, id }].slice(-MAX_TOASTS));
      const timer = setTimeout(() => {
        timers.current.delete(timer);
        dismiss(id);
      }, TOAST_MS);
      timers.current.add(timer);
    },
    [dismiss],
  );
  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((t) => clearTimeout(t));
  }, []);

  // the session's sets live as long as the provider; only `toast` is swapped in
  const [base] = useState(() => newRunUi());
  const value = useMemo(() => ({ ...base, toast }), [base, toast]);

  return (
    <RunUiContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite" aria-label="Notifications">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone ?? 'ok'}`}>
            <b>{t.title}</b>
            {t.text ? <span>{t.text}</span> : null}
            <button type="button" className="x" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </RunUiContext.Provider>
  );
}
