import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { canAccess } from '@/features/auth/access';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import { useFormatters } from '@/lib/formatters';
import { todayInZone } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { PAYROLL_ACCESS } from './access';
import {
  fetchPeriodReview,
  finalizePeriod,
  PAYROLL_PERIODS_KEY,
  periodReviewKey,
  preparePeriod,
  recalculatePeriod,
  returnPeriod,
  UNRESOLVED_KEY,
  type PayrollPeriodPage,
  type PayrollPeriodReview,
} from './api';
import { FinalizePeriodDialog } from './FinalizePeriodDialog';
import { PayrollTabs } from './PayrollTabs';
import {
  isCode,
  isUnknownResult,
  isUuid,
  RECALCULATE_AFTER_PREPARE,
} from './periodRules';
import {
  OutOfRangeHelp,
  PeriodRefusal,
  PeriodReviewView,
  type PeriodAction,
  type PeriodCapabilities,
} from './PeriodReviewView';
import { ReturnPeriodDialog } from './ReturnPeriodDialog';

// /payroll/periods/:periodId (#110b C, D). One query, `GET …/lines`; every
// action refreshes the period, the list and the unresolved work afterwards,
// whatever it answered (D15), and no action can start until that refresh is
// done (D9). "May be out of date" is one piece of state: the review query's
// last fetch failed while it still holds data. It stays until a reload
// succeeds, and no action starts meanwhile.
//
// - A refused `prepare` changed nothing, so on `payroll_unresolved_work` or
//   `payroll_negative_total` the page runs `recalculate`, then refreshes, then
//   shows the refusal (D3).
// - Return: a 422 stays in the dialog; anything else closes it and shows here.
// - Finalize: the dialog keeps a snapshot of the review it opened on, and its
//   key (D7, D9). A cancelled password dialog keeps it open (D8); a lost
//   response keeps it open and refreshes; a settled refusal closes it and
//   shows here. When the refresh shows the period is no longer prepared, the
//   dialog closes.
// - A period that was open on this page and then reads 404 (#110 E8: a
//   delegated Administrator can't read an `open` period) left the user's
//   review: after their own send-back the page goes to the list with a
//   confirmation; after someone else's change it says the status changed,
//   never "page not found" (QA #459 D1). Only a first load's 404 is not found.
// - A lost finalize answer locks every action until its refresh lands, also
//   after the dialog is cancelled (QA #459 D2).
// - A period that fails to load with `payroll_amount_out_of_range` shows the
//   way out, and Recalculate for a payroll.prepare holder (C8).

/** Where the list reads a send-back confirmation (QA #459 D1). */
export interface PeriodsNotice {
  periodsNotice: string;
}

/** The tutor of a period in a cached list page, if any (C8's link). */
function cachedTutorId(
  queryClient: QueryClient,
  periodId: string,
): string | null {
  const pages = queryClient.getQueriesData<PayrollPeriodPage>({
    queryKey: [...PAYROLL_PERIODS_KEY, 'list'],
  });
  for (const [, page] of pages) {
    const row = page?.items.find((item) => item.id === periodId);
    if (row) return row.tutor_user_id;
  }
  return null;
}

function BackLink() {
  const { t } = useTranslation();
  return (
    <p>
      <Link
        to="/payroll/periods"
        data-periods-back
        className="inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6"
      >
        {t('payroll.period.back')}
      </Link>
    </p>
  );
}

type OpenDialog =
  { kind: 'return' } | { kind: 'finalize'; snapshot: PayrollPeriodReview };

export function PeriodReviewPage() {
  const { t } = useTranslation();
  const { periodId = '' } = useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const timeZone = useCenterTimeZone();
  const { formatLocalDate } = useFormatters();
  const valid = isUuid(periodId);
  const can: PeriodCapabilities = {
    prepare: canAccess(user, { permission: 'payroll.prepare' }),
    finalize: canAccess(user, { permission: 'payroll.finalize' }),
    openSession: canAccess(user, { permission: 'schedule.view' }),
    openPayPages: canAccess(user, PAYROLL_ACCESS),
  };

  const review = useQuery({
    queryKey: periodReviewKey(periodId),
    queryFn: ({ signal }) => fetchPeriodReview(periodId, signal),
    enabled: valid,
    // A missing period, or an open one for a delegated Administrator, is the
    // not-found page: a designed state, not an error to report.
    meta: { expectedStatuses: [404] },
  });
  const [running, setRunning] = useState<PeriodAction | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [failure, setFailure] = useState<unknown>(null);
  const [dialog, setDialog] = useState<OpenDialog | null>(null);
  // A finalize whose answer was lost: the refresh says what happened.
  const [finalizeUnknown, setFinalizeUnknown] = useState(false);
  // The refresh after a lost finalize answer hasn't landed (QA #459 D2).
  const [unknownRefresh, setUnknownRefresh] = useState(false);

  // The period was shown here, then a refresh read 404: it left the user's
  // review (QA #459 D1).
  const gone =
    review.data !== undefined &&
    review.error instanceof ApiError &&
    review.error.status === 404;
  const data = gone ? undefined : review.data;
  const period = data?.period;
  const title = period
    ? t('payroll.period.title', {
        name: period.tutor_display_name,
        dates: t('payroll.periods.dates', {
          start: formatLocalDate(period.starts_on),
          end: formatLocalDate(period.ends_on),
        }),
      })
    : t('payroll.period.fallbackTitle');
  useDocumentTitle(title);

  // The refresh shows the period isn't prepared any more: the finalize
  // dialog has nothing left to confirm (D9). Adjusting state while rendering.
  // After a lost answer, the landed refresh says what happened, whether the
  // dialog is still open or not.
  const status = period?.status;
  const settled =
    !unknownRefresh && status !== undefined && status !== 'prepared';
  if (settled && (dialog?.kind === 'finalize' || finalizeUnknown)) {
    if (dialog?.kind === 'finalize') setDialog(null);
    setAnnouncement(
      finalizeUnknown && status === 'finalized'
        ? t('payroll.period.done.finalized')
        : t('payroll.period.refused.statusChanged'),
    );
    setFinalizeUnknown(false);
    setFailure(null);
  }
  if (gone && dialog !== null) {
    setDialog(null);
  }

  /** D15: the period, the list and the unresolved work. Never rejects. */
  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: PAYROLL_PERIODS_KEY }),
      queryClient.invalidateQueries({ queryKey: UNRESOLVED_KEY }),
    ]);
  }

  function begin(action: PeriodAction) {
    setRunning(action);
    setFailure(null);
    setAnnouncement('');
  }

  async function recalculate() {
    begin('recalculate');
    let refused: unknown = null;
    try {
      await recalculatePeriod(periodId);
    } catch (caught) {
      refused = caught;
    }
    await refresh();
    setFailure(refused);
    setAnnouncement(refused ? '' : t('payroll.period.done.recalculated'));
    setRunning(null);
  }

  async function prepare() {
    begin('prepare');
    let refused: unknown = null;
    try {
      await preparePeriod(periodId);
    } catch (caught) {
      refused = caught;
      if (isCode(caught, ...RECALCULATE_AFTER_PREPARE)) {
        // D3: the refusal rolled back its own recalculation, so the stored
        // lines and the Unresolved page wouldn't show what it names.
        try {
          await recalculatePeriod(periodId);
        } catch {
          // The refresh shows whatever is stored.
        }
      }
    }
    await refresh();
    setFailure(refused);
    setAnnouncement(refused ? '' : t('payroll.period.done.prepared'));
    setRunning(null);
  }

  async function sendBack(reason: string) {
    try {
      await returnPeriod(periodId, reason);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 422) throw caught;
      setDialog(null);
      begin('return');
      await refresh();
      setFailure(caught);
      setRunning(null);
      return;
    }
    setDialog(null);
    begin('return');
    await refresh();
    const after = queryClient.getQueryState(periodReviewKey(periodId));
    if (after?.error instanceof ApiError && after.error.status === 404) {
      // A delegated Administrator can't read the open period any more.
      const state: PeriodsNotice = {
        periodsNotice: t('payroll.period.goneReturned'),
      };
      void navigate('/payroll/periods', { state });
      return;
    }
    setAnnouncement(t('payroll.period.done.returned'));
    setRunning(null);
  }

  async function finalize(preparedAt: string, idempotencyKey: string) {
    try {
      await finalizePeriod(periodId, preparedAt, idempotencyKey);
    } catch (caught) {
      if (isCode(caught, 'reauth_required')) throw caught;
      if (isUnknownResult(caught)) {
        setFinalizeUnknown(true);
        setUnknownRefresh(true);
        setFailure(caught);
        void refresh().finally(() => setUnknownRefresh(false));
        throw caught;
      }
      setDialog(null);
      begin('finalize');
      await refresh();
      setFailure(caught);
      setRunning(null);
      return;
    }
    setFinalizeUnknown(false);
    setDialog(null);
    begin('finalize');
    await refresh();
    setAnnouncement(t('payroll.period.done.finalized'));
    setRunning(null);
  }

  if (
    !valid ||
    (!gone && review.error instanceof ApiError && review.error.status === 404)
  ) {
    return <NotFoundPage />;
  }

  let content;
  if (gone) {
    content = (
      <div
        role="status"
        className="flex min-w-0 flex-col items-start gap-2 rounded-md border border-border p-4"
        data-period-gone
      >
        <p className="font-medium">{t('payroll.period.gone')}</p>
        <Link
          to="/payroll/periods"
          className="inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6"
        >
          {t('payroll.period.goneLink')}
        </Link>
      </div>
    );
  } else if (data) {
    content = (
      <PeriodReviewView
        review={data}
        today={todayInZone(timeZone)}
        user={{ id: user?.id ?? '', roles: user?.roles ?? [] }}
        can={can}
        running={running ?? (unknownRefresh ? 'finalize' : null)}
        refreshError={review.isError ? review.error : null}
        onReload={() => void review.refetch()}
        announcement={announcement}
        failure={failure}
        onRecalculate={() => void recalculate()}
        onPrepare={() => void prepare()}
        onReturn={() => {
          setAnnouncement('');
          setDialog({ kind: 'return' });
        }}
        onFinalize={() => {
          setAnnouncement('');
          setDialog({ kind: 'finalize', snapshot: data });
        }}
      />
    );
  } else if (review.isError) {
    const outOfRange = isCode(review.error, 'payroll_amount_out_of_range');
    const tutorId = cachedTutorId(queryClient, periodId);
    content = (
      <div className="flex min-w-0 flex-col gap-4" data-period-load-error>
        <ErrorAlert
          error={review.error}
          onRetry={() => void review.refetch()}
          detail={
            outOfRange ? (
              <OutOfRangeHelp
                error={review.error}
                tutorId={tutorId}
                can={can}
              />
            ) : undefined
          }
        />
        {outOfRange && can.prepare ? (
          <div>
            <Button
              type="button"
              variant="outline"
              className="min-h-11 md:min-h-9"
              disabled={running !== null}
              onClick={() => void recalculate()}
            >
              {running === 'recalculate' ? (
                <Spinner
                  label={t('payroll.period.actions.working')}
                  showLabel
                />
              ) : (
                t('payroll.period.actions.recalculate')
              )}
            </Button>
          </div>
        ) : null}
        {failure ? (
          <PeriodRefusal error={failure} tutorId={tutorId} can={can} />
        ) : null}
      </div>
    );
  } else {
    content = <LoadingState rows={6} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <PayrollTabs />
      <BackLink />
      <h1 className="text-2xl font-semibold break-words">{title}</h1>
      {content}
      <ReturnPeriodDialog
        open={dialog?.kind === 'return'}
        onSubmit={sendBack}
        onClose={() => setDialog(null)}
      />
      <FinalizePeriodDialog
        open={dialog?.kind === 'finalize'}
        review={dialog?.kind === 'finalize' ? dialog.snapshot : null}
        onSubmit={finalize}
        onClose={() => setDialog(null)}
        refreshing={review.isFetching}
      />
    </div>
  );
}
