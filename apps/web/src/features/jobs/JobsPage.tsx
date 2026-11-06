import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';
import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import {
  fetchJobs,
  fetchJobTasks,
  JOB_TASKS_KEY,
  JOBS_QUERY_KEY,
  jobsKey,
  retryJob,
  type FailedJob,
  type FailedJobPage,
  type JobTask,
} from './api';
import { FailedJobList, type JobListState } from './FailedJobList';
import {
  filtersFromParams,
  hasErrors,
  isFiltered,
  NO_FILTERS,
  paramsFor,
  STATUS_FILTERS,
  validateFilters,
  type FilterErrors,
  type JobFilters,
  type StatusFilter,
} from './filters';
import { useTaskLabel } from './useTaskLabel';

// /jobs (#128 G): the failed background jobs for jobs.manage holders
// (Owner, Administrator). Filters (task, status, from/to center-local days)
// live in the URL; the form applies them on submit, after the checks (no
// request while they fail, also for a hand-edited URL). The list is newest
// failure first, 25 per page, with "Load more". "Retry" opens a
// confirmation; a 202 replaces the row with the response and says so in the
// polite status (the app has no toast); a 409 shows its own message; every
// refused retry reloads the list.

const SELECT_CLASS =
  'min-h-11 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-destructive md:text-sm';

function useJobList(filters: JobFilters, timeZone: string, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: jobsKey(filters, timeZone),
    queryFn: ({ pageParam, signal }) =>
      fetchJobs(filters, timeZone, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled,
    retry: false,
  });
}

export function JobsPage() {
  const { t } = useTranslation();
  const { t: errorsT } = useTranslation('errors');
  useDocumentTitle(t('jobs.title'));
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const taskLabel = useTaskLabel();
  const heading = useRef<HTMLHeadingElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = filtersFromParams(searchParams);
  const urlErrors = validateFilters(filters);
  const valid = !hasErrors(urlErrors);
  const [retrying, setRetrying] = useState<FailedJob | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const tasks = useQuery({
    queryKey: JOB_TASKS_KEY,
    queryFn: ({ signal }) => fetchJobTasks(signal),
  });
  const list = useJobList(filters, timeZone, valid);
  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const cursorInvalid =
    list.error instanceof ApiError && list.error.code === 'invalid_cursor';
  const listKey = jobsKey(filters, timeZone);

  function apply(next: JobFilters) {
    setStatus(null);
    setRefusal(null);
    setSearchParams(paramsFor(next));
  }

  function restart() {
    void queryClient.resetQueries({ queryKey: listKey });
  }

  function replaceRow(updated: FailedJob) {
    queryClient.setQueryData<InfiniteData<FailedJobPage>>(listKey, (data) =>
      data
        ? {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              items: page.items.map((job) =>
                job.id === updated.id ? updated : job,
              ),
            })),
          }
        : data,
    );
  }

  async function confirmRetry(job: FailedJob) {
    const label = taskLabel(job.task_name);
    try {
      const updated = await retryJob(job.id);
      replaceRow(updated);
      setRefusal(null);
      setStatus(t('jobs.retry.done', { task: label }));
    } catch (error) {
      setStatus(null);
      // job_not_retryable, job_not_failed and job_already_queued have their
      // own messages; a 404 or any other error its generic one.
      setRefusal(errorMessage(error, errorsT));
      void queryClient.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
    } finally {
      setRetrying(null);
    }
  }

  let listState: JobListState;
  if (list.data) {
    listState = {
      status: 'ready',
      items,
      hasMore: list.hasNextPage,
      loadingMore: list.isFetchingNextPage,
      moreError: list.isFetchNextPageError ? list.error : null,
      onLoadMore: () => void list.fetchNextPage(),
    };
  } else if (list.isError) {
    listState = {
      status: 'error',
      error: list.error,
      onRetry: () => void list.refetch(),
    };
  } else {
    listState = { status: 'loading' };
  }

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1
          ref={heading}
          tabIndex={-1}
          className="text-2xl font-semibold outline-none"
        >
          {t('jobs.title')}
        </h1>
        <p>{t('jobs.intro')}</p>
      </div>
      <FilterForm
        // A new URL (back/forward, a link) resets the form.
        key={searchParams.toString()}
        filters={filters}
        initialErrors={urlErrors}
        tasks={tasks.data ?? []}
        onApply={apply}
        onClear={() => {
          apply(NO_FILTERS);
          heading.current?.focus();
        }}
      />
      <p aria-live="polite" aria-atomic="true" className="text-sm">
        {status ??
          (valid && list.data
            ? t('jobs.list.shown', { count: items.length })
            : null)}
      </p>
      {refusal ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {refusal}
        </p>
      ) : null}
      {valid ? (
        <FailedJobList
          state={listState}
          cursorInvalid={cursorInvalid}
          onRestart={restart}
          onRetryJob={(job) => {
            setRefusal(null);
            setRetrying(job);
          }}
        />
      ) : (
        <p>{t('jobs.filter.fixFirst')}</p>
      )}
      <ConfirmDialog
        open={retrying !== null}
        title={t('jobs.retry.title', {
          task: retrying ? taskLabel(retrying.task_name) : '',
        })}
        body={t('jobs.retry.body')}
        confirmLabel={t('jobs.retry.confirm')}
        onConfirm={() => confirmRetry(retrying!)}
        onCancel={() => setRetrying(null)}
      />
    </div>
  );
}

interface FilterFormProps {
  filters: JobFilters;
  initialErrors: FilterErrors;
  tasks: readonly JobTask[];
  onApply: (filters: JobFilters) => void;
  onClear: () => void;
}

function FilterForm({
  filters,
  initialErrors,
  tasks,
  onApply,
  onClear,
}: FilterFormProps) {
  const { t } = useTranslation();
  const taskLabel = useTaskLabel();
  const id = useId();
  const [draft, setDraft] = useState<JobFilters>(filters);
  const [errors, setErrors] = useState<FilterErrors>(initialErrors);
  const fromField = useRef<HTMLInputElement>(null);
  const toField = useRef<HTMLInputElement>(null);
  // A task from the URL that is no longer registered stays selectable.
  const names = tasks.map((task) => task.task_name);
  const taskNames =
    filters.task && !names.includes(filters.task)
      ? [...names, filters.task]
      : names;

  function set<K extends keyof JobFilters>(key: K, value: string) {
    const trimmed = value.trim();
    setDraft((current) => ({ ...current, [key]: trimmed ? trimmed : null }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateFilters(draft);
    setErrors(found);
    if (!hasErrors(found)) {
      onApply(draft);
      return;
    }
    if (found.from) {
      fromField.current?.focus();
    } else if (found.to) {
      toField.current?.focus();
    }
  }

  const dateError =
    errors.to === 'beforeFrom'
      ? t('jobs.filter.errors.dateOrder')
      : errors.from || errors.to
        ? t('jobs.filter.errors.date')
        : null;
  const dateErrorId = `${id}-date-error`;

  return (
    <form
      noValidate
      onSubmit={submit}
      aria-label={t('jobs.filter.label')}
      className="flex min-w-0 flex-col gap-4"
    >
      <div className="grid min-w-0 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-task`} className="text-sm font-medium">
            {t('jobs.filter.task')}
          </label>
          <select
            id={`${id}-task`}
            value={draft.task ?? ''}
            className={SELECT_CLASS}
            aria-invalid={errors.task ? true : undefined}
            onChange={(event) => set('task', event.target.value)}
          >
            <option value="">{t('jobs.filter.allTasks')}</option>
            {taskNames.map((name) => (
              <option key={name} value={name}>
                {taskLabel(name)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-status`} className="text-sm font-medium">
            {t('jobs.filter.status')}
          </label>
          <select
            id={`${id}-status`}
            value={draft.status ?? 'failed'}
            className={SELECT_CLASS}
            aria-invalid={errors.status ? true : undefined}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                status:
                  event.target.value === 'failed'
                    ? null
                    : (event.target.value as StatusFilter),
              }))
            }
          >
            {STATUS_FILTERS.map((value) => (
              <option key={value} value={value}>
                {t(`jobs.statusFilter.${value}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-from`} className="text-sm font-medium">
            {t('jobs.filter.from')}
          </label>
          <Input
            id={`${id}-from`}
            ref={fromField}
            type="date"
            value={draft.from ?? ''}
            className="min-h-11"
            aria-invalid={errors.from ? true : undefined}
            aria-describedby={dateError ? dateErrorId : undefined}
            onChange={(event) => set('from', event.target.value)}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-to`} className="text-sm font-medium">
            {t('jobs.filter.to')}
          </label>
          <Input
            id={`${id}-to`}
            ref={toField}
            type="date"
            value={draft.to ?? ''}
            className="min-h-11"
            aria-invalid={errors.to ? true : undefined}
            aria-describedby={dateError ? dateErrorId : undefined}
            onChange={(event) => set('to', event.target.value)}
          />
        </div>
      </div>
      {dateError ? (
        <p id={dateErrorId} className="text-sm font-medium text-destructive">
          {dateError}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Button type="submit" className="min-h-11">
          {t('jobs.filter.apply')}
        </Button>
        {isFiltered(filters) ? (
          <Button
            type="button"
            variant="outline"
            className="min-h-11"
            onClick={onClear}
          >
            {t('jobs.filter.clear')}
          </Button>
        ) : null}
      </div>
    </form>
  );
}

export default JobsPage;
