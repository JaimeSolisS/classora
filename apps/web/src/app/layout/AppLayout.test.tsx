import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SESSION_QUERY_KEY } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import { reloadSidebarPreference, resetUiStore } from '@/stores/ui';
import { stubSignedIn, type ApiStub } from '@/test/api-stub';
import {
  buttonNamed,
  mountApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { SIDEBAR_STORAGE_KEY } from './sidebar';

// #688: on a page load the sidebar appears when the session resolves. The
// top bar and <main> must take their final margin at once (no 0 → 240 px
// slide); the margin (and the sidebar width) animate only after the user
// collapses or expands the sidebar.

let app: RenderedApp | undefined;
let api: ApiStub;
let observer: MutationObserver | undefined;

beforeEach(() => {
  applyLanguage('en');
  api = stubSignedIn();
});

afterEach(() => {
  observer?.disconnect();
  observer = undefined;
  app?.unmount();
  app = undefined;
  resetUiStore();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  reloadSidebarPreference();
});

const tokens = (element: Element) => element.className.split(/\s+/);
const mainOf = (rendered: RenderedApp) =>
  rendered.container.querySelector<HTMLElement>('main#main')!;
const headerOf = (rendered: RenderedApp) =>
  rendered.container.querySelector<HTMLElement>('header')!;
const navOf = (rendered: RenderedApp) =>
  rendered.container.querySelector<HTMLElement>('header nav');

const MARGIN = /^lg:ml-(16|60)$/;

/**
 * Every class value <main> and <header> take from now on (each commit that
 * changes them), starting with the current one.
 */
function recordShellClasses(rendered: RenderedApp): string[][] {
  const seen: string[][] = [];
  const shell = [mainOf(rendered), headerOf(rendered)];
  for (const element of shell) {
    seen.push(tokens(element));
  }
  observer = new MutationObserver((records) => {
    for (const record of records) {
      seen.push(tokens(record.target as Element));
    }
  });
  for (const element of shell) {
    observer.observe(element, { attributes: true, attributeFilter: ['class'] });
  }
  return seen;
}

/** Mounts the app while /auth/me is pending and records the shell classes
 * until the sidebar is there. */
async function loadRecording(path = '/') {
  app = mountApp(path);
  const rendered = app;
  // The session is still loading: no navigation, no margin.
  expect(navOf(rendered)).toBeNull();
  const seen = recordShellClasses(rendered);
  await settleUntil(() => navOf(rendered));
  return seen;
}

function expectNoMarginTransition(seen: string[][]) {
  for (const classes of seen) {
    expect(classes).not.toContain('lg:transition-[margin]');
  }
}

describe('shell margin on load (#688)', () => {
  it('starts at lg:ml-60 without a margin transition when the session resolves', async () => {
    const seen = await loadRecording();
    // Loading (no margin), then the first commit with the navigation.
    expect(seen.some((classes) => !classes.some((c) => MARGIN.test(c)))).toBe(
      true,
    );
    expect(seen.some((classes) => classes.includes('lg:ml-60'))).toBe(true);
    expectNoMarginTransition(seen);
    expect(tokens(navOf(app!)!)).not.toContain('lg:transition-[width]');
  });

  it('starts at lg:ml-16 without a transition with the collapsed preference stored', async () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, 'collapsed');
    reloadSidebarPreference();
    const seen = await loadRecording();
    expect(seen.some((classes) => classes.includes('lg:ml-16'))).toBe(true);
    expect(seen.some((classes) => classes.includes('lg:ml-60'))).toBe(false);
    expectNoMarginTransition(seen);
  });

  it('keeps no transition when the session is refetched and stays signed in', async () => {
    const seen = await loadRecording();
    await act(() =>
      app!.queryClient.refetchQueries({ queryKey: SESSION_QUERY_KEY }),
    );
    expect(api.calls('GET /api/v1/auth/me')).toHaveLength(2);
    expect(navOf(app!)).not.toBeNull();
    expectNoMarginTransition(seen);
  });

  it('animates the margins and the sidebar width after a collapse or expand, at lg and up only', async () => {
    await loadRecording();
    const nav = navOf(app!)!;
    act(() => buttonNamed(nav, 'Collapse sidebar').click());
    for (const element of [mainOf(app!), headerOf(app!)]) {
      const classes = tokens(element);
      expect(classes).toContain('lg:ml-16');
      expect(classes).toContain('lg:transition-[margin]');
      expect(classes).toContain('lg:duration-200');
      expect(classes).toContain('lg:motion-reduce:transition-none');
      // #576: never below lg.
      expect(classes.some((c) => /^(transition|duration)(-|$)/.test(c))).toBe(
        false,
      );
    }
    expect(tokens(nav)).toContain('lg:transition-[width]');

    act(() => buttonNamed(nav, 'Expand sidebar').click());
    expect(tokens(mainOf(app!))).toEqual(
      expect.arrayContaining(['lg:ml-60', 'lg:transition-[margin]']),
    );
  });

  it('drops the transition with the navigation and does not slide it back in (privacy notice)', async () => {
    await loadRecording();
    act(() => buttonNamed(navOf(app!)!, 'Collapse sidebar').click());
    expect(tokens(mainOf(app!))).toContain('lg:transition-[margin]');

    const seen = recordShellClasses(app!);
    await act(() => app!.router.navigate('/privacy-notice'));
    await settleUntil(() => navOf(app!) === null);
    await act(() => app!.router.navigate('/'));
    await settleUntil(() => navOf(app!));
    // Each value either has the margin without a transition, or no margin
    // and no transition: never a margin sliding in or out.
    expect(
      seen.slice(2).some((classes) => !classes.some((c) => MARGIN.test(c))),
    ).toBe(true);
    for (const classes of seen.slice(2)) {
      expect(classes).not.toContain('lg:transition-[margin]');
    }
  });
});
