import type { Plan } from '@/api/types';

/** Tiny typed event bus to decouple the API layer from navigation and auth. */
type EventMap = {
  unauthorized: void;
  upgradeRequired: { requiredPlan?: Plan; reason?: string };
};

type Listener<T> = (payload: T) => void;
const listeners = new Map<keyof EventMap, Set<Listener<never>>>();

export function on<K extends keyof EventMap>(event: K, fn: Listener<EventMap[K]>): () => void {
  let set = listeners.get(event);
  if (!set) listeners.set(event, (set = new Set()));
  set.add(fn as Listener<never>);
  return () => {
    set.delete(fn as Listener<never>);
  };
}

export function emit<K extends keyof EventMap>(event: K, payload: EventMap[K]): void {
  listeners.get(event)?.forEach((fn) => (fn as Listener<EventMap[K]>)(payload));
}
