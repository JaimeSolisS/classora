import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  auditEventsKey,
  fetchAuditEvents,
  fetchRecordTypes,
  RECORD_TYPES_KEY,
  shortId,
  type AuditEvent,
} from './api';
import { AuditEventList, type AuditListState } from './AuditEventList';
import {
  filtersFromParams,
  hasErrors,
  isFiltered,
  NO_FILTERS,
  paramsFor,
  validateFilters,
  type AuditFilters,
  type FilterErrors,
} from './filters';

// /audit (#120 C): the read-only audit log for audit.view holders (Owner,
// Administrator). Filters (record type and ID, person, from/to center-local
// days) live in the URL; the form applies them on submit, after the C2
// checks (no request while they fail, also for a hand-edited URL). The
// person filter is set from a row or a link and shown as a removable chip
// with the name. A row's history buttons replace the filters with that one.
// The list is newest first, 25 per page, with "Load more"; a polite live
// region says how many events are shown after each load. Only GET requests.

const SELECT_CLASS =
  'min-h-11 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-destructive md:text-sm';
const INPUT_CLASS = 'min-h-11';

function useAuditList(
  filters: AuditFilters,
  timeZone: string,
  enabled: boolean,
) {
  return useInfiniteQuery({
    queryKey: auditEventsKey(filters, timeZone),
    queryFn: ({ pageParam, signal }) =>
      fetchAuditEvents(filters, timeZone, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled,
    retry: false,
  });
}

export function AuditPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('audit.title'));
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const heading = useRef<HTMLHeadingElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = filtersFromParams(searchParams);
  const urlErrors = validateFilters(filters);
  const valid = !hasErrors(urlErrors);
  // Names of people chosen from a row, until the list names them itself.
  const [knownNames, setKnownNames] = useState<Record<string, string>>({});

  const recordTypes = useQuery({
    queryKey: RECORD_TYPES_KEY,
    queryFn: ({ signal }) => fetchRecordTypes(signal),
  });
  const list = useAuditList(filters, timeZone, valid);
  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const cursorInvalid =
    list.error instanceof ApiError && list.error.code === 'invalid_cursor';

  function apply(next: AuditFilters) {
    setSearchParams(paramsFor(next));
  }

  function showOnly(next: AuditFilters) {
    apply(next);
    // The clicked button may be gone: focus the page heading.
    heading.current?.focus();
  }

  function restart() {
    void queryClient.resetQueries({
      queryKey: auditEventsKey(filters, timeZone),
    });
  }

  let listState: AuditListState;
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

  const actorName = filters.actorId
    ? (knownNames[filters.actorId] ??
      items.find((event) => event.actor_id === filters.actorId)?.actor
        ?.display_name ??
      t('audit.list.unknownUser', { id: shortId(filters.actorId) }))
    : null;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1
          ref={heading}
          tabIndex={-1}
          className="text-2xl font-semibold outline-none"
        >
          {t('audit.title')}
        </h1>
        <p>{t('audit.intro')}</p>
      </div>
      <FilterForm
        // A new URL (back/forward, a link, a row action) resets the form.
        key={searchParams.toString()}
        filters={filters}
        initialErrors={urlErrors}
        recordTypes={recordTypes.data ?? []}
        actorName={actorName}
        onApply={apply}
        onRemoveActor={() => apply({ ...filters, actorId: null })}
        onClear={() => {
          apply(NO_FILTERS);
          heading.current?.focus();
        }}
      />
      <p aria-live="polite" aria-atomic="true" className="text-sm">
        {valid && list.data
          ? t('audit.list.shown', { count: items.length })
          : null}
      </p>
      {valid ? (
        <AuditEventList
          state={listState}
          cursorInvalid={cursorInvalid}
          onRestart={restart}
          onRecord={(event: AuditEvent) =>
            showOnly({
              ...NO_FILTERS,
              recordType: event.record_type,
              recordId: event.record_id,
            })
          }
          onActor={(event: AuditEvent) => {
            const actorId = event.actor_id;
            if (!actorId) {
              return;
            }
            const name = event.actor?.display_name;
            if (name) {
              setKnownNames((names) => ({ ...names, [actorId]: name }));
            }
            showOnly({ ...NO_FILTERS, actorId });
          }}
        />
      ) : (
        <p>{t('audit.filter.fixFirst')}</p>
      )}
    </div>
  );
}

interface FilterFormProps {
  filters: AuditFilters;
  initialErrors: FilterErrors;
  recordTypes: readonly string[];
  actorName: string | null;
  onApply: (filters: AuditFilters) => void;
  onRemoveActor: () => void;
  onClear: () => void;
}

function FilterForm({
  filters,
  initialErrors,
  recordTypes,
  actorName,
  onApply,
  onRemoveActor,
  onClear,
}: FilterFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const [draft, setDraft] = useState<AuditFilters>(filters);
  const [errors, setErrors] = useState<FilterErrors>(initialErrors);
  const typeField = useRef<HTMLSelectElement>(null);
  const idField = useRef<HTMLInputElement>(null);
  const fromField = useRef<HTMLInputElement>(null);
  const toField = useRef<HTMLInputElement>(null);
  // An unknown type from the URL (a named event's) stays selectable.
  const types =
    filters.recordType && !recordTypes.includes(filters.recordType)
      ? [...recordTypes, filters.recordType].sort()
      : recordTypes;

  function set<K extends keyof AuditFilters>(key: K, value: string) {
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
    if (found.record === 'pair') {
      (draft.recordType ? idField : typeField).current?.focus();
    } else if (found.recordType) {
      typeField.current?.focus();
    } else if (found.recordId) {
      idField.current?.focus();
    } else if (found.from) {
      fromField.current?.focus();
    } else if (found.to) {
      toField.current?.focus();
    }
  }

  const recordError = errors.record
    ? t('audit.filter.errors.recordPair')
    : errors.recordType
      ? t('audit.filter.errors.recordType')
      : errors.recordId
        ? t('audit.filter.errors.recordId')
        : null;
  const dateError =
    errors.to === 'beforeFrom'
      ? t('audit.filter.errors.dateOrder')
      : errors.from || errors.to
        ? t('audit.filter.errors.date')
        : null;
  const recordErrorId = `${id}-record-error`;
  const dateErrorId = `${id}-date-error`;

  return (
    <form
      noValidate
      onSubmit={submit}
      aria-label={t('audit.filter.label')}
      className="flex min-w-0 flex-col gap-4"
    >
      <div className="grid min-w-0 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-type`} className="text-sm font-medium">
            {t('audit.filter.recordType')}
          </label>
          <select
            id={`${id}-type`}
            ref={typeField}
            value={draft.recordType ?? ''}
            className={SELECT_CLASS}
            aria-invalid={
              errors.record === 'pair' && !draft.recordType
                ? true
                : errors.recordType
                  ? true
                  : undefined
            }
            aria-describedby={recordError ? recordErrorId : undefined}
            onChange={(event) => set('recordType', event.target.value)}
          >
            <option value="">{t('audit.filter.allTypes')}</option>
            {types.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-record`} className="text-sm font-medium">
            {t('audit.filter.recordId')}
          </label>
          <Input
            id={`${id}-record`}
            ref={idField}
            value={draft.recordId ?? ''}
            className={`${INPUT_CLASS} font-mono`}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={
              (errors.record === 'pair' && !draft.recordId) || errors.recordId
                ? true
                : undefined
            }
            aria-describedby={recordError ? recordErrorId : undefined}
            onChange={(event) => set('recordId', event.target.value)}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-from`} className="text-sm font-medium">
            {t('audit.filter.from')}
          </label>
          <Input
            id={`${id}-from`}
            ref={fromField}
            type="date"
            value={draft.from ?? ''}
            className={INPUT_CLASS}
            aria-invalid={errors.from ? true : undefined}
            aria-describedby={dateError ? dateErrorId : undefined}
            onChange={(event) => set('from', event.target.value)}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-to`} className="text-sm font-medium">
            {t('audit.filter.to')}
          </label>
          <Input
            id={`${id}-to`}
            ref={toField}
            type="date"
            value={draft.to ?? ''}
            className={INPUT_CLASS}
            aria-invalid={errors.to ? true : undefined}
            aria-describedby={dateError ? dateErrorId : undefined}
            onChange={(event) => set('to', event.target.value)}
          />
        </div>
      </div>
      {recordError ? (
        <p id={recordErrorId} className="text-sm font-medium text-destructive">
          {recordError}
        </p>
      ) : null}
      {dateError ? (
        <p id={dateErrorId} className="text-sm font-medium text-destructive">
          {dateError}
        </p>
      ) : null}
      {filters.actorId ? (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-sm font-medium">
            {t('audit.filter.person')}
          </span>
          <span className="inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border border-border px-3 text-sm">
            <span className="break-words">{actorName}</span>
            <Button
              type="button"
              variant="ghost"
              className="min-h-11 min-w-11"
              aria-label={t('audit.filter.removePerson', { name: actorName })}
              onClick={onRemoveActor}
            >
              <span aria-hidden="true">×</span>
            </Button>
          </span>
          {errors.actorId ? (
            <p className="text-sm font-medium text-destructive">
              {t('audit.filter.errors.recordId')}
            </p>
          ) : null}
        </div>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Button type="submit" className="min-h-11">
          {t('audit.filter.apply')}
        </Button>
        {isFiltered(filters) ? (
          <Button
            type="button"
            variant="outline"
            className="min-h-11"
            onClick={onClear}
          >
            {t('audit.filter.clear')}
          </Button>
        ) : null}
      </div>
    </form>
  );
}

export default AuditPage;
