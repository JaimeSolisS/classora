import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useSession } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';
import { fetchImports, importsListKey, JOB_STATUSES } from './api';
import { ImportHistory, type ImportHistoryState } from './ImportHistory';
import { availableKinds, isImportKind, type ImportKindCode } from './kinds';
import { NewImportForm } from './NewImportForm';
import { historyParams } from './rules';
import { SELECT_CLASS } from './styles';

// /imports (M8-05b B): a new import, then the history. `?kind=` preselects
// the new import's kind (the "Import" links of /students and /tutors, A2).
// The history's filters live in the URL as `historyKind`, `status` and
// `page` (D1: `kind` already names the preselected kind), so a reload and
// Back keep them.

export function ImportsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('imports.title'));
  const session = useSession();
  const kinds = availableKinds(
    session.status === 'signedIn' ? session.user : null,
  );
  const kindCodes = kinds.map((kind) => kind.code);
  const id = useId();
  const ids = {
    title: `${id}-title`,
    newTitle: `${id}-new`,
    historyTitle: `${id}-history`,
    historyKind: `${id}-history-kind`,
    status: `${id}-status`,
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get('kind') ?? '';
  const initialKind: ImportKindCode | '' =
    isImportKind(requested) && kindCodes.includes(requested) ? requested : '';
  const params = historyParams(searchParams, kindCodes);

  function setFilter(name: 'historyKind' | 'status', value: string) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) {
        next.set(name, value);
      } else {
        next.delete(name);
      }
      next.delete('page');
      return next;
    });
  }

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
    queryKey: importsListKey(params),
    queryFn: ({ signal }) => fetchImports(params, signal),
    placeholderData: keepPreviousData,
    enabled: kinds.length > 0,
  });

  const historyState: ImportHistoryState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <h1 id={ids.title} className="text-2xl font-semibold">
        {t('imports.title')}
      </h1>
      {kinds.length === 0 ? (
        <p>{t('imports.noKinds')}</p>
      ) : (
        <>
          <section
            aria-labelledby={ids.newTitle}
            className="flex flex-col gap-4"
          >
            <h2 id={ids.newTitle} className="text-xl font-semibold">
              {t('imports.form.title')}
            </h2>
            <NewImportForm kinds={kinds} initialKind={initialKind} />
          </section>
          <section
            aria-labelledby={ids.historyTitle}
            className="flex min-w-0 flex-col gap-4"
          >
            <h2 id={ids.historyTitle} className="text-xl font-semibold">
              {t('imports.history.title')}
            </h2>
            <div
              role="search"
              aria-label={t('imports.history.filters')}
              className="grid grid-cols-1 gap-4 md:max-w-2xl md:grid-cols-2"
            >
              <div className="flex flex-col gap-2">
                <label
                  htmlFor={ids.historyKind}
                  className="text-sm font-medium"
                >
                  {t('imports.history.kind')}
                </label>
                <select
                  id={ids.historyKind}
                  className={cn(SELECT_CLASS, 'min-h-11 md:min-h-9')}
                  value={params.kind}
                  onChange={(event) =>
                    setFilter('historyKind', event.target.value)
                  }
                >
                  <option value="">{t('imports.history.allKinds')}</option>
                  {kinds.map((kind) => (
                    <option key={kind.code} value={kind.code}>
                      {t(`imports.kinds.${kind.code}`)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-2">
                <label htmlFor={ids.status} className="text-sm font-medium">
                  {t('imports.history.status')}
                </label>
                <select
                  id={ids.status}
                  className={cn(SELECT_CLASS, 'min-h-11 md:min-h-9')}
                  value={params.status}
                  onChange={(event) => setFilter('status', event.target.value)}
                >
                  <option value="">{t('imports.history.allStatuses')}</option>
                  {JOB_STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {t(`imports.status.${status}`)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <ImportHistory
              state={historyState}
              labelledBy={ids.historyTitle}
              filtered={params.kind !== '' || params.status !== ''}
              onPage={goToPage}
            />
          </section>
        </>
      )}
    </div>
  );
}
