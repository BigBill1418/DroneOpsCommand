/**
 * useApiCache — minimal client-side TTL cache for read-only `api.get()` calls.
 *
 * FIX-4 (v2.63.10) — third+fourth fix in the 2026-04-24 perf audit. The
 * audit's BEFORE-state showed Dashboard / Flights / Settings re-fetched
 * every endpoint on every mount, multiplying the same axios round-trips
 * across navigation. This hook replaces hand-rolled
 * `useState + useEffect + api.get` triples with a deduplicated, TTL-cached,
 * mutation-invalidatable hook.
 *
 * Design choices (per ADR-0005 §FIX-4):
 *  - Cache key = the URL string. Same URL across components shares one
 *    network round-trip (request deduplication).
 *  - TTL default = 30 seconds. Tunable per call site via `ttlMs`.
 *  - `invalidate(prefix)` is exported for mutations: after a POST/PUT/DELETE
 *    that affects a list, call `invalidate('/missions')` to drop all cached
 *    queries whose URL starts with that prefix and notify subscribers.
 *  - Failure mode: on network error, `data` stays at the last cached value
 *    (or null) and `error` reflects the failure. Errors do NOT poison the
 *    cache — the next refetch retries cleanly.
 *  - No new dependencies. ~80 lines.
 *
 * Why not TanStack Query: it adds ~14 KB gzipped and partially undoes
 * FIX-3's bundle gains. The surface area we care about is small (3 pages).
 */

import { useEffect, useState, useRef, useCallback } from 'react';
import api from '../api/client';

interface CacheEntry<T> {
  data: T;
  expiresAt: number;
}

const cache = new Map<string, CacheEntry<unknown>>();
const inflight = new Map<string, Promise<unknown>>();
const subscribers = new Map<string, Set<() => void>>();

/**
 * Drop every cache entry whose key starts with `prefix`, then notify any
 * mounted hooks subscribed to those keys so they refetch.
 *
 * Use after a successful mutation that changes the underlying resource.
 * Example: after `await api.post('/aircraft', body); invalidate('/aircraft');`
 */
export function invalidate(prefix: string): void {
  if (!prefix) return;
  const dropped: string[] = [];
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) dropped.push(key);
  }
  for (const key of dropped) {
    cache.delete(key);
    const subs = subscribers.get(key);
    if (subs) {
      for (const fn of subs) fn();
    }
  }
}

interface UseApiCacheOptions {
  ttlMs?: number;
  /** Skip the GET entirely when false. Useful when params aren't ready. */
  enabled?: boolean;
}

interface UseApiCacheResult<T> {
  data: T | null;
  loading: boolean;
  error: unknown;
  refetch: () => void;
}

/**
 * Subscribe to a TTL-cached GET. Identical URLs across components share
 * one round-trip. Mutations call `invalidate(prefix)` to push fresh data.
 */
export function useApiCache<T>(
  url: string | null,
  options: UseApiCacheOptions = {},
): UseApiCacheResult<T> {
  const ttl = options.ttlMs ?? 30_000;
  const enabled = options.enabled ?? true;
  const [, force] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const key = url ?? '';
  const entry = key ? (cache.get(key) as CacheEntry<T> | undefined) : undefined;
  const fresh = !!entry && entry.expiresAt > Date.now();

  // Subscribe so external invalidate() triggers a re-render here.
  useEffect(() => {
    if (!key) return;
    if (!subscribers.has(key)) subscribers.set(key, new Set());
    const fn = () => { if (mountedRef.current) force((n) => n + 1); };
    subscribers.get(key)!.add(fn);
    return () => { subscribers.get(key)?.delete(fn); };
  }, [key]);

  const refetch = useCallback(() => {
    if (!url || !enabled) return;
    cache.delete(url);
    if (!inflight.has(url)) {
      const p = api.get<T>(url)
        .then((r) => {
          cache.set(url, { data: r.data, expiresAt: Date.now() + ttl });
          inflight.delete(url);
          const subs = subscribers.get(url);
          if (subs) for (const fn of subs) fn();
          if (mountedRef.current) setError(null);
          return r.data;
        })
        .catch((e) => {
          inflight.delete(url);
          if (mountedRef.current) setError(e);
          throw e;
        });
      inflight.set(url, p);
    }
  }, [url, ttl, enabled]);

  useEffect(() => {
    if (url && enabled && !fresh && !inflight.has(url)) {
      refetch();
    }
    // intentionally exclude `refetch` from deps — its identity already
    // depends on url/ttl/enabled, included below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, ttl, enabled, fresh]);

  return {
    data: entry?.data ?? null,
    loading: !!url && enabled && !fresh,
    error,
    refetch,
  };
}

/**
 * Drop every cached entry. Used by Logout. Avoid in normal flows;
 * prefer `invalidate(prefix)` for surgical invalidation.
 */
export function clearAllCache(): void {
  for (const key of cache.keys()) {
    const subs = subscribers.get(key);
    if (subs) for (const fn of subs) fn();
  }
  cache.clear();
}
