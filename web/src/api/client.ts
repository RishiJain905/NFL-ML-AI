import { useQuery } from '@tanstack/react-query';
import type { ApiErrorBody, Meta, WeeksResponse } from './types';

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { accept: 'application/json' } });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (body as ApiErrorBody | null)?.error;
    throw new ApiError(res.status, err?.code ?? `http_${res.status}`, err?.message ?? res.statusText);
  }
  return body as T;
}

let tokenPromise: Promise<string> | null = null;

/** The per-launch token every state-changing request needs (README §5.2). */
export function launchToken(): Promise<string> {
  tokenPromise ??= apiGet<{ token: string }>('/api/session').then((r) => r.token);
  return tokenPromise;
}

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: () => apiGet<Meta>('/api/meta'), refetchInterval: 30_000 });
}

export function useWeeks() {
  return useQuery({
    queryKey: ['weeks'],
    queryFn: () => apiGet<WeeksResponse>('/api/weeks'),
    refetchInterval: 30_000,
  });
}
