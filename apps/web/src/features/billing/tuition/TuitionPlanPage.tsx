import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useCan } from '@/features/auth/useSession';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { BillingTabs } from '../BillingTabs';
import { useCustomPriceLabels } from '../custom-prices/labels';
import { UNBILLED_QUERY_KEY } from '../unbilled/api';
import {
  deletePlan,
  fetchPlan,
  pausePlan,
  planKey,
  resumePlan,
  TUITION_QUERY_KEY,
  updatePlan,
  type TuitionPauseCreate,
  type TuitionPlanDetail,
  type TuitionPlanUpdate,
  type TuitionResume,
} from './api';
import { useTuitionLabels } from './labels';
import {
  DueOffsetDialog,
  EndDialog,
  PauseDialog,
  ResumeDialog,
} from './PlanDialogs';
import {
  canDeletePlan,
  canEndPlan,
  pauseStarts,
  periodDisplay,
  resumeStarts,
} from './rules';
import {
  ChargeStatusBadge,
  PeriodStateBadge,
  PlanStatusBadge,
} from './TuitionBadges';

// /billing/tuition-plans/:planId (#374 E), for billing.view holders: the
// plan's summary, its periods (the upcoming ones with their state, then the
// charged ones with their amount and charge status), its pauses and, for
// billing.manage holders, the actions that apply (Pause, Resume, End, the
// due offset, Delete while nothing is charged). After a change the plan
// queries and the unbilled lists are invalidated. An unknown id is the
// not-found message with a link back; "Back to tuition plans" keeps the
// list's filters and page.

function useBackTo(): string {
  const location = useLocation();
  const state: unknown = location.state;
  const search =
    typeof state === 'object' &&
    state !== null &&
    'listSearch' in state &&
    typeof state.listSearch === 'string'
      ? state.listSearch
      : '';
  return `/billing/tuition-plans${search.startsWith('?') ? search : ''}`;
}

function BackLink() {
  const { t } = useTranslation();
  const to = useBackTo();
  return (
    <p>
      <Link to={to} className={CATALOG_LINK_CLASS}>
        {t('billing.tuition.back')}
      </Link>
    </p>
  );
}

export function TuitionPlanPage() {
  const { t } = useTranslation();
  const { planId = '' } = useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const backTo = useBackTo();
  const canManage = useCan('billing.manage');
  const detail = useQuery({
    queryKey: planKey(planId),
    queryFn: ({ signal }) => fetchPlan(planId, signal),
  });
  // A malformed id is a 422 from the API: not found too (QA O6).
  const notFound =
    detail.error instanceof ApiError &&
    (detail.error.status === 404 || detail.error.status === 422);
  useDocumentTitle(
    notFound
      ? t('billing.tuition.notFound')
      : detail.data
        ? t('billing.tuition.detailTitleOf', {
            student: detail.data.student.name,
          })
        : t('billing.tuition.detailTitle'),
  );

  if (detail.isError) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <div className="flex flex-col gap-4">
          <h1 className="text-2xl font-semibold">
            {notFound
              ? t('billing.tuition.notFound')
              : t('billing.tuition.detailTitle')}
          </h1>
          {notFound ? (
            <p>{t('billing.tuition.notFoundHelp')}</p>
          ) : (
            <ErrorAlert
              error={detail.error}
              onRetry={() => void detail.refetch()}
            />
          )}
          <BackLink />
        </div>
      </div>
    );
  }
  if (!detail.data) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <LoadingState />
      </div>
    );
  }

  const plan = detail.data;

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: TUITION_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: UNBILLED_QUERY_KEY }),
    ]);
  }

  /** Shows the plan the API answered with, then refreshes the lists. */
  async function saved(next: TuitionPlanDetail) {
    queryClient.setQueryData(planKey(planId), next);
    await refresh();
  }

  return (
    <TuitionPlanPageView
      plan={plan}
      canManage={canManage}
      onPause={async (body) => saved(await pausePlan(planId, body))}
      onResume={async (body) => saved(await resumePlan(planId, body))}
      onUpdate={async (body) => saved(await updatePlan(planId, body))}
      onDelete={async () => {
        await deletePlan(planId, plan.version);
        queryClient.removeQueries({ queryKey: planKey(planId) });
        await refresh();
        void navigate(backTo);
      }}
      onReload={() => void refresh()}
    />
  );
}

type OpenDialog = 'pause' | 'resume' | 'end' | 'due' | 'delete' | null;

export interface TuitionPlanPageViewProps {
  plan: TuitionPlanDetail;
  /** billing.manage: the actions. */
  canManage: boolean;
  /** Each action; rejects with the error to show. */
  onPause: (body: TuitionPauseCreate) => Promise<void>;
  onResume: (body: TuitionResume) => Promise<void>;
  onUpdate: (body: TuitionPlanUpdate) => Promise<void>;
  onDelete: () => Promise<void>;
  /** Reloads the plan (after a 409 version_conflict). */
  onReload: () => void;
}

/** The plan page's content, without any query. */
export function TuitionPlanPageView({
  plan,
  canManage,
  onPause,
  onResume,
  onUpdate,
  onDelete,
  onReload,
}: TuitionPlanPageViewProps) {
  const { t } = useTranslation();
  const labels = useTuitionLabels();
  const priceLabels = useCustomPriceLabels();
  const { formatMoney } = useFormatters();
  const id = useId();
  const ids = {
    periods: `${id}-periods`,
    upcoming: `${id}-upcoming`,
    recent: `${id}-recent`,
    pauses: `${id}-pauses`,
  };
  const [open, setOpen] = useState<OpenDialog>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState('');

  function show(dialog: OpenDialog) {
    setAnnouncement('');
    setDeleteError(null);
    setOpen(dialog);
  }

  function done(key: string) {
    return () => {
      setOpen(null);
      setAnnouncement(t(key as 'billing.tuition.pause.done'));
    };
  }

  const ended = plan.status === 'ended';
  const actions = canManage
    ? {
        pause: pauseStarts(plan).length > 0,
        resume: resumeStarts(plan).length > 0,
        end: canEndPlan(plan),
        due: !ended,
        delete: canDeletePlan(plan),
      }
    : null;
  const service = priceLabels.service(plan.service_price);
  const terms: [string, ReactNode][] = [
    [t('billing.tuition.columns.student'), plan.student.name],
    [t('billing.tuition.columns.family'), plan.family.name],
    [
      t('billing.tuition.summary.price'),
      canManage ? (
        <Link
          to={`/billing/prices/${plan.service_price.id}`}
          className={TEXT_LINK_CLASS}
        >
          {service}
        </Link>
      ) : (
        service
      ),
    ],
    [t('billing.tuition.summary.frequency'), labels.frequency(plan.frequency)],
    [t('billing.tuition.columns.amount'), labels.amount(plan)],
    [t('billing.tuition.columns.startsOn'), labels.startsOn(plan)],
    [t('billing.tuition.columns.endsOn'), labels.endsOn(plan)],
    [t('billing.tuition.summary.due'), labels.due(plan)],
    [
      t('billing.tuition.columns.status'),
      <PlanStatusBadge key="status" status={plan.status} />,
    ],
    [
      t('billing.tuition.summary.group'),
      plan.group?.name ?? t('billing.tuition.individual'),
    ],
  ];

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-3">
        <BackLink />
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-semibold break-words">
            {t('billing.tuition.detailTitleOf', { student: plan.student.name })}
          </h1>
          {actions ? (
            <div className="flex flex-wrap gap-2">
              {actions.pause ? (
                <Button type="button" onClick={() => show('pause')}>
                  {t('billing.tuition.pause.action')}
                </Button>
              ) : null}
              {actions.resume ? (
                <Button type="button" onClick={() => show('resume')}>
                  {t('billing.tuition.resume.action')}
                </Button>
              ) : null}
              {actions.end ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => show('end')}
                >
                  {t('billing.tuition.end.action')}
                </Button>
              ) : null}
              {actions.due ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => show('due')}
                >
                  {t('billing.tuition.dueOffset.action')}
                </Button>
              ) : null}
              {actions.delete ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => show('delete')}
                >
                  {t('billing.tuition.delete')}
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">{labels.plan(plan)}</p>
        <p role="status" className="text-sm">
          {announcement}
        </p>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
          {terms.map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="font-medium">{term}</dt>
              <dd className="break-words">{value}</dd>
            </div>
          ))}
        </dl>
      </div>

      <section aria-labelledby={ids.periods} className="flex flex-col gap-4">
        <h2 id={ids.periods} className="text-lg font-semibold">
          {t('billing.tuition.periods.title')}
        </h2>
        <h3 id={ids.upcoming} className="font-medium">
          {t('billing.tuition.periods.upcoming')}
        </h3>
        {plan.upcoming_periods.length === 0 ? (
          <p data-tuition-empty="upcoming">
            {t('billing.tuition.periods.noUpcoming')}
          </p>
        ) : (
          <Table aria-labelledby={ids.upcoming}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('billing.tuition.periods.period')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.tuition.periods.state')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {plan.upcoming_periods.map((period) => {
                const display = periodDisplay(period, plan);
                return (
                  <TableRow
                    key={period.period_start}
                    data-upcoming-period={period.period_start}
                  >
                    <TableCell>{labels.period(period)}</TableCell>
                    <TableCell className="whitespace-normal">
                      <span className="flex flex-col items-start gap-1">
                        <PeriodStateBadge display={display} />
                        {display === 'due_unbilled' && period.reason ? (
                          <span className="text-sm text-muted-foreground">
                            {t(`billing.tuition.reasons.${period.reason}`)}
                          </span>
                        ) : null}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        <h3 id={ids.recent} className="font-medium">
          {t('billing.tuition.periods.recent')}
        </h3>
        {plan.recent_periods.length === 0 ? (
          <p data-tuition-empty="recent">
            {t('billing.tuition.periods.noRecent')}
          </p>
        ) : (
          <Table aria-labelledby={ids.recent}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('billing.tuition.periods.period')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.tuition.periods.amount')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.tuition.periods.charge')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {plan.recent_periods.map((period) => (
                <TableRow
                  key={period.charge_id}
                  data-recent-period={period.period_start}
                >
                  <TableCell>{labels.period(period)}</TableCell>
                  <TableCell>{formatMoney(period.amount)}</TableCell>
                  <TableCell>
                    <ChargeStatusBadge status={period.status} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section aria-labelledby={ids.pauses} className="flex flex-col gap-4">
        <h2 id={ids.pauses} className="text-lg font-semibold">
          {t('billing.tuition.pauses.title')}
        </h2>
        {plan.pauses.length === 0 ? (
          <p>{t('billing.tuition.pauses.empty')}</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {plan.pauses.map((pause) => (
              <li key={pause.id} data-pause-id={pause.id}>
                {t('billing.tuition.pauses.range', {
                  from: labels.date(pause.starts_on),
                  to:
                    pause.ends_on === null
                      ? t('billing.tuition.untilResumed')
                      : labels.date(pause.ends_on),
                })}
              </li>
            ))}
          </ul>
        )}
      </section>

      {actions ? (
        <>
          <PauseDialog
            plan={plan}
            open={open === 'pause'}
            onSubmit={async (body) => {
              await onPause(body);
              done('billing.tuition.pause.done')();
            }}
            onReload={onReload}
            onClose={() => setOpen(null)}
          />
          <ResumeDialog
            plan={plan}
            open={open === 'resume'}
            onSubmit={async (body) => {
              await onResume(body);
              done('billing.tuition.resume.done')();
            }}
            onReload={onReload}
            onClose={() => setOpen(null)}
          />
          <EndDialog
            plan={plan}
            open={open === 'end'}
            onSubmit={async (body) => {
              await onUpdate(body);
              done('billing.tuition.end.done')();
            }}
            onReload={onReload}
            onClose={() => setOpen(null)}
          />
          <DueOffsetDialog
            plan={plan}
            open={open === 'due'}
            onSubmit={async (body) => {
              await onUpdate(body);
              done('billing.tuition.dueOffset.done')();
            }}
            onReload={onReload}
            onClose={() => setOpen(null)}
          />
          <ConfirmDialog
            open={open === 'delete'}
            title={t('billing.tuition.deleteDialog.title')}
            body={t('billing.tuition.deleteDialog.body', {
              student: plan.student.name,
              plan: labels.plan(plan),
            })}
            confirmLabel={t('billing.tuition.delete')}
            destructive
            alert={deleteError ? <ErrorAlert error={deleteError} /> : null}
            onConfirm={async () => {
              setDeleteError(null);
              try {
                await onDelete();
              } catch (caught) {
                setDeleteError(caught);
                if (isConflict(caught)) onReload();
              }
            }}
            onCancel={() => {
              setOpen(null);
              setDeleteError(null);
            }}
          />
        </>
      ) : null}
    </div>
  );
}

function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'version_conflict';
}
