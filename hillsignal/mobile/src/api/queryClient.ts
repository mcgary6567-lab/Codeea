import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';
import { emit } from '@/lib/events';
import { isApiError } from './client';

/**
 * Global API error policy:
 * - 401 on an authenticated request → sign out.
 * - 402 from a user action (mutation) → open the paywall.
 *   402 from a background query is rendered in place as a locked state instead,
 *   so the app never yanks the user into a modal while they are just scrolling.
 * - 429 / 503 / others are shown inline by the screen that made the call.
 */
function handleGlobal(error: unknown, source: 'query' | 'mutation') {
  if (!isApiError(error)) return;
  if (error.status === 401 && error.hadToken) emit('unauthorized', undefined);
  if (error.status === 402 && source === 'mutation') emit('upgradeRequired', { requiredPlan: error.requiredPlan });
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: (e) => handleGlobal(e, 'query') }),
  mutationCache: new MutationCache({ onError: (e) => handleGlobal(e, 'mutation') }),
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      retry: (count, error) => {
        if (isApiError(error) && error.status >= 400 && error.status < 500) return false;
        return count < 2;
      },
    },
    mutations: { retry: false },
  },
});
