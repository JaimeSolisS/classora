import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import {
  createVendor,
  fetchVendors,
  updateVendor,
  VENDORS_KEY,
  vendorsListKey,
  type Vendor,
  type VendorStateFilter,
} from '../api';
import {
  isGone,
  isVersionConflict,
  vendorCreateBody,
  vendorUpdateBody,
  type VendorFormValues,
} from '../forms';
import { useListNotices, type StaleNotice } from '../useListNotices';
import type { SaveResult } from '../useRowSave';
import { DEFAULT_VENDOR_STATE, vendorListParams } from './listParams';
import { VendorDialog } from './VendorDialog';
import { VendorsView, type VendorsState } from './VendorsView';

// /expenses/vendors (M7-11b C), for expense.configure holders. The search,
// the state filter and the page live in the URL. Typing waits 300 ms before
// it changes the URL and goes back to page 1; picking a state writes both at
// once. Writes, stale confirmations, failed refetches and closing after a
// failed save follow the categories page (B4, B5, D1). A 403 is the
// forbidden page (A4).

const SEARCH_DEBOUNCE_MS = 300;
const V = 'finance.vendors';

type Editing = { vendor: Vendor | null };

export function VendorsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t(`${V}.title`));
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const params = vendorListParams(searchParams);
  const urlSearch = searchParams.toString();
  const [text, setText] = useState(params.q);
  const [editing, setEditing] = useState<Editing | null>(null);
  const saveFailed = useRef(false);

  // What the user last asked for. Every URL write is composed from this, never
  // from rendered params: a navigation can still be pending (QA #425 D6).
  // `written` lists our own writes not yet committed, so a committed URL that
  // isn't one of them (back/forward, a link) replaces the intent. `committed`
  // is the URL at call time (QA #425 D7).
  const committed = useRef(urlSearch);
  const intent = useRef({ q: params.q, state: params.state });
  const written = useRef<string[]>([]);
  // A search waiting to be written: each keystroke takes a new number, so the
  // effect below restarts its timer; null when none waits.
  const [searchWait, setSearchWait] = useState<number | null>(null);
  const waiting = useRef(false);
  const keystrokes = useRef(0);

  function cancelTimer() {
    waiting.current = false;
    setSearchWait(null);
  }

  /** Writes the intent to the URL, unless the URL already says it. */
  function writeUrl(page: number, options?: { replace?: boolean }) {
    const next = new URLSearchParams();
    const { q, state } = intent.current;
    if (q) {
      next.set('q', q);
    }
    if (state !== DEFAULT_VENDOR_STATE) {
      next.set('state', state);
    }
    if (page > 1) {
      next.set('page', String(page));
    }
    const value = next.toString();
    if (value !== (written.current.at(-1) ?? committed.current)) {
      written.current.push(value);
      setSearchParams(next, options);
    }
  }

  // The debounced write runs with the latest render's setter and reads the
  // intent and the committed URL when it fires (BRIEF 2026-11-01).
  const writeSearch = useEffectEvent(() => {
    cancelTimer();
    writeUrl(1, { replace: true });
  });

  useEffect(() => {
    if (searchWait === null) {
      return;
    }
    const timer = window.setTimeout(() => writeSearch(), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [searchWait]);

  useEffect(() => {
    committed.current = urlSearch;
    const own = written.current.indexOf(urlSearch);
    if (own >= 0) {
      written.current.splice(0, own + 1);
      return;
    }
    written.current = [];
    cancelTimer();
    const { q, state } = vendorListParams(new URLSearchParams(urlSearch));
    intent.current = { q, state };
    setText(q);
  }, [urlSearch]);

  function onSearch(value: string) {
    setText(value);
    const q = value.trim();
    const committedOrPending = vendorListParams(
      new URLSearchParams(written.current.at(-1) ?? committed.current),
    ).q;
    intent.current.q = q;
    cancelTimer();
    if (q !== committedOrPending) {
      waiting.current = true;
      keystrokes.current += 1;
      setSearchWait(keystrokes.current);
    }
  }

  /** Any waiting search goes with it, in one write, back on page 1. */
  function onStateFilter(value: VendorStateFilter) {
    cancelTimer();
    intent.current.state = value;
    writeUrl(1);
  }

  /** A waiting search takes over: its results start on page 1. */
  function goToPage(page: number) {
    const searching = waiting.current;
    cancelTimer();
    writeUrl(searching ? 1 : page);
  }

  const listKey = vendorsListKey(params);
  const list = useQuery({
    queryKey: listKey,
    queryFn: ({ signal }) => fetchVendors(params, signal),
    placeholderData: keepPreviousData,
    meta: { expectedStatuses: [403] },
  });
  const notices = useListNotices(
    list.isSuccess && !list.isFetching,
    (notice: StaleNotice, reloaded: boolean) =>
      notice === 'gone'
        ? reloaded
          ? t(`${V}.gone`)
          : t(`${V}.goneNotReloaded`)
        : reloaded
          ? t(`${V}.conflictRefreshed`)
          : t(`${V}.conflictNotReloaded`),
  );

  /** Whether the shown page reloaded: invalidating never rejects. */
  async function refresh(): Promise<boolean> {
    await queryClient.invalidateQueries({ queryKey: VENDORS_KEY });
    return queryClient.getQueryState(listKey)?.status !== 'error';
  }

  async function save(
    values: VendorFormValues,
    base: Vendor | null,
  ): Promise<SaveResult> {
    const update = base ? vendorUpdateBody(values, base) : null;
    if (base && !update) {
      return 'unchanged';
    }
    try {
      await (base && update
        ? updateVendor(base.id, update)
        : createVendor(vendorCreateBody(values)));
    } catch (caught) {
      saveFailed.current = true;
      throw caught;
    }
    saveFailed.current = false;
    await refresh();
    return 'saved';
  }

  async function setActive(vendor: Vendor, active: boolean) {
    notices.announce('');
    try {
      await updateVendor(vendor.id, {
        version: vendor.version,
        is_active: active,
      });
    } catch (caught) {
      if (isGone(caught) || isVersionConflict(caught)) {
        notices.stale(isGone(caught) ? 'gone' : 'conflict', await refresh());
        return;
      }
      throw caught;
    }
    await refresh();
    notices.announce(active ? t(`${V}.reactivated`) : t(`${V}.deactivated`));
  }

  if (list.error instanceof ApiError && list.error.status === 403) {
    return <ForbiddenPage />;
  }

  // Another page's rows are never shown as this one's (a failed page change
  // is an error), and rows whose refetch failed say they may be out of date.
  const state: VendorsState =
    list.isError && (!list.data || list.isPlaceholderData)
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : list.data
        ? {
            status: 'ready',
            page: list.data,
            refreshError: list.isError ? list.error : null,
            onRefresh: () => void list.refetch(),
          }
        : { status: 'loading' };

  // The live row, so a colleague's version reaches an open dialog; the
  // snapshot if it went missing, so the dialog never closes by itself.
  const edited = editing?.vendor
    ? (list.data?.items.find((row) => row.id === editing.vendor?.id) ??
      editing.vendor)
    : null;

  return (
    <>
      <VendorsView
        state={state}
        search={text}
        onSearch={onSearch}
        stateFilter={params.state}
        onStateFilter={onStateFilter}
        filtered={params.q !== '' || params.state !== DEFAULT_VENDOR_STATE}
        onPage={goToPage}
        onAdd={() => {
          notices.announce('');
          setEditing({ vendor: null });
        }}
        onEdit={(vendor) => {
          notices.announce('');
          setEditing({ vendor });
        }}
        onSetActive={setActive}
        announcement={notices.announcement}
      />
      {editing ? (
        <VendorDialog
          key={editing.vendor?.id ?? 'new'}
          open
          vendor={edited}
          onSubmit={save}
          onReload={refresh}
          onClose={(saved) => {
            setEditing(null);
            if (saved) {
              notices.announce(t(`${V}.saved`));
            }
            if (saveFailed.current) {
              saveFailed.current = false;
              void refresh();
            }
          }}
        />
      ) : null}
    </>
  );
}
