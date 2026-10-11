import { config } from '@/config';
import type { ApiErrorBody, ApiErrorCode, Plan } from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | 'network_error' | 'unknown';
  readonly requiredPlan?: Plan;
  /** Whether the failing request carried a bearer token (401 without one is just a bad login). */
  readonly hadToken: boolean;

  constructor(opts: { status: number; code: ApiError['code']; message: string; requiredPlan?: Plan; hadToken: boolean }) {
    super(opts.message);
    this.name = 'ApiError';
    this.status = opts.status;
    this.code = opts.code;
    this.requiredPlan = opts.requiredPlan;
    this.hadToken = opts.hadToken;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

let authToken: string | null = null;
export function setAuthToken(token: string | null) {
  authToken = token;
}
export function getAuthToken() {
  return authToken;
}

type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryValue>;

export function buildQuery(params?: QueryParams): string {
  if (!params) return '';
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

interface RequestOptions {
  query?: QueryParams;
  body?: unknown;
  signal?: AbortSignal;
  /** Response format; defaults to JSON. */
  as?: 'json' | 'text';
}

const KNOWN_CODES: ApiErrorCode[] = [
  'invalid_request',
  'unauthorized',
  'upgrade_required',
  'not_found',
  'conflict',
  'ai_declined',
  'quota_exceeded',
  'rate_limited',
  'ai_unavailable',
  'billing_unavailable',
];

async function toApiError(res: Response, hadToken: boolean): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null = null;
  try {
    body = (await res.json()) as Partial<ApiErrorBody>;
  } catch {
    body = null;
  }
  const rawCode = body?.error?.code;
  const code = KNOWN_CODES.includes(rawCode as ApiErrorCode) ? (rawCode as ApiErrorCode) : 'unknown';
  return new ApiError({
    status: res.status,
    code,
    message: body?.error?.message ?? `Request failed (${res.status})`,
    requiredPlan: body?.requiredPlan,
    hadToken,
  });
}

export async function request<T>(method: 'GET' | 'POST' | 'DELETE', path: string, opts: RequestOptions = {}): Promise<T> {
  const token = authToken;
  const headers: Record<string, string> = { Accept: opts.as === 'text' ? 'text/csv, */*' : 'application/json' };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${config.API_BASE}${path}${buildQuery(opts.query)}`, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as { name?: string })?.name === 'AbortError') throw e;
    throw new ApiError({
      status: 0,
      code: 'network_error',
      message: `Can't reach the ${config.APP_NAME} server. Check your connection.`,
      hadToken: !!token,
    });
  }

  if (!res.ok) throw await toApiError(res, !!token);
  if (res.status === 204) return undefined as T;
  if (opts.as === 'text') return (await res.text()) as T;
  return (await res.json()) as T;
}

/** Human-friendly message for any error thrown by the API layer. */
export function errorMessage(e: unknown): string {
  if (isApiError(e)) {
    switch (e.code) {
      case 'quota_exceeded':
        return "You've used today's AI quota. It resets tomorrow, or upgrade for more.";
      case 'ai_declined':
        return "HillSignal AI couldn't help with that. Try rephrasing your question.";
      case 'rate_limited':
        return 'Too many requests. Wait a moment and try again.';
      case 'ai_unavailable':
        return 'AI features are temporarily unavailable. Try again later.';
      case 'billing_unavailable':
        return "Purchases can't be verified right now. You won't lose your purchase; try Restore purchases later.";
      case 'upgrade_required':
        return e.message || 'This feature needs a higher plan.';
      case 'not_found':
        return 'Not found.';
      default:
        return e.message;
    }
  }
  if (e instanceof Error) return e.message;
  return 'Something went wrong.';
}
