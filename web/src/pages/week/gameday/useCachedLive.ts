// The tab label's "N live" (LD02): read from the list already in the cache, never fetched here,
// so other tabs and WeekPage never call ESPN.

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useSyncExternalStore } from 'react';
import type { LiveGamesResponse } from '../../../api/types';
import { liveGamesKey } from './model';

export function useCachedLiveCount(season: number, week: number): number {
  const qc = useQueryClient();
  const subscribe = useCallback((onChange: () => void) => qc.getQueryCache().subscribe(onChange), [qc]);
  const read = () => {
    const d = qc.getQueryData<LiveGamesResponse>(liveGamesKey(season, week));
    return d ? d.games.filter((g) => g.state === 'in').length : 0;
  };
  return useSyncExternalStore(subscribe, read, read);
}
