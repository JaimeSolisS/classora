import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Button } from '@/components/ui/button';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { SELECT_CLASS } from '@/features/students/styles';
import { todayInZone } from '@/lib/time';
import {
  CLOSURES_QUERY_KEY,
  closuresListKey,
  createClosure,
  fetchClosures,
  updateClosure,
  type Closure,
} from './api';
import { CatalogTabs } from './CatalogTabs';
import { RangeItemDialog } from './RangeItemDialog';
import {
  CLOSURE_MAX_SPAN_DAYS,
  CLOSURE_TYPES,
  closureCreateBody,
  closureUpdateBody,
  yearOptions,
  yearParam,
  yearRange,
  type RangeFormValues,
} from './rangeRules';
import { RangesTable, type RangesTableState } from './RangesTable';
import { pageParam } from './rules';

// /catalog/closures (#38 E3): the closures (active or not) that overlap the
// chosen year, for holders of catalog.manage, by start date. The year
// filter offers the current year in the center time zone ± 2 (default the
// current year) and is sent as from/to; it and the page live in the URL
// (?year=, ?page=). A link to another year (1900-2999) adds it to the
// select. "Add closure" and each row's "Edit" open the closure
// dialog; after a change the closure queries are invalidated.

type Row = Closure & { typeLabel: string };
type DialogState = { mode: 'create' } | { mode: 'edit'; closure: Closure };

export function ClosuresPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('catalog.closures.title'));
  const titleId = useId();
  const yearId = useId();
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const currentYear = Number(todayInZone(timeZone).slice(0, 4));
  const [searchParams, setSearchParams] = useSearchParams();
  const year = yearParam(searchParams, currentYear);
  const nearby = yearOptions(currentYear);
  const years = nearby.includes(year)
    ? nearby
    : [...nearby, year].sort((a, b) => a - b);
  const page = pageParam(searchParams);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saved, setSaved] = useState(false);

  const list = useQuery({
    queryKey: closuresListKey(year, page),
    queryFn: ({ signal }) => fetchClosures(yearRange(year), page, signal),
    placeholderData: keepPreviousData,
  });

  const typeLabel = (type: Closure['closure_type']) =>
    t(`catalog.closureTypes.${type}`);
  const typeOptions = CLOSURE_TYPES.map((value) => ({
    value,
    label: typeLabel(value),
  }));

  function setParam(name: 'page' | 'year', value: number, fallback: number) {
    setSearchParams((previous) => {
      const params = new URLSearchParams(previous);
      if (value !== fallback) {
        params.set(name, String(value));
      } else {
        params.delete(name);
      }
      if (name === 'year') {
        params.delete('page');
      }
      return params;
    });
  }

  // The edited closure as loaded now (a Reload brings a new version).
  const editing =
    dialog?.mode === 'edit'
      ? (list.data?.items.find((item) => item.id === dialog.closure.id) ??
        dialog.closure)
      : null;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: CLOSURES_QUERY_KEY });
  }

  async function submit(values: RangeFormValues) {
    if (!editing) {
      await createClosure(closureCreateBody(values));
    } else {
      const body = closureUpdateBody(editing, values);
      if (body) {
        await updateClosure(editing.id, body);
      }
    }
    await refresh();
  }

  async function setActive(active: boolean) {
    if (!editing) {
      return;
    }
    await updateClosure(editing.id, {
      version: editing.version,
      is_active: active,
    });
    await refresh();
  }

  function open(next: DialogState) {
    setSaved(false);
    setDialog(next);
  }

  const tableState: RangesTableState<Row> = list.data
    ? {
        status: 'ready',
        page: {
          ...list.data,
          items: list.data.items.map((closure) => ({
            ...closure,
            typeLabel: typeLabel(closure.closure_type),
          })),
        },
      }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex flex-col gap-8">
      <CatalogTabs />
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={titleId} className="text-2xl font-semibold">
          {t('catalog.closures.title')}
        </h1>
        <Button type="button" onClick={() => open({ mode: 'create' })}>
          {t('catalog.closures.add')}
        </Button>
      </div>
      <div className="flex max-w-40 flex-col gap-2">
        <label htmlFor={yearId} className="text-sm font-medium">
          {t('catalog.closures.year')}
        </label>
        <select
          id={yearId}
          className={SELECT_CLASS}
          value={year}
          onChange={(event) =>
            setParam('year', Number(event.target.value), currentYear)
          }
        >
          {years.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>
      <p role="status" className="text-sm">
        {saved ? t('catalog.saved') : null}
      </p>
      <RangesTable
        state={tableState}
        dates="combined"
        labelledBy={titleId}
        emptyText={t('catalog.closures.empty', { year })}
        onPage={(next) => setParam('page', next, 1)}
        onEdit={(closure) => open({ mode: 'edit', closure })}
      />
      <RangeItemDialog
        open={dialog !== null}
        title={editing ? t('catalog.closures.edit') : t('catalog.closures.add')}
        typeOptions={typeOptions}
        maxSpanDays={CLOSURE_MAX_SPAN_DAYS}
        deactivateHelp={t('catalog.closures.deactivateHelp')}
        item={
          editing
            ? {
                name: editing.name,
                type: editing.closure_type,
                startsOn: editing.starts_on,
                endsOn: editing.ends_on,
                isActive: editing.is_active,
                version: editing.version,
              }
            : null
        }
        onSubmit={submit}
        onSetActive={setActive}
        onReload={async () => {
          await list.refetch();
        }}
        onClose={(didSave) => {
          setDialog(null);
          setSaved(didSave);
        }}
      />
    </div>
  );
}
