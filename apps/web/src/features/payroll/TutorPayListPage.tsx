import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Input } from '@/components/ui/input';
import { USER_STATES } from '@/features/users/admin/api';
import {
  fetchPayrollTutors,
  payrollTutorsListKey,
  type UserState,
} from './api';
import { PayrollTabs } from './PayrollTabs';
import {
  DEFAULT_LIST_STATE,
  payrollListParams,
  SEARCH_MAX_LENGTH,
} from './rules';
import { INPUT_CLASS, SELECT_CLASS } from './sheet';
import { TutorPayList, type TutorPayListState } from './TutorPayList';

// /payroll/tutors (#425 B), for payroll.view holders: search, state filter
// and the paginated list from GET /payroll/tutors (never GET /tutors, which
// Finance Staff can't read). The filters live in the URL; without a state
// the list shows active tutors. Typing waits 300 ms before it changes the
// URL and goes back to page 1; picking a state writes both at once.

const SEARCH_DEBOUNCE_MS = 300;

export function TutorPayListPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.list.title'));
  const id = useId();
  const ids = {
    title: `${id}-title`,
    search: `${id}-search`,
    state: `${id}-state`,
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const params = payrollListParams(searchParams);
  const urlSearch = searchParams.toString();
  const [text, setText] = useState(params.q);

  // What the user last asked for. Every URL write is composed from this, never
  // from rendered params: a navigation can still be pending, so those can be
  // out of date (QA #425 D6). `written` lists our own writes not yet committed,
  // so a committed URL that isn't one of them (back/forward, a link) replaces
  // the intent instead of being overwritten.
  const intent = useRef({ q: params.q, state: params.state });
  const written = useRef<string[]>([]);
  const timer = useRef<number | null>(null);

  function cancelTimer() {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }

  /** Writes the intent to the URL, unless the URL already says it. */
  function writeUrl(page: number, options?: { replace?: boolean }) {
    const next = new URLSearchParams();
    const { q, state } = intent.current;
    if (q) {
      next.set('q', q);
    }
    if (state !== DEFAULT_LIST_STATE) {
      next.set('state', state);
    }
    if (page > 1) {
      next.set('page', String(page));
    }
    const value = next.toString();
    if (value !== (written.current.at(-1) ?? urlSearch)) {
      written.current.push(value);
      setSearchParams(next, options);
    }
  }

  useEffect(() => {
    const own = written.current.indexOf(urlSearch);
    if (own >= 0) {
      written.current.splice(0, own + 1);
      return;
    }
    written.current = [];
    cancelTimer();
    const { q, state } = payrollListParams(new URLSearchParams(urlSearch));
    intent.current = { q, state };
    setText(q);
  }, [urlSearch]);

  useEffect(() => cancelTimer, []);

  function onSearch(value: string) {
    setText(value);
    const q = value.trim();
    const committedOrPending = payrollListParams(
      new URLSearchParams(written.current.at(-1) ?? urlSearch),
    ).q;
    intent.current.q = q;
    cancelTimer();
    if (q !== committedOrPending) {
      timer.current = window.setTimeout(() => {
        timer.current = null;
        writeUrl(1, { replace: true });
      }, SEARCH_DEBOUNCE_MS);
    }
  }

  /** Any waiting search goes with it, in one write, back on page 1. */
  function onState(value: UserState | 'all') {
    cancelTimer();
    intent.current.state = value;
    writeUrl(1);
  }

  /** A waiting search takes over: its results start on page 1. */
  function goToPage(page: number) {
    const searching = timer.current !== null;
    cancelTimer();
    writeUrl(searching ? 1 : page);
  }

  const list = useQuery({
    queryKey: payrollTutorsListKey(params),
    queryFn: ({ signal }) => fetchPayrollTutors(params, signal),
    placeholderData: keepPreviousData,
  });

  const state: TutorPayListState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex flex-col gap-8">
      <PayrollTabs />
      <h1 id={ids.title} className="text-2xl font-semibold">
        {t('payroll.list.title')}
      </h1>
      <div
        role="search"
        className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.search} className="text-sm font-medium">
            {t('payroll.list.search')}
          </label>
          <Input
            id={ids.search}
            type="search"
            autoComplete="off"
            spellCheck={false}
            maxLength={SEARCH_MAX_LENGTH}
            className={INPUT_CLASS}
            value={text}
            onChange={(event) => onSearch(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.state} className="text-sm font-medium">
            {t('payroll.list.state')}
          </label>
          <select
            id={ids.state}
            className={SELECT_CLASS}
            value={params.state}
            onChange={(event) =>
              onState(event.target.value as UserState | 'all')
            }
          >
            {USER_STATES.map((value) => (
              <option key={value} value={value}>
                {t(`userStates.${value}`)}
              </option>
            ))}
            <option value="all">{t('payroll.list.allStates')}</option>
          </select>
        </div>
      </div>
      <TutorPayList state={state} labelledBy={ids.title} onPage={goToPage} />
    </div>
  );
}
