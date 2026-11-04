import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useParams, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { isUuid } from '@/features/payroll/periodRules';
import { useFormatters } from '@/lib/formatters';
import { todayInZone } from '@/lib/time';
import {
  EXPENSES_KEY,
  fetchExpenseTemplate,
  fetchTemplateExpenses,
  TEMPLATES_KEY,
  templateExpensesKey,
  templateKey,
  updateExpenseTemplate,
  type ExpenseTemplate,
  type ExpenseTemplateUpdate,
} from '../api';
import { ExpensesTabs } from '../ExpensesTabs';
import { isGone, isVersionConflict } from '../forms';
import { useListNotices, type StaleNotice } from '../useListNotices';
import type { SaveResult } from '../useRowSave';
import { EndTemplateDialog } from './EndTemplateDialog';
import {
  generatedPage,
  templateCapabilities,
  templateUpdateBody,
  type EndFormValues,
  type TemplateFormValues,
} from './rules';
import { TemplateDialog } from './TemplateDialog';
import { BackLink, TemplateDetailView } from './TemplateDetailView';
import { usePickers } from './usePickers';

// /expenses/templates/:templateId (M7-13b C, D2-D6), for expense.view
// holders, with its queries and the dialogs. Every save writes the answer
// into the cache and then refetches the template (the job moves the next
// and last periods without a new version, QA #116 D1) and its expenses.
// The #458 config-page rules (E2 (a)):
// - closing a dialog after a failed save refetches, however it closes, and
//   every action waits for that refetch; a close with no attempt sends
//   nothing (D6);
// - a failed refetch keeps the template with the out-of-date alert and
//   holds the actions; nothing says "reloaded" unless it did (C3);
// - a pause or resume refused as gone or changed closes, refetches and says
//   so (the categories page's rule).

type OpenDialog = 'edit' | 'end' | 'pause' | 'resume';
type Block = { kind: 'refreshing' } | { kind: 'stale'; error: unknown };

const L = 'finance.templates';
const DT = `${L}.detail`;

export function TemplatePage() {
  const { t } = useTranslation();
  useDocumentTitle(t(`${DT}.title`));
  const { templateId = '' } = useParams();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = generatedPage(searchParams);
  const valid = isUuid(templateId);
  const id = templateId.toLowerCase();
  const queryClient = useQueryClient();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const can = templateCapabilities(user);
  const timeZone = useCenterTimeZone();
  const { formatLocalDate } = useFormatters();
  const pickers = usePickers();
  const [dialog, setDialog] = useState<OpenDialog | null>(null);
  const [block, setBlock] = useState<Block | null>(null);
  const [confirmError, setConfirmError] = useState<unknown>(null);
  const saveFailed = useRef(false);
  const created =
    (location.state as { created?: boolean } | null)?.created === true;

  const key = templateKey(id);
  const template = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchExpenseTemplate(id, signal),
    enabled: valid,
    meta: { expectedStatuses: [403, 404] },
  });
  const generated = useQuery({
    queryKey: templateExpensesKey(id, page),
    queryFn: ({ signal }) => fetchTemplateExpenses(id, page, signal),
    enabled: valid,
    placeholderData: keepPreviousData,
    meta: { expectedStatuses: [403] },
  });
  const notices = useListNotices(
    template.isSuccess && !template.isFetching,
    (notice: StaleNotice, reloaded: boolean) =>
      notice === 'gone'
        ? reloaded
          ? t(`${DT}.gone`)
          : t(`${DT}.goneNotReloaded`)
        : reloaded
          ? t(`${DT}.conflictRefreshed`)
          : t(`${DT}.conflictNotReloaded`),
  );
  const [createdShown, setCreatedShown] = useState(false);
  if (created && !createdShown) {
    // Adjusting state while rendering: say it once (M7-12b's created rule).
    setCreatedShown(true);
    notices.announce(t(`${DT}.created`));
  }

  /** Refetches the template, its expenses and the list; whether the
   * template reloaded. Never rejects. */
  async function refresh(): Promise<boolean> {
    void queryClient.invalidateQueries({
      queryKey: [...TEMPLATES_KEY, 'list'],
    });
    void queryClient.invalidateQueries({
      queryKey: [...EXPENSES_KEY, 'template', id],
    });
    await queryClient.invalidateQueries({ queryKey: key });
    return queryClient.getQueryState(key)?.status !== 'error';
  }

  /** Refetches and holds every action until it lands (D6). */
  async function reload(): Promise<boolean> {
    setBlock({ kind: 'refreshing' });
    const reloaded = await refresh();
    const state = queryClient.getQueryState(key);
    setBlock(
      state?.status === 'error' ? { kind: 'stale', error: state.error } : null,
    );
    return reloaded;
  }

  /** Sends a change; the answer goes in the cache, then a refetch. */
  async function send(
    base: ExpenseTemplate,
    body: ExpenseTemplateUpdate,
  ): Promise<ExpenseTemplate> {
    let item: ExpenseTemplate;
    try {
      item = await updateExpenseTemplate(base.id, body);
    } catch (caught) {
      saveFailed.current = true;
      await pickers.reloadIfNeeded(caught);
      throw caught;
    }
    saveFailed.current = false;
    queryClient.setQueryData(key, item);
    void refresh();
    return item;
  }

  async function saveEdit(
    values: TemplateFormValues,
    base: ExpenseTemplate | null,
  ): Promise<SaveResult> {
    if (!base) return 'unchanged';
    const body = templateUpdateBody(values, base);
    if (!body) return 'unchanged';
    await send(base, body);
    notices.announce(t(`${DT}.saved`));
    return 'saved';
  }

  async function saveEnd(
    values: EndFormValues,
    base: ExpenseTemplate,
  ): Promise<SaveResult> {
    if (values.endsOn === base.ends_on) return 'unchanged';
    const item = await send(base, {
      version: base.version,
      ends_on: values.endsOn,
    });
    notices.announce(
      item.status === 'ended'
        ? t(`${DT}.ended`)
        : t(`${DT}.endSet`, { date: formatLocalDate(values.endsOn) }),
    );
    return 'saved';
  }

  async function setActive(base: ExpenseTemplate, active: boolean) {
    setConfirmError(null);
    let item: ExpenseTemplate;
    try {
      item = await send(base, { version: base.version, is_active: active });
    } catch (caught) {
      if (isGone(caught) || isVersionConflict(caught)) {
        saveFailed.current = false;
        setDialog(null);
        notices.stale(isGone(caught) ? 'gone' : 'conflict', await reload());
        return;
      }
      setConfirmError(caught);
      return;
    }
    setDialog(null);
    notices.announce(
      !active
        ? t(`${DT}.paused`)
        : item.status === 'ended'
          ? t(`${DT}.resumedEnded`)
          : t(`${DT}.resumed`, {
              date: formatLocalDate(item.next_period_start),
            }),
    );
  }

  function closeDialog() {
    setDialog(null);
    setConfirmError(null);
    if (saveFailed.current) {
      saveFailed.current = false;
      void reload();
    }
  }

  function open(next: OpenDialog) {
    notices.announce('');
    setConfirmError(null);
    setDialog(next);
  }

  const status =
    template.error instanceof ApiError ? template.error.status : undefined;
  if (
    status === 403 ||
    (generated.error instanceof ApiError && generated.error.status === 403)
  ) {
    return <ForbiddenPage />;
  }

  let content;
  if (!valid || status === 404) {
    content = (
      <div
        role="status"
        className="flex min-w-0 flex-col items-start gap-2 rounded-md border border-border p-4"
        data-template-not-found
      >
        <p className="font-medium">{t(`${DT}.notFound`)}</p>
        {notices.announcement ? (
          <p className="text-sm">{notices.announcement}</p>
        ) : null}
        <BackLink />
      </div>
    );
  } else if (!template.data) {
    content = template.isError ? (
      <ErrorAlert
        error={template.error}
        onRetry={() => void template.refetch()}
      />
    ) : (
      <LoadingState rows={6} />
    );
  }
  if (content || !template.data) {
    return (
      <div className="flex min-w-0 flex-col gap-8">
        <ExpensesTabs />
        <div className="flex min-w-0 flex-col gap-4">
          <BackLink />
          {content}
        </div>
      </div>
    );
  }

  const data = template.data;
  const staleError =
    block?.kind === 'stale'
      ? block.error
      : template.isError
        ? template.error
        : null;
  const today = todayInZone(timeZone);

  return (
    <>
      <TemplateDetailView
        template={data}
        can={can}
        generated={
          generated.data
            ? { status: 'ready', page: generated.data }
            : generated.isError
              ? {
                  status: 'error',
                  error: generated.error,
                  onRetry: () => void generated.refetch(),
                }
              : { status: 'loading' }
        }
        onPage={(next) =>
          setSearchParams(
            next > 1 ? new URLSearchParams({ page: String(next) }) : {},
          )
        }
        blocked={block !== null || staleError !== null || dialog !== null}
        staleError={staleError}
        onReload={() => void reload()}
        onEdit={() => open('edit')}
        onPause={() => open('pause')}
        onResume={() => open('resume')}
        onEnd={() => open('end')}
        announcement={notices.announcement}
      />
      {can.manage ? (
        <>
          <TemplateDialog
            open={dialog === 'edit'}
            template={data}
            today={today}
            categories={pickers.categoryPicker}
            vendors={pickers.vendorPicker}
            onSubmit={saveEdit}
            onReload={refresh}
            onClose={closeDialog}
          />
          <EndTemplateDialog
            open={dialog === 'end'}
            template={data}
            today={today}
            onSubmit={saveEnd}
            onReload={refresh}
            onClose={closeDialog}
          />
          <ConfirmDialog
            open={dialog === 'pause' || dialog === 'resume'}
            title={t(
              dialog === 'resume' ? `${L}.resume.title` : `${L}.pause.title`,
              { name: data.name },
            )}
            body={
              dialog === 'resume' ? t(`${L}.resume.body`) : t(`${L}.pause.body`)
            }
            confirmLabel={
              dialog === 'resume'
                ? t(`${L}.resume.confirm`)
                : t(`${L}.pause.confirm`)
            }
            alert={confirmError ? <ErrorAlert error={confirmError} /> : null}
            onConfirm={() => setActive(data, dialog === 'resume')}
            onCancel={closeDialog}
          />
        </>
      ) : null}
    </>
  );
}
