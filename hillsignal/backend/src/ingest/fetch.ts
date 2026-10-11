const UA = "HillSignal/0.1 (+https://hillsignal.app; public disclosure ingestion)";

/** fetch with a polite UA, timeout, and a couple of retries on network errors / 5xx. */
export async function politeFetch(url: string, init: RequestInit = {}, attempts = 3): Promise<Response> {
  let lastErr: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(url, {
        ...init,
        headers: { "User-Agent": UA, ...(init.headers ?? {}) },
        signal: AbortSignal.timeout(60_000),
      });
      if (res.status >= 500) throw new Error(`HTTP ${res.status} for ${url}`);
      return res;
    } catch (err) {
      lastErr = err;
      await new Promise((r) => setTimeout(r, 1000 * 2 ** i));
    }
  }
  throw lastErr;
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
