export type ArtworkStatus = { state: "loading" | "ready" | "retrying" | "failed"; message: string };
export const MAX_ARTWORK_RETRIES = 3;

export function artworkRetryDelay(attempt: number, response?: Response, data?: any, now = Date.now()): number | null {
  if (attempt >= MAX_ARTWORK_RETRIES || data?.retryable === false) return null;
  if (response && [400, 401, 403, 404, 410, 422].includes(response.status)) return null;
  const header = response?.headers.get("Retry-After");
  const seconds = header && /^\d+$/.test(header) ? Number(header) * 1000
    : header ? Date.parse(header) - now : 0;
  const server = Number(data?.retryAfterSeconds || 0) * 1000;
  const delay = Math.max([2000, 5000, 15000][attempt], Number.isFinite(seconds) ? seconds : 0, Number.isFinite(server) ? server : 0);
  // An unreasonable remote delay ends automatic retries instead of retrying early.
  return delay <= 86400000 ? delay : null;
}
