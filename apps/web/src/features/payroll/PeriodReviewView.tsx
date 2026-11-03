import { CircleAlert } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { StateBadge } from '@/features/users/admin/StateBadge';
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import {
  PAY_ITEMS_ANCHOR,
  type PayrollLine,
  type PayrollPeriodReview,
} from './api';
import { usePayLabels } from './labels';
import { PeriodLines } from './PeriodLines';
import {
  canCarryForward,
  earliestPeriodId,
  finalizeBlocks,
  hasEnded,
  isCode,
  isNegativeAmount,
  isUnknownResult,
  outOfRangeSessionId,
  periodActions,
  periodPath,
  selfFinalizeReason,
  unresolvedWorkDetails,
  type FinalizeBlock,
  type ReviewerIdentity,
} from './periodRules';
import { PeriodStatusBadge } from './PeriodsView';
import { UNRESOLVED_REASONS } from './unresolvedUrl';

// A period's review (#110b C, D), without any query: the head, the warnings,
// the lines, the actions the user may take and why one is disabled, and the
// last refusal with its way out. The API decides; a hidden or disabled
// control is a convenience, and every refusal still has a message. While an
// action or the refresh after it runs, or while the page may be out of date,
// no action can start (#454 D2's lesson).

export type PeriodAction =
  'recalculate' | 'prepare' | 'return' | 'finalize' | 'carry' | 'adjust';

export interface PeriodCapabilities {
  /** payroll.prepare: recalculate, prepare, return. */
  prepare: boolean;
  /** payroll.finalize: return, finalize. */
  finalize: boolean;
  /** schedule.view: session links. */
  openSession: boolean;
  /** The Payroll pages (payroll.view or payroll.manage): items, unresolved. */
  openPayPages: boolean;
  /** payroll.manage on this tutor (own pay only for an Owner): "Correct
   * line" on a finalized period (M7-08b E1). */
  correct?: boolean;
}

export interface PeriodReviewViewProps {
  review: PayrollPeriodReview;
  /** The center's today. */
  today: LocalDate;
  user: ReviewerIdentity;
  can: PeriodCapabilities;
  /** An action, or the refresh after it, is running. */
  running: PeriodAction | null;
  /** The last reload failed: what's shown may be out of date. */
  refreshError: unknown;
  onReload: () => void;
  /** The last action's outcome, for the status region. */
  announcement: string;
  /** The last action's refusal. */
  failure: unknown;
  onRecalculate: () => void;
  onPrepare: () => void;
  onReturn: () => void;
  onFinalize: () => void;
  /** "Correct line" on a finalized period's line (M7-08b E1). */
  onCorrect?: (line: PayrollLine) => void;
  /** Carry the negative total forward (M7-08b E2). */
  onCarry?: () => void;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium break-words underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

const BUTTON_CLASS = 'min-h-11 md:min-h-9';

function Notice({
  children,
  ...data
}: { children: ReactNode } & Record<`data-${string}`, string | boolean>) {
  return (
    <div
      className="flex gap-3 rounded-md border border-border bg-muted/40 p-4 text-sm"
      {...data}
    >
      <CircleAlert aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
      <div className="flex min-w-0 flex-col items-start gap-2">{children}</div>
    </div>
  );
}

/** The way out of `payroll_amount_out_of_range` (C8): fix one item at a time,
 * then recalculate. Phrasing content only: it sits in the alert's line. */
export function OutOfRangeHelp({
  error,
  tutorId,
  can,
}: {
  error: unknown;
  tutorId: string | null;
  can: Pick<PeriodCapabilities, 'openSession' | 'openPayPages'>;
}) {
  const { t } = useTranslation();
  const sessionId = outOfRangeSessionId(error);
  return (
    <span className="flex flex-col items-start gap-1" data-out-of-range-help>
      <span>{t('payroll.period.outOfRange.help')}</span>
      {can.openPayPages ? (
        tutorId ? (
          <Link
            to={`/payroll/tutors/${encodeURIComponent(tutorId)}#${PAY_ITEMS_ANCHOR}`}
            className={LINK_CLASS}
          >
            {t('payroll.period.outOfRange.items')}
          </Link>
        ) : (
          <Link to="/payroll/tutors" className={LINK_CLASS}>
            {t('payroll.period.outOfRange.tutors')}
          </Link>
        )
      ) : null}
      {sessionId && can.openSession ? (
        <Link
          to={`/schedule/sessions/${encodeURIComponent(sessionId)}`}
          className={LINK_CLASS}
        >
          {t('payroll.period.outOfRange.session')}
        </Link>
      ) : null}
    </span>
  );
}

/** `payroll_unresolved_work`'s count and items, with the way to fix them
 * (D3, D13). No amount: the API sends none. */
function UnresolvedHelp({
  error,
  tutorId,
  can,
}: {
  error: unknown;
  tutorId: string | null;
  can: Pick<PeriodCapabilities, 'openSession' | 'openPayPages'>;
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const work = unresolvedWorkDetails(error);
  if (!work) return null;
  return (
    <span className="flex flex-col items-start gap-1" data-unresolved-help>
      <span>{t('payroll.period.unresolved', { count: work.count })}</span>
      {work.items.map((item) => {
        const known = UNRESOLVED_REASONS.find((r) => r === item.reason);
        const text = t('payroll.period.unresolvedItem', {
          date: formatLocalDate(item.service_date),
          reason: known
            ? t(`payroll.unresolved.reasons.${known}`)
            : item.reason,
        });
        return (
          <span
            key={`${item.class_session_id}:${item.reason}`}
            data-unresolved-item={item.class_session_id}
          >
            {can.openSession ? (
              <Link
                to={`/schedule/sessions/${encodeURIComponent(item.class_session_id)}`}
                className={LINK_CLASS}
              >
                {text}
              </Link>
            ) : (
              text
            )}
          </span>
        );
      })}
      {can.openPayPages && tutorId ? (
        <Link
          to={`/payroll/unresolved?tutor=${encodeURIComponent(tutorId)}`}
          className={LINK_CLASS}
        >
          {t('payroll.period.unresolvedLink')}
        </Link>
      ) : null}
    </span>
  );
}

/** The last action's refusal, in words, with its way out. */
export function PeriodRefusal({
  error,
  tutorId,
  can,
  onReturn,
  onCarry,
}: {
  error: unknown;
  /** Null when the period's tutor isn't known (it failed to load). */
  tutorId: string | null;
  can: PeriodCapabilities;
  /** Offered after `payroll_period_stale` (D12), when Return shows. */
  onReturn?: () => void;
  /** Offered after `payroll_negative_total` to payroll.prepare (M7-08b C3). */
  onCarry?: () => void;
}) {
  const { t } = useTranslation();
  if (isUnknownResult(error)) {
    return (
      <ErrorAlert error={error} message={t('payroll.period.unknownResult')} />
    );
  }
  if (isCode(error, 'payroll_review_outdated')) {
    return (
      <ErrorAlert
        error={error}
        message={t('payroll.period.refused.outdated')}
      />
    );
  }
  if (isCode(error, 'payroll_period_not_open', 'payroll_period_not_prepared')) {
    return (
      <ErrorAlert
        error={error}
        message={t('payroll.period.refused.statusChanged')}
      />
    );
  }
  if (isCode(error, 'payroll_period_stale')) {
    return (
      <ErrorAlert
        error={error}
        message={t('payroll.period.refused.stale')}
        detail={
          onReturn ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className={BUTTON_CLASS}
              onClick={onReturn}
            >
              {t('payroll.period.actions.return')}
            </Button>
          ) : undefined
        }
      />
    );
  }
  const self = selfFinalizeReason(error);
  if (self) {
    return (
      <ErrorAlert
        error={error}
        message={t(
          self === 'preparer'
            ? 'payroll.period.refused.selfPreparer'
            : 'payroll.period.refused.selfOwnPay',
        )}
      />
    );
  }
  const earliest = earliestPeriodId(error);
  if (isCode(error, 'payroll_period_out_of_order')) {
    return (
      <ErrorAlert
        error={error}
        detail={
          earliest ? (
            <Link to={periodPath(earliest)} className={LINK_CLASS}>
              {t('payroll.period.blocks.earlierLink')}
            </Link>
          ) : undefined
        }
      />
    );
  }
  if (isCode(error, 'payroll_unresolved_work')) {
    return (
      <ErrorAlert
        error={error}
        detail={<UnresolvedHelp error={error} tutorId={tutorId} can={can} />}
      />
    );
  }
  if (isCode(error, 'payroll_amount_out_of_range')) {
    return (
      <ErrorAlert
        error={error}
        detail={<OutOfRangeHelp error={error} tutorId={tutorId} can={can} />}
      />
    );
  }
  if (isCode(error, 'payroll_negative_total')) {
    return (
      <ErrorAlert
        error={error}
        detail={
          <span
            className="flex flex-col items-start gap-2"
            data-negative-refusal
          >
            <span>{t('payroll.period.carry.waiting')}</span>
            {can.prepare && onCarry ? (
              <>
                <span>{t('payroll.period.carry.help')}</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className={BUTTON_CLASS}
                  onClick={onCarry}
                >
                  {t('payroll.period.carry.action')}
                </Button>
              </>
            ) : null}
          </span>
        }
      />
    );
  }
  return <ErrorAlert error={error} />;
}

function BlockReason({ block }: { block: FinalizeBlock }) {
  const { t } = useTranslation();
  if (block.kind === 'earlier') {
    return (
      <span className="flex flex-col items-start gap-1">
        <span>{t('payroll.period.blocks.earlier')}</span>
        <Link to={periodPath(block.periodId)} className={LINK_CLASS}>
          {t('payroll.period.blocks.earlierLink')}
        </Link>
      </span>
    );
  }
  return <span>{t(`payroll.period.blocks.${block.kind}`)}</span>;
}

function Head({ review }: { review: PayrollPeriodReview }) {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate, formatInstant, formatMoney } = useFormatters();
  const { period } = review;
  const byAt = (
    who: { display_name: string } | null,
    when: string | null,
  ): string | null =>
    who && when
      ? t('payroll.period.head.byAt', {
          name: who.display_name,
          when: formatInstant(when, 'dateTime'),
        })
      : null;
  const H = 'payroll.period.head';
  const rows: [string, ReactNode][] = [
    [
      t(`${H}.tutor`),
      <span key="tutor" className="flex flex-wrap items-center gap-2">
        <span className="break-words">{period.tutor_display_name}</span>
        <StateBadge state={period.tutor_state} />
      </span>,
    ],
    [
      t(`${H}.dates`),
      t('payroll.periods.dates', {
        start: formatLocalDate(period.starts_on),
        end: formatLocalDate(period.ends_on),
      }),
    ],
    [t(`${H}.frequency`), labels.frequency(period.pay_frequency)],
    [t(`${H}.status`), <PeriodStatusBadge key="s" status={period.status} />],
  ];
  const prepared = byAt(period.prepared_by, period.prepared_at);
  if (prepared) rows.push([t(`${H}.preparedBy`), prepared]);
  const finalized = byAt(period.finalized_by, period.finalized_at);
  if (finalized) rows.push([t(`${H}.finalizedBy`), finalized]);
  if (period.status === 'open') {
    const returned = byAt(period.returned_by, period.returned_at);
    if (returned) rows.push([t(`${H}.returnedBy`), returned]);
    if (period.return_reason) {
      rows.push([t(`${H}.returnReason`), period.return_reason]);
    }
  }
  rows.push([
    t(`${H}.total`),
    <span
      key="total"
      className="text-base font-semibold"
      data-period-total={review.total}
    >
      {formatMoney(review.total)}
    </span>,
  ]);
  return (
    <dl
      className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
      data-period-head
    >
      {rows.map(([term, value]) => (
        <div key={term} className="min-w-0">
          <dt className="text-sm font-medium text-muted-foreground">{term}</dt>
          <dd className="break-words whitespace-pre-line">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PeriodReviewView({
  review,
  today,
  user,
  can,
  running,
  refreshError,
  onReload,
  announcement,
  failure,
  onRecalculate,
  onPrepare,
  onReturn,
  onFinalize,
  onCorrect,
  onCarry,
}: PeriodReviewViewProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const { period } = review;
  const actions = periodActions(period.status, can);
  const ended = hasEnded(period, today);
  const blocks = actions.finalize ? finalizeBlocks(period, user) : [];
  const negative = isNegativeAmount(review.total);
  const locked = running !== null || refreshError !== null;
  const carry =
    can.prepare &&
    onCarry !== undefined &&
    canCarryForward(period, review.total, today);
  const hasAdjustments = review.lines.some(
    (line) => line.kind === 'adjustment',
  );
  const correcting =
    period.status === 'finalized' && can.correct === true && onCorrect
      ? onCorrect
      : undefined;
  const ids = {
    lines: `${id}-lines`,
    notEnded: `${id}-not-ended`,
    preview: `${id}-preview`,
    blocks: `${id}-blocks`,
  };
  const spinning = (action: PeriodAction, label: string) =>
    running === action ? (
      <Spinner label={t('payroll.period.actions.working')} showLabel />
    ) : (
      label
    );
  const anyAction =
    actions.recalculate ||
    actions.prepare ||
    actions.return ||
    actions.finalize;

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <p
        role="status"
        className="text-sm font-medium"
        data-period-announcement={announcement}
      >
        {announcement}
      </p>
      {refreshError ? (
        <ErrorAlert
          error={refreshError}
          message={t('payroll.period.outdatedNotice')}
          onRetry={onReload}
          retryLabel={t('payroll.period.reload')}
          detail={
            isCode(refreshError, 'payroll_amount_out_of_range') ? (
              <OutOfRangeHelp
                error={refreshError}
                tutorId={period.tutor_user_id}
                can={can}
              />
            ) : undefined
          }
        />
      ) : null}
      <Head review={review} />
      {period.status === 'open' && period.returned_at ? (
        <Notice data-period-returned>
          <span>{t('payroll.period.returned')}</span>
        </Notice>
      ) : null}
      {period.status === 'finalized' ? (
        <Notice data-period-locked>
          <span>{t('payroll.period.locked')}</span>
        </Notice>
      ) : null}
      {period.status !== 'finalized' && period.pending_closeouts > 0 ? (
        <Notice data-pending-closeouts={String(period.pending_closeouts)}>
          <span>
            {t('payroll.period.pendingCloseouts', {
              count: period.pending_closeouts,
            })}
          </span>
        </Notice>
      ) : null}
      {carry ? (
        <Notice data-carry-banner>
          <span>{t('payroll.period.carry.banner')}</span>
          <span>{t('payroll.period.carry.waiting')}</span>
          <Button
            type="button"
            variant="outline"
            className={BUTTON_CLASS}
            disabled={locked}
            onClick={onCarry}
          >
            {spinning('carry', t('payroll.period.carry.action'))}
          </Button>
        </Notice>
      ) : period.status === 'open' && negative ? (
        <Notice data-negative-total>
          <span>{t('payroll.period.negativeTotal')}</span>
        </Notice>
      ) : null}
      {failure ? (
        <PeriodRefusal
          error={failure}
          tutorId={period.tutor_user_id}
          can={can}
          onReturn={actions.return && !locked ? onReturn : undefined}
          onCarry={carry && !locked ? onCarry : undefined}
        />
      ) : null}
      {anyAction ? (
        <section
          aria-label={t('payroll.period.actions.label')}
          className="flex min-w-0 flex-col gap-3"
          data-period-actions
        >
          <div className="flex flex-wrap gap-2">
            {actions.recalculate ? (
              <Button
                type="button"
                variant="outline"
                className={BUTTON_CLASS}
                disabled={locked}
                aria-describedby={ended ? undefined : ids.preview}
                onClick={onRecalculate}
              >
                {spinning(
                  'recalculate',
                  ended
                    ? t('payroll.period.actions.recalculate')
                    : t('payroll.period.actions.recalculatePreview'),
                )}
              </Button>
            ) : null}
            {actions.prepare ? (
              <Button
                type="button"
                className={BUTTON_CLASS}
                disabled={locked || !ended}
                aria-describedby={ended ? undefined : ids.notEnded}
                onClick={onPrepare}
              >
                {spinning('prepare', t('payroll.period.actions.prepare'))}
              </Button>
            ) : null}
            {actions.return ? (
              <Button
                type="button"
                variant="outline"
                className={BUTTON_CLASS}
                disabled={locked}
                onClick={onReturn}
              >
                {spinning('return', t('payroll.period.actions.return'))}
              </Button>
            ) : null}
            {actions.finalize ? (
              <Button
                type="button"
                className={BUTTON_CLASS}
                disabled={locked || blocks.length > 0}
                aria-describedby={blocks.length > 0 ? ids.blocks : undefined}
                onClick={onFinalize}
              >
                {spinning('finalize', t('payroll.period.actions.finalize'))}
              </Button>
            ) : null}
          </div>
          {actions.recalculate && !ended ? (
            <p id={ids.preview} className="text-sm text-muted-foreground">
              {t('payroll.period.actions.previewHelp')}
            </p>
          ) : null}
          {actions.prepare && !ended ? (
            <p
              id={ids.notEnded}
              className="text-sm text-muted-foreground"
              data-not-ended
            >
              {t('payroll.period.actions.notEnded', {
                date: formatLocalDate(period.ends_on),
              })}
            </p>
          ) : null}
          {blocks.length > 0 ? (
            <p
              id={ids.blocks}
              className="flex flex-col items-start gap-1 text-sm text-muted-foreground"
              data-finalize-blocks={blocks.map((b) => b.kind).join(' ')}
            >
              {blocks.map((block) => (
                <BlockReason key={block.kind} block={block} />
              ))}
            </p>
          ) : null}
        </section>
      ) : null}
      <section
        aria-labelledby={ids.lines}
        className="flex min-w-0 flex-col gap-4"
      >
        <h2 id={ids.lines} className="text-lg font-semibold">
          {t('payroll.period.lines.title')}
        </h2>
        {hasAdjustments ? (
          <p className="text-sm text-muted-foreground" data-adjustments-note>
            {t('payroll.period.adjustmentsNote')}
          </p>
        ) : null}
        <PeriodLines
          lines={review.lines}
          canOpenSession={can.openSession}
          periodId={period.id}
          tutorId={period.tutor_user_id}
          onCorrect={correcting}
          correctDisabled={locked}
        />
      </section>
    </div>
  );
}
