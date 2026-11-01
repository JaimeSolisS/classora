import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useEffectEvent, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Input } from '@/components/ui/input';
import { USER_STATES } from '@/features/users/admin/api';
import { fetchPayrollTutors, payrollTutorsListKey } from './api';
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
// URL and goes back to page 1.

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
  const [text, setText] = useState(params.q);
  const [shownQ, setShownQ] = useState(params.q);
  if (params.q !== shownQ) {
    // Back/forward or a link changed q: show it in the field (adjusting
    // state while rendering, not in an effect).
    setShownQ(params.q);
    if (text.trim() !== params.q) {
      setText(params.q);
    }
  }

  /** Sets one filter (empty removes it) and goes back to page 1. */
  function setFilter(
    name: 'q' | 'state',
    value: string,
    options?: { replace?: boolean },
  ) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) {
        next.set(name, value);
      } else {
        next.delete(name);
      }
      next.delete('page');
      return next;
    }, options);
  }

  // An effect event, so the timer writes over the params of the latest render:
  // `previous` in setSearchParams is the params of the render that made that
  // setter, not the URL at call time. A filter changed during the wait stays
  // (QA #425 D6).
  const applySearch = useEffectEvent((q: string) => {
    setFilter('q', q, { replace: true });
  });

  useEffect(() => {
    const q = text.trim();
    if (q === params.q) {
      return;
    }
    const timer = window.setTimeout(() => {
      applySearch(q);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [text, params.q]);

  function goToPage(page: number) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (page > 1) {
        next.set('page', String(page));
      } else {
        next.delete('page');
      }
      return next;
    });
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
            onChange={(event) => setText(event.target.value)}
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
              setFilter(
                'state',
                event.target.value === DEFAULT_LIST_STATE
                  ? ''
                  : event.target.value,
              )
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
