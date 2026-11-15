import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Input } from '@/components/ui/input';
import { useCan } from '@/features/auth/useSession';
import { ROLE_ORDER, roleNameKey } from '../roles';
import { fetchUsers, USER_STATES, usersListKey } from './api';
import { listParams, SEARCH_MAX_LENGTH } from './rules';
import { UsersTable, type UsersTableState } from './UsersTable';

// /users (#31 J): search, role and state filters, and the paginated list.
// The filters live in the URL (q, role, state, page), so a reload and Back
// keep them. Typing in the search waits 300 ms before it changes the URL and
// goes back to page 1.

/** How long typing must pause before the search runs. */
export const SEARCH_DEBOUNCE_MS = 300;
const SELECT_CLASS =
  'h-9 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring md:text-sm';

export function UsersPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('users.title'));
  const canInvite = useCan('user.invite');
  const id = useId();
  const ids = {
    title: `${id}-title`,
    search: `${id}-search`,
    role: `${id}-role`,
    state: `${id}-state`,
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const params = listParams(searchParams);
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
    name: 'q' | 'role' | 'state',
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

  useEffect(() => {
    const q = text.trim();
    if (q === params.q) {
      return;
    }
    const timer = window.setTimeout(() => {
      setFilter('q', q, { replace: true });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // setFilter only uses the stable setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    queryKey: usersListKey(params),
    queryFn: ({ signal }) => fetchUsers(params, signal),
    placeholderData: keepPreviousData,
  });

  const tableState: UsersTableState = list.data
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
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={ids.title} className="text-2xl font-semibold">
          {t('users.title')}
        </h1>
        {canInvite ? (
          <Link
            to="/invitations"
            className="inline-flex h-9 items-center rounded-md border border-border px-2.5 text-sm font-medium outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('users.list.invite')}
          </Link>
        ) : null}
      </div>
      <div
        role="search"
        className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.search} className="text-sm font-medium">
            {t('users.list.search')}
          </label>
          <Input
            id={ids.search}
            type="search"
            autoComplete="off"
            spellCheck={false}
            maxLength={SEARCH_MAX_LENGTH}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.role} className="text-sm font-medium">
            {t('users.list.role')}
          </label>
          <select
            id={ids.role}
            className={SELECT_CLASS}
            value={params.role}
            onChange={(event) => setFilter('role', event.target.value)}
          >
            <option value="">{t('users.list.allRoles')}</option>
            {ROLE_ORDER.map((role) => (
              <option key={role} value={role}>
                {t(roleNameKey(role))}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.state} className="text-sm font-medium">
            {t('users.list.state')}
          </label>
          <select
            id={ids.state}
            className={SELECT_CLASS}
            value={params.state}
            onChange={(event) => setFilter('state', event.target.value)}
          >
            <option value="">{t('users.list.allStates')}</option>
            {USER_STATES.map((state) => (
              <option key={state} value={state}>
                {t(`userStates.${state}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <UsersTable state={tableState} labelledBy={ids.title} onPage={goToPage} />
    </div>
  );
}
