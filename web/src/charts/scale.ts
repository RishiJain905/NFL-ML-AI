/** Round tick values covering [lo, hi] with about `n` steps (1 / 2 / 2.5 / 5 × 10^k). */
export function niceTicks(lo: number, hi: number, n = 4): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  }
  const raw = (hi - lo) / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const start = Math.floor(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** Index of the x position nearest `vx` (in viewBox units) for `n` evenly spaced points. */
export function nearestIndex(vx: number, left: number, width: number, n: number): number {
  if (n <= 1 || width <= 0 || !Number.isFinite(vx)) return 0;
  const i = Math.round(((vx - left) / width) * (n - 1));
  return Math.max(0, Math.min(n - 1, i));
}
