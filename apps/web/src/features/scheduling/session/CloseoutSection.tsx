import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type {
  CloseoutCorrectionBody,
  SessionCloseoutView,
} from '@/features/tutor-portal/closeout/api';
import { SummaryList } from '@/features/tutor-portal/closeout/CloseoutFlow';
import { ApproveCloseoutDialog } from './ApproveCloseoutDialog';
import { CloseoutHistory } from './CloseoutHistory';
import { closeoutBadge } from './closeoutReview';
import { CorrectCloseoutDialog } from './CorrectCloseoutDialog';
import { RequestChangesDialog } from './RequestChangesDialog';

// The session's closeout on the staff session page (#77 G3, #78 G1): for a
// completed session the status badge ("Submitted for review", "Correction
// submitted", "Changes requested" with the reason, or "Approved · n min by
// name, date"), the tutor's reason of a resubmission or correction request
// (AM3), the actual vs scheduled minutes with the difference as text, who
// submitted it and when, the summary (or "Summary visible to academic
// reviewers" when it is hidden, E5), "Attendance changed since approval"
// as text and the history. Reviewers (the API's can_review / can_correct
// flags) get Approve, Request changes and Correct (and Re-approve next to
// the attendance marker). "No closeout recorded" for a completed session
// without one; a started confirmed session reads "Waiting for the tutor's
// closeout". The page renders it for completed and started confirmed
// sessions only; `#closeout` (the inbox's Review link) scrolls to it.

export type CloseoutSectionState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; closeout: SessionCloseoutView };

/** The decisions; each resolves once done, or rejects with the error. */
export interface CloseoutReviewActions {
  onApprove: (version: number) => Promise<void>;
  onRequestChanges: (version: number, reason: string) => Promise<void>;
  onCorrect: (body: CloseoutCorrectionBody) => Promise<void>;
  onReload: () => void;
}

export interface CloseoutSectionProps {
  state: CloseoutSectionState;
  timeZone: string;
  review?: CloseoutReviewActions;
}

type Dialog = 'approve' | 'changes' | 'correct' | 'reapprove' | null;

export function CloseoutSection({
  state,
  timeZone,
  review,
}: CloseoutSectionProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const section = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);

  /** Opens a dialog, remembering its trigger for the focus return. */
  function show(kind: Dialog, from: HTMLElement) {
    trigger.current = from;
    setDialog(kind);
  }

  /** Closes the dialog; focus goes back to the trigger (or the heading
   * when a decision removed it). */
  function close() {
    setDialog(null);
    window.setTimeout(() => {
      const target = trigger.current;
      if (target?.isConnected) {
        target.focus();
      } else {
        heading.current?.focus();
      }
    }, 0);
  }
  const ready = state.status === 'ready';

  useEffect(() => {
    if (ready && window.location.hash === '#closeout') {
      section.current?.scrollIntoView?.({ block: 'start' });
    }
  }, [ready]);

  const when = (instant: string) =>
    formatInstant(instant, { timeZone, language, style: 'dateTime' });

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={2} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { closeout } = state;
    const record = closeout.closeout;
    if (closeout.state === 'confirmed') {
      body = <p className="text-sm">{t('closeout.section.waiting')}</p>;
    } else if (!record) {
      body = <p className="text-sm">{t('closeout.noCloseout')}</p>;
    } else {
      const actual = record.actual_minutes ?? 0;
      const difference = actual - closeout.scheduled_minutes;
      const badge = closeoutBadge(record);
      body = (
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <p>
              <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-sm font-medium break-words">
                {badge.kind === 'approved'
                  ? t('closeout.review.approvedBadge', {
                      minutes: badge.minutes,
                      name: badge.name,
                      when: when(badge.approvedAt),
                    })
                  : badge.kind === 'changesRequested'
                    ? t('closeout.review.changesRequested')
                    : badge.kind === 'correctionSubmitted'
                      ? t('closeout.review.correctionSubmitted')
                      : t('closeout.submitted')}
              </span>
            </p>
            {badge.kind === 'changesRequested' && badge.reason ? (
              <p className="text-sm break-words whitespace-pre-line">
                <span className="font-medium">
                  {t('closeout.review.reviewReason')}:
                </span>{' '}
                {badge.reason}
              </p>
            ) : null}
            {record.review.tutor_note ? (
              <p className="text-sm break-words whitespace-pre-line">
                <span className="font-medium">
                  {t('closeout.review.tutorNote')}:
                </span>{' '}
                {record.review.tutor_note}
              </p>
            ) : null}
          </div>
          {review && (closeout.can_review || closeout.can_correct) ? (
            <div className="flex flex-wrap gap-2">
              {closeout.can_review ? (
                <>
                  <Button
                    type="button"
                    onClick={(event) => show('approve', event.currentTarget)}
                  >
                    {t('closeout.review.approve')}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={(event) => show('changes', event.currentTarget)}
                  >
                    {t('closeout.review.requestChanges')}
                  </Button>
                </>
              ) : null}
              {closeout.can_correct ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={(event) => show('correct', event.currentTarget)}
                >
                  {t('closeout.review.correct')}
                </Button>
              ) : null}
            </div>
          ) : null}
          {record.attendance_changed_since_approval ? (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2 text-sm">
              <span className="font-medium">
                {t('closeout.review.attendanceChanged')}
              </span>
              {review && closeout.can_correct ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={(event) => show('reapprove', event.currentTarget)}
                >
                  {t('closeout.review.reapprove')}
                </Button>
              ) : null}
            </div>
          ) : null}
          <dl className="grid gap-2 text-sm sm:grid-cols-[12rem_1fr]">
            <dt className="font-medium">{t('closeout.actualMinutes')}</dt>
            <dd>
              {t('closeout.minutesOfScheduled', {
                actual,
                scheduled: closeout.scheduled_minutes,
              })}{' '}
              <span className="text-muted-foreground">
                (
                {difference === 0
                  ? t('closeout.section.asScheduled')
                  : difference > 0
                    ? t('closeout.section.more', { count: difference })
                    : t('closeout.section.less', { count: -difference })}
                )
              </span>
            </dd>
            <dt className="font-medium">{t('closeout.section.submittedBy')}</dt>
            <dd>
              {t('closeout.section.byWhen', {
                name: record.submitted_by.display_name,
                when: when(record.submitted_at),
              })}
            </dd>
          </dl>
          {record.summary_hidden || !record.summary ? (
            <p className="text-sm">{t('closeout.summaryHidden')}</p>
          ) : (
            <SummaryList summary={record.summary} />
          )}
          <CloseoutHistory record={record} timeZone={timeZone} />
          {review ? (
            <>
              <ApproveCloseoutDialog
                open={dialog === 'approve'}
                actualMinutes={actual}
                scheduledMinutes={closeout.scheduled_minutes}
                onConfirm={async () => {
                  await review.onApprove(record.version);
                  close();
                }}
                onCancel={close}
                onReload={() => {
                  close();
                  review.onReload();
                }}
              />
              <RequestChangesDialog
                open={dialog === 'changes'}
                onConfirm={async (reason) => {
                  await review.onRequestChanges(record.version, reason);
                  close();
                }}
                onCancel={close}
                onReload={() => {
                  close();
                  review.onReload();
                }}
              />
              <CorrectCloseoutDialog
                key={dialog === 'reapprove' ? 'reapprove' : 'correct'}
                open={dialog === 'correct' || dialog === 'reapprove'}
                reapprove={dialog === 'reapprove'}
                record={record}
                scheduledMinutes={closeout.scheduled_minutes}
                allAbsent={closeout.attendance.all_absent}
                onConfirm={async (correction) => {
                  await review.onCorrect(correction);
                  close();
                }}
                onCancel={close}
                onReload={() => {
                  close();
                  review.onReload();
                }}
              />
            </>
          ) : null}
        </div>
      );
    }
  }

  return (
    <section
      id="closeout"
      ref={section}
      aria-labelledby={`${id}-closeout`}
      className="flex min-w-0 scroll-mt-4 flex-col gap-3"
    >
      <h2
        id={`${id}-closeout`}
        ref={heading}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        {t('closeout.section.heading')}
      </h2>
      {body}
    </section>
  );
}
