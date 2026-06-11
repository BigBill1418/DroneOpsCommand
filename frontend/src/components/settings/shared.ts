/**
 * Shared constants for the Settings tab subtrees (P2-5 extraction).
 *
 * The monolithic `Settings.tsx` (2715 lines) mounted 13 forms and fired ~19
 * parallel GETs in a single mount `useEffect`. It is now a thin shell that
 * lazy-mounts one tab component at a time (`keepMounted={false}`), so only the
 * active tab fetches + renders. These constants are the bits every tab needs;
 * they are copied verbatim from the original file — no visual change.
 */

export const tabStyles = {
  tab: {
    color: '#5a6478',
    fontFamily: "'Share Tech Mono', monospace",
    letterSpacing: '1px',
    fontSize: '12px',
    '&[data-active]': { color: '#00d4ff', borderColor: '#00d4ff' },
  },
  list: { borderColor: '#1a1f2e' },
};
