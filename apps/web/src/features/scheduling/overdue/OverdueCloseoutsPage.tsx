import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { searchTutorOptions } from '@/features/calendar/api';
import { fetchOverdueCloseouts, overdueKey } from './api';
import {
  OverdueCloseoutsView,
  type OverdueCloseoutsState,
} from './OverdueCloseoutsView';
import {
  overdueQuery,
  overdueSearchParams,
  parseOverdueState,
  rangeInvalid,
} from './urlState';

// /schedule/overdue-closeouts (#301b A), for teaching_hours.approve holders
// (the route guard): the filters and the page live in the URL; `to` before
// `from` sends nothing; a page past the last one goes back to page 1. The
// tutor chip names the picked option, else the first row's tutor (a URL
// loaded directly), else "Selected tutor".

function isRangeError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.fieldErrors.some(({ code }) => code === 'range_invalid')
  );
}

export function OverdueCloseoutsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('scheduling.overdue.title'));
  const timeZone = useCenterTimeZone();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseOverdueState(params), [params]);
  const [names, setNames] = useState<Readonly<Record<string, string>>>({});
  const invalid = rangeInvalid(filters);
  const query = overdueQuery(filters);
  const list = useQuery({
    queryKey: overdueKey(query),
    queryFn: ({ signal }) => fetchOverdueCloseouts(query, signal),
    enabled: !invalid,
  });

  const pastTheEnd =
    list.data !== undefined && list.data.items.length === 0 && query.offset > 0;
  useEffect(() => {
    if (pastTheEnd) {
      setParams(overdueSearchParams({ ...filters, page: 1 }), {
        replace: true,
      });
    }
  }, [pastTheEnd, filters, setParams]);

  const serverRange = list.isError && isRangeError(list.error);
  let state: OverdueCloseoutsState;
  if (invalid || serverRange) {
    state = { status: 'invalid' };
  } else if (list.isError) {
    state = {
      status: 'error',
      error: list.error,
      onRetry: () => void list.refetch(),
    };
  } else if (!list.data || pastTheEnd) {
    state = { status: 'loading' };
  } else {
    state = {
      status: 'ready',
      items: list.data.items,
      total: list.data.total,
      offset: list.data.offset,
      fetchedAt: new Date(list.dataUpdatedAt),
    };
  }

  let tutorName: string | null = null;
  if (filters.tutor) {
    tutorName =
      names[filters.tutor] ??
      list.data?.items.find((item) => item.tutor.user_id === filters.tutor)
        ?.tutor.display_name ??
      null;
  }

  return (
    <OverdueCloseoutsView
      state={state}
      filters={filters}
      onFilters={(next) => setParams(overdueSearchParams(next))}
      timeZone={timeZone}
      searchTutors={searchTutorOptions}
      tutorName={tutorName}
      onTutorName={(id, name) =>
        setNames((current) => ({ ...current, [id]: name }))
      }
      rangeError={serverRange}
    />
  );
}

export default OverdueCloseoutsPage;
