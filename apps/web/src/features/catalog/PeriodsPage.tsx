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
import {
  createPeriod,
  fetchPeriods,
  PERIODS_QUERY_KEY,
  periodsListKey,
  updatePeriod,
  type AcademicPeriod,
} from './api';
import { CatalogTabs } from './CatalogTabs';
import { RangeItemDialog } from './RangeItemDialog';
import {
  PERIOD_MAX_SPAN_DAYS,
  PERIOD_TYPES,
  periodCreateBody,
  periodUpdateBody,
  type RangeFormValues,
} from './rangeRules';
import { RangesTable, type RangesTableState } from './RangesTable';
import { pageParam } from './rules';

// /catalog/periods (#38 E2): every academic period, active or not, for
// holders of catalog.manage, newest start first. "Add period" and each
// row's "Edit" open the period dialog (the type is read-only when editing);
// after a change the period queries are invalidated and a polite status
// says "Saved.". The page lives in the URL (?page=).

type Row = AcademicPeriod & { typeLabel: string };
type DialogState =
  { mode: 'create' } | { mode: 'edit'; period: AcademicPeriod };

export function PeriodsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('catalog.periods.title'));
  const titleId = useId();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = pageParam(searchParams);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saved, setSaved] = useState(false);

  const list = useQuery({
    queryKey: periodsListKey(page),
    queryFn: ({ signal }) => fetchPeriods(page, signal),
    placeholderData: keepPreviousData,
  });

  const typeLabel = (type: AcademicPeriod['period_type']) =>
    t(`catalog.periodTypes.${type}`);
  const typeOptions = PERIOD_TYPES.map((value) => ({
    value,
    label: typeLabel(value),
  }));

  function goToPage(next: number) {
    setSearchParams((previous) => {
      const params = new URLSearchParams(previous);
      if (next > 1) {
        params.set('page', String(next));
      } else {
        params.delete('page');
      }
      return params;
    });
  }

  // The edited period as loaded now (a Reload brings a new version).
  const editing =
    dialog?.mode === 'edit'
      ? (list.data?.items.find((item) => item.id === dialog.period.id) ??
        dialog.period)
      : null;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: PERIODS_QUERY_KEY });
  }

  async function submit(values: RangeFormValues) {
    if (!editing) {
      await createPeriod(periodCreateBody(values));
    } else {
      const body = periodUpdateBody(editing, values);
      if (body) {
        await updatePeriod(editing.id, body);
      }
    }
    await refresh();
  }

  async function setActive(active: boolean) {
    if (!editing) {
      return;
    }
    await updatePeriod(editing.id, {
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
          items: list.data.items.map((period) => ({
            ...period,
            typeLabel: typeLabel(period.period_type),
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
          {t('catalog.periods.title')}
        </h1>
        <Button type="button" onClick={() => open({ mode: 'create' })}>
          {t('catalog.periods.add')}
        </Button>
      </div>
      <p role="status" className="text-sm">
        {saved ? t('catalog.saved') : null}
      </p>
      <RangesTable
        state={tableState}
        dates="split"
        labelledBy={titleId}
        emptyText={t('catalog.periods.empty')}
        onPage={goToPage}
        onEdit={(period) => open({ mode: 'edit', period })}
      />
      <RangeItemDialog
        open={dialog !== null}
        title={editing ? t('catalog.periods.edit') : t('catalog.periods.add')}
        typeOptions={typeOptions}
        typeLocked={editing !== null}
        maxSpanDays={PERIOD_MAX_SPAN_DAYS}
        deactivateHelp={t('catalog.periods.deactivateHelp')}
        item={
          editing
            ? {
                name: editing.name,
                type: editing.period_type,
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
