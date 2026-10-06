// Watches the current run (mounted once in Shell). When a run this page followed ends (seen
// running, or launched from here), it refetches everything the run may have written (sidebar
// badge, header, every tab) and says so with a toast and a browser notification. A run that had
// already ended before the page opened is history, not news: no toast.

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { invalidateAfterRun, useRunCurrent } from '../../api/client';
import type { RunCurrent } from '../../api/types';
import { notify } from './notify';
import { followed, markSeen, runEndToast, runKey, useRunUi } from './runUi';

export function RunWatcher() {
  const q = useRunCurrent();
  const qc = useQueryClient();
  const ui = useRunUi();
  const prev = useRef<RunCurrent | null>(null);
  const handled = useRef(new Set<string>());

  useEffect(() => {
    const d = q.data;
    if (!d) return;
    const before = prev.current;
    prev.current = d;
    const run = d.run;
    if (d.state === 'running') {
      if (run) markSeen(ui, run);
      return;
    }
    if (d.state === 'finished' && run) {
      const key = runKey(run);
      if (handled.current.has(key) || !followed(ui, run)) return;
      handled.current.add(key);
      invalidateAfterRun(qc);
      // an injury update followed on its week page is announced there (with `material`)
      if (ui.announced.has(key) || ui.claimed.has(key)) return;
      ui.announced.add(key);
      const t = runEndToast(run);
      ui.toast(t);
      notify(t.title, t.text);
      return;
    }
    // idle after running: a run the app didn't launch (a terminal) ended; refresh quietly
    if (before?.state === 'running') invalidateAfterRun(qc);
  }, [q.data, qc, ui]);

  return null;
}
