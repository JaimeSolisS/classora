import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { ExportLink } from '@/features/exports/ExportLink';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import type { AttemptFailure } from '@/features/billing/accounts/rules';
import { todayInZone } from '@/lib/time';
import {
  ALL_VENDORS_KEY,
  createExpense,
  EXPENSE_CATEGORIES_KEY,
  EXPENSES_KEY,
  expensesListKey,
  fetchAllVendors,
  fetchExpenseCategories,
  fetchExpenses,
  type ExpenseCreate,
  type ExpenseListParams,
} from '../api';
import {
  ExpensesView,
  type ExpenseFilterChange,
  type ExpensesState,
} from './ExpensesView';
import { NewExpenseDialog, type PickerState } from './NewExpenseDialog';
import {
  expenseCapabilities,
  expenseListParams,
  expenseListSearch,
  expensePath,
  isFiltered,
  isRangeInvalid,
} from './rules';

// /expenses/records (M7-12b B), for expense.view holders. The filters, the
// search, the sort and the page live in the URL; typing waits 300 ms before
// it changes the URL and goes back to page 1, and the debounced write reads
// the intent and the committed URL when it fires (BRIEF 2026-11-01; the
// vendors page's pattern). A 403 is the forbidden page.
//
// "Nuevo gasto" (expense.manage) opens the dialog; a 201 opens the new
// expense. Closing it after any failed attempt refetches the list, and
// "Nuevo gasto" stays disabled until that lands (E2 (a), D6); after a lost
// answer the status line says the expense may already exist. A failed
// refetch keeps the rows with the out-of-date alert and never says it
// reloaded (B3).

const SEARCH_DEBOUNCE_MS = 300;
const L = 'finance.expenses';

type Intent = Omit<ExpenseListParams, 'page'>;
type Block = { kind: 'refreshing' } | { kind: 'stale'; error: unknown };

function intentOf(params: ExpenseListParams): Intent {
  return {
    statuses: params.statuses,
    categoryId: params.categoryId,
    vendorId: params.vendorId,
    from: params.from,
    to: params.to,
    q: params.q,
    sort: params.sort,
  };
}

export function ExpensesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t(`${L}.title`));
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const can = expenseCapabilities(user);
  const timeZone = useCenterTimeZone();
  const [searchParams, setSearchParams] = useSearchParams();
  const params = expenseListParams(searchParams);
  const urlSearch = searchParams.toString();
  const rangeInvalid = isRangeInvalid(params);
  const [text, setText] = useState(params.q);
  const [creating, setCreating] = useState(false);
  const [block, setBlock] = useState<Block | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const createdId = useRef<string | null>(null);

  // What the user last asked for; every URL write is composed from it, never
  // from rendered params (QA #425 D6, D7; the vendors page).
  const committed = useRef(urlSearch);
  const intent = useRef<Intent>(intentOf(params));
  const written = useRef<string[]>([]);
  const [searchWait, setSearchWait] = useState<number | null>(null);
  const waiting = useRef(false);
  const keystrokes = useRef(0);

  function cancelTimer() {
    waiting.current = false;
    setSearchWait(null);
  }

  function writeUrl(page: number, options?: { replace?: boolean }) {
    const value = expenseListSearch({ ...intent.current, page });
    if (value !== (written.current.at(-1) ?? committed.current)) {
      written.current.push(value);
      setSearchParams(new URLSearchParams(value), options);
    }
  }

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
    const next = expenseListParams(new URLSearchParams(urlSearch));
    intent.current = intentOf(next);
    setText(next.q);
  }, [urlSearch]);

  function onSearch(value: string) {
    setText(value);
    const q = value.trim();
    const committedOrPending = expenseListParams(
      new URLSearchParams(written.current.at(-1) ?? committed.current),
    ).q;
    intent.current = { ...intent.current, q };
    cancelTimer();
    if (q !== committedOrPending) {
      waiting.current = true;
      keystrokes.current += 1;
      setSearchWait(keystrokes.current);
    }
  }

  /** Any waiting search goes with it, in one write, back on page 1. */
  function onFilter(change: ExpenseFilterChange) {
    cancelTimer();
    intent.current = { ...intent.current, ...change };
    writeUrl(1);
  }

  /** A waiting search takes over: its results start on page 1. */
  function goToPage(page: number) {
    const searching = waiting.current;
    cancelTimer();
    writeUrl(searching ? 1 : page);
  }

  const listKey = expensesListKey(params);
  const list = useQuery({
    queryKey: listKey,
    queryFn: ({ signal }) => fetchExpenses(params, signal),
    placeholderData: keepPreviousData,
    enabled: !rangeInvalid,
    meta: { expectedStatuses: [403] },
  });
  const categories = useQuery({
    queryKey: EXPENSE_CATEGORIES_KEY,
    queryFn: ({ signal }) => fetchExpenseCategories(signal),
  });
  const vendors = useQuery({
    queryKey: ALL_VENDORS_KEY,
    queryFn: ({ signal }) => fetchAllVendors(signal),
  });

  /** Refetches the list and holds "Nuevo gasto" until it lands (D6). */
  async function reload() {
    setBlock({ kind: 'refreshing' });
    await queryClient.invalidateQueries({
      queryKey: [...EXPENSES_KEY, 'list'],
    });
    const state = queryClient.getQueryState(listKey);
    setBlock(
      state?.status === 'error' ? { kind: 'stale', error: state.error } : null,
    );
  }

  async function reloadPickers() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: EXPENSE_CATEGORIES_KEY }),
      queryClient.invalidateQueries({ queryKey: ALL_VENDORS_KEY }),
    ]);
  }

  async function create(body: ExpenseCreate, key: string) {
    const created = await createExpense(body, key);
    createdId.current = created.id;
  }

  function saved() {
    const id = createdId.current;
    setCreating(false);
    void queryClient.invalidateQueries({ queryKey: EXPENSES_KEY });
    if (id) void navigate(expensePath(id), { state: { created: true } });
  }

  function closed(failed: AttemptFailure) {
    setCreating(false);
    if (failed === 'none') return;
    if (failed === 'lost') setAnnouncement(t(`${L}.closedLost`));
    void reload();
  }

  if (list.error instanceof ApiError && list.error.status === 403) {
    return <ForbiddenPage />;
  }

  const state: ExpensesState =
    list.isError && (!list.data || list.isPlaceholderData)
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : list.data && !rangeInvalid
        ? {
            status: 'ready',
            page: list.data,
            refreshError: list.isError
              ? list.error
              : block?.kind === 'stale'
                ? block.error
                : null,
            onRefresh: () => void reload(),
          }
        : { status: 'loading' };

  const picker = <T,>(query: {
    data: T[] | undefined;
    isError: boolean;
    error: unknown;
    refetch: () => unknown;
  }): PickerState<T> =>
    query.data
      ? { status: 'ready', items: query.data }
      : query.isError
        ? {
            status: 'error',
            error: query.error,
            onRetry: () => void query.refetch(),
          }
        : { status: 'loading' };

  return (
    <>
      <ExpensesView
        state={state}
        params={params}
        search={text}
        onSearch={onSearch}
        onFilter={onFilter}
        categories={categories.data ?? null}
        vendors={vendors.data ?? null}
        rangeInvalid={rangeInvalid}
        filtered={isFiltered(params)}
        onPage={goToPage}
        canAdd={can.manage}
        addBlocked={block !== null || creating}
        onAdd={() => {
          setAnnouncement('');
          setCreating(true);
        }}
        announcement={announcement}
        exportLink={<ExportLink dataset="expenses" />}
      />
      {can.manage ? (
        <NewExpenseDialog
          open={creating}
          today={todayInZone(timeZone)}
          categories={picker(categories)}
          vendors={picker(vendors)}
          onReloadPickers={reloadPickers}
          onSubmit={create}
          onSaved={saved}
          onClose={closed}
        />
      ) : null}
    </>
  );
}
