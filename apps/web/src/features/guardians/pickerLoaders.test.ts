import { afterEach, describe, expect, it, vi } from 'vitest';
import { searchFamilies } from '@/features/billing/packages/api';
import { searchTutorOptions } from '@/features/calendar/api';
import { searchLoginAccounts } from '@/features/consent/api';
import { searchPayrollTutors } from '@/features/payroll/api';
import { stubApi } from '@/test/api-stub';
import { jsonResponse } from '@/test/render-app';
import { fetchGuardianOptions, searchGuardianAccounts } from './api';

// The loaders of the tutor, account, guardian and family comboboxes (#548
// C1, G1): the first page has no `q` key (never `q=`, a 422 on some
// routes), a typed text is sent trimmed, each asks for its page size, and
// each returns the response's `total`.

interface Case {
  name: string;
  path: string;
  load: (q: string | null, signal: AbortSignal) => Promise<unknown>;
  /** The expected query string, without `q`. */
  fixed: Record<string, string>;
  item: unknown;
}

const USER = {
  id: '00000000-0000-4000-8000-0000000b0001',
  username: 'ana.p',
  display_name: 'Ana P',
  state: 'active',
  roles: ['tutor'],
  created_at: '2026-01-05T12:00:00Z',
};

const CASES: Case[] = [
  {
    name: 'tutor options (F1-F3)',
    path: '/api/v1/schedule/tutor-options',
    load: searchTutorOptions,
    fixed: { limit: '50' },
    item: { id: USER.id, display_name: 'Ana P', state: 'suspended' },
  },
  {
    name: 'payroll tutors (F4, F5)',
    path: '/api/v1/payroll/tutors',
    load: searchPayrollTutors,
    fixed: { limit: '100' },
    item: { user_id: USER.id, display_name: 'Ana P', username: 'ana.p' },
  },
  {
    name: 'guardian accounts (F6)',
    path: '/api/v1/users',
    load: searchGuardianAccounts,
    fixed: { role: 'guardian', limit: '100' },
    item: USER,
  },
  {
    name: 'login accounts (F7)',
    path: '/api/v1/users',
    load: searchLoginAccounts,
    fixed: { limit: '100' },
    item: USER,
  },
  {
    name: 'active guardians (F8)',
    path: '/api/v1/guardians',
    load: fetchGuardianOptions,
    fixed: { status: 'active', limit: '100' },
    item: { id: USER.id, given_names: 'Ana', family_names: 'P' },
  },
  {
    name: 'families (F9)',
    path: '/api/v1/families',
    load: searchFamilies,
    fixed: { status: 'all', limit: '100' },
    item: { id: USER.id, name: 'Familia P' },
  },
];

function stubbed(path: string, item: unknown) {
  const urls: URL[] = [];
  stubApi({
    [`GET ${path}`]: (request) => {
      urls.push(new URL(request.url));
      return jsonResponse(200, {
        items: [item],
        total: 63,
        limit: 100,
        offset: 0,
      });
    },
  });
  return urls;
}

const params = (url: URL) => Object.fromEntries(url.searchParams);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe.each(CASES)('the $name loader', ({ path, load, fixed, item }) => {
  it('sends no q for the first page or blank text, and returns total', async () => {
    const urls = stubbed(path, item);

    const page = (await load(null, new AbortController().signal)) as {
      items: unknown[];
      total: number;
    };
    await load('   ', new AbortController().signal);

    expect(urls.map(params)).toEqual([fixed, fixed]);
    expect(page.total).toBe(63);
    expect(page.items).toHaveLength(1);
  });

  it('sends the typed text, trimmed, as q', async () => {
    const urls = stubbed(path, item);

    await load('  ana ', new AbortController().signal);

    expect(params(urls[0]!)).toEqual({ ...fixed, q: 'ana' });
  });
});

describe('the tutor options loader', () => {
  it('marks inactive tutors and keeps the plain name', async () => {
    stubbed(CASES[0]!.path, CASES[0]!.item);

    const page = await searchTutorOptions(null, new AbortController().signal);

    expect(page.items).toEqual([
      { id: USER.id, name: 'Ana P', inactive: true },
    ]);
  });
});
