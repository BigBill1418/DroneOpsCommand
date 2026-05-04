/**
 * useDirtyGuard — shared unsaved-changes guard for facet editors
 * (v2.67.3 polish).
 *
 * Two layers of protection:
 *
 *   1. In-app navigation (Cancel button + any Hub-back link the editor
 *      renders): the editor calls `guardedNavigate(target)` instead of
 *      `navigate(target)` directly. When `isDirty` is true the hook
 *      stashes the target and flips `showConfirm` so the editor's own
 *      <Modal> renders. On confirm the editor calls
 *      `confirmAndNavigate()`; on cancel the editor calls
 *      `setShowConfirm(false)`.
 *
 *   2. Browser-level navigation (tab close, hard refresh, back/forward
 *      to a non-SPA page): a `beforeunload` listener registers while
 *      `isDirty` is true so the browser shows its native "Leave site?"
 *      prompt. This is the only protection that works for closes /
 *      hard refreshes — React Router can't intercept those.
 *
 * NOTE: react-router-dom@6.28 with `BrowserRouter` (not the data
 * router) does not expose `useBlocker`. So in-app SPA-navigation
 * elsewhere — e.g. clicking the sidebar `MISSIONS` link while a facet
 * editor is dirty — is NOT intercepted by Layer 1. Layer 2's
 * beforeunload doesn't fire on intra-SPA navigation either. The
 * shipped surface area is the editor's own Cancel/Done/back buttons
 * (Layer 1) plus tab close / hard refresh / external link (Layer 2).
 * This is the same compromise the ADR-0014 facet editors already
 * accept everywhere — see App.tsx for the BrowserRouter mount.
 */
import { useCallback, useEffect, useState } from 'react';

export interface UseDirtyGuardResult {
  /** True while the confirm modal should be rendered. */
  showConfirm: boolean;
  /** Manual setter — used by Modal's onClose / Keep-Editing button. */
  setShowConfirm: (v: boolean) => void;
  /**
   * Replacement for direct `navigate()` calls. If `isDirty` is true,
   * stashes `target` and shows the confirm modal. If clean, runs the
   * navigation immediately via the supplied `navigate` callback.
   */
  guardedNavigate: (target: string) => void;
  /**
   * Called by the modal's "Discard changes" button. Performs the
   * stashed navigation and clears state.
   */
  confirmAndNavigate: () => void;
}

export interface UseDirtyGuardOptions {
  /** Current dirty state — recomputed on every render by the caller. */
  isDirty: boolean;
  /**
   * Imperative navigate function (typically from `useNavigate()`).
   * Passed in (not imported) so the hook works in tests that mock
   * react-router-dom without the hook re-importing it.
   */
  navigate: (target: string) => void;
}

export function useDirtyGuard(opts: UseDirtyGuardOptions): UseDirtyGuardResult {
  const { isDirty, navigate } = opts;
  const [showConfirm, setShowConfirm] = useState(false);
  const [pendingTarget, setPendingTarget] = useState<string | null>(null);

  // Layer 2 — browser-level protection. Only registered while dirty so
  // a clean editor doesn't get a false-positive beforeunload prompt
  // when the operator simply closes the tab.
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Some browsers require returnValue to be set explicitly.
      // The string is ignored; modern browsers show their generic
      // "Leave site?" prompt regardless.
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);

  const guardedNavigate = useCallback(
    (target: string) => {
      if (isDirty) {
        setPendingTarget(target);
        setShowConfirm(true);
        return;
      }
      navigate(target);
    },
    [isDirty, navigate],
  );

  const confirmAndNavigate = useCallback(() => {
    const target = pendingTarget;
    setShowConfirm(false);
    setPendingTarget(null);
    if (target !== null) navigate(target);
  }, [pendingTarget, navigate]);

  return { showConfirm, setShowConfirm, guardedNavigate, confirmAndNavigate };
}
