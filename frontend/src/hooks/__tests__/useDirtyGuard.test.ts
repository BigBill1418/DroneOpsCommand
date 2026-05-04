/**
 * Unit tests for useDirtyGuard — the shared facet-editor unsaved-
 * changes guard hook (v2.67.3 polish).
 *
 * The hook is the load-bearing piece across all 5 facet editors.
 * Verifying it in isolation lets the per-editor tests focus on the
 * editor's own wiring (modal renders, navigate target, save resets
 * dirty) rather than re-asserting the hook's semantics 5 times.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import { useDirtyGuard } from '../useDirtyGuard';

describe('useDirtyGuard', () => {
  let navigate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    navigate = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('navigates immediately when isDirty=false', () => {
    const { result } = renderHook(() =>
      useDirtyGuard({ isDirty: false, navigate }),
    );

    act(() => {
      result.current.guardedNavigate('/missions/abc');
    });

    expect(navigate).toHaveBeenCalledWith('/missions/abc');
    expect(result.current.showConfirm).toBe(false);
  });

  it('shows the confirm modal and stashes the target when isDirty=true', () => {
    const { result } = renderHook(() =>
      useDirtyGuard({ isDirty: true, navigate }),
    );

    act(() => {
      result.current.guardedNavigate('/missions/abc');
    });

    expect(navigate).not.toHaveBeenCalled();
    expect(result.current.showConfirm).toBe(true);
  });

  it('confirmAndNavigate fires the stashed target and clears state', () => {
    const { result } = renderHook(() =>
      useDirtyGuard({ isDirty: true, navigate }),
    );

    act(() => {
      result.current.guardedNavigate('/missions/abc');
    });
    expect(result.current.showConfirm).toBe(true);

    act(() => {
      result.current.confirmAndNavigate();
    });

    expect(navigate).toHaveBeenCalledWith('/missions/abc');
    expect(result.current.showConfirm).toBe(false);
  });

  it('setShowConfirm(false) (Keep Editing) does NOT navigate', () => {
    const { result } = renderHook(() =>
      useDirtyGuard({ isDirty: true, navigate }),
    );

    act(() => {
      result.current.guardedNavigate('/missions/abc');
    });
    expect(result.current.showConfirm).toBe(true);

    act(() => {
      result.current.setShowConfirm(false);
    });

    expect(navigate).not.toHaveBeenCalled();
    expect(result.current.showConfirm).toBe(false);
  });

  it('registers a beforeunload listener while dirty and removes it when clean', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    const removeSpy = vi.spyOn(window, 'removeEventListener');

    const { rerender, unmount } = renderHook(
      ({ isDirty }: { isDirty: boolean }) =>
        useDirtyGuard({ isDirty, navigate }),
      { initialProps: { isDirty: false } },
    );

    // Clean — no listener.
    expect(
      addSpy.mock.calls.some((c) => c[0] === 'beforeunload'),
    ).toBe(false);

    // Goes dirty — listener attached.
    rerender({ isDirty: true });
    expect(
      addSpy.mock.calls.some((c) => c[0] === 'beforeunload'),
    ).toBe(true);

    // Unmount while dirty — cleanup runs.
    unmount();
    expect(
      removeSpy.mock.calls.some((c) => c[0] === 'beforeunload'),
    ).toBe(true);
  });

  it('beforeunload handler calls preventDefault and sets returnValue', () => {
    const captured: EventListener[] = [];
    const originalAdd = window.addEventListener.bind(window);
    vi.spyOn(window, 'addEventListener').mockImplementation((type, handler) => {
      if (type === 'beforeunload') {
        captured.push(handler as EventListener);
      }
      return originalAdd(type as never, handler as never);
    });

    renderHook(() => useDirtyGuard({ isDirty: true, navigate }));

    expect(captured.length).toBeGreaterThan(0);
    const handler = captured[0];

    const event = {
      preventDefault: vi.fn(),
      returnValue: undefined as unknown as string,
    } as unknown as BeforeUnloadEvent;

    handler(event);

    expect(event.preventDefault).toHaveBeenCalled();
    expect(event.returnValue).toBe('');
  });
});
