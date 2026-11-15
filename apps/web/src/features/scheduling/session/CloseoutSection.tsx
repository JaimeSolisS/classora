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
import {
  CloseoutOnBehalfDialog,
  type OnBehalfValues,
} from './CloseoutOnBehalfDialog';
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
//
// #301b: the waiting state also says how many students are marked, and a
// reviewer (`can_submit_on_behalf`) gets "Close out on behalf of {tutor}";
// a closeout sent back to its tutor offers "Take over and resubmit"
// (`can_take_over`). An on-behalf submission reads "Submitted on behalf of
// {tutor} by {name}, {when}" with the reason ("Reason visible to academic
// reviewers" when it is hidden), and Request changes follows its own flag
// (`can_request_changes`: never on an on-behalf submission).

export type CloseoutSectionState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; closeout: SessionCloseoutView };

/** The decisions; each resolves once done, or rejects with the error. */
export interface CloseoutReviewActions {
  onApprove: (version: number) => Promise<void>;
  onRequestChanges: (version: number, reason: string) => Promise<void>;
  onCorrect: (body: CloseoutCorrectionBody) => Promise<void>;
  /** #301b C3: the session's version. */
  onSubmitOnBehalf: (version: number, values: OnBehalfValues) => Promise<void>;
  /** #301b D3: the closeout's version. */
  onTakeOver: (version: number, values: OnBehalfValues) => Promise<void>;
  onReload: () => void;
}

/** A row of the page's attendance (the unmarked students' names). */
export interface CloseoutStudent {
  student: { id: string; display_name: string };
  status: string | null;
}

export interface CloseoutSectionProps {
  state: CloseoutSectionState;
  timeZone: string;
  review?: CloseoutReviewActions;
  /** The page's attendance rows (#301b C2). */
  students?: readonly CloseoutStudent[];
}

type Dialog =
  | 'approve'
  | 'changes'
  | 'correct'
  | 'reapprove'
  | 'onBehalf'
  | 'takeover'
  | null;

export function CloseoutSection({
  state,
  timeZone,
  review,
  students = [],
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
      const tutorName = closeout.tutor?.display_name ?? '';
      body = (
        <div className="flex min-w-0 flex-col gap-3">
          <p className="text-sm">{t('closeout.section.waiting')}</p>
          <p className="text-sm">
            {t('closeout.onBehalf.attendance', {
              marked: closeout.attendance.marked,
              total: closeout.attendance.total,
            })}
          </p>
          {review && closeout.can_submit_on_behalf ? (
            <>
              <div>
                <Button
                  type="button"
                  className="h-auto min-h-9 whitespace-normal"
                  onClick={(event) => show('onBehalf', event.currentTarget)}
                >
                  {t('closeout.onBehalf.action', { tutor: tutorName })}
                </Button>
              </div>
              <CloseoutOnBehalfDialog
                open={dialog === 'onBehalf'}
                mode="submit"
                tutorName={tutorName}
                scheduledMinutes={closeout.scheduled_minutes}
                allAbsent={closeout.attendance.all_absent}
                attendance={closeout.attendance}
                students={students}
                onConfirm={async (values) => {
                  await review.onSubmitOnBehalf(closeout.version, values);
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
            {record.on_behalf ? (
              <>
                <p className="text-sm break-words">
                  {t('closeout.onBehalf.marker', {
                    tutor: record.submitted_by.display_name,
                    name: record.on_behalf.by.display_name,
                    when: when(record.on_behalf.submitted_at),
                  })}
                </p>
                <p className="text-sm break-words whitespace-pre-line">
                  {record.on_behalf.reason === null
                    ? t('closeout.onBehalf.reasonHidden')
                    : t('closeout.onBehalf.reasonLine', {
                        reason: record.on_behalf.reason,
                      })}
                </p>
              </>
            ) : null}
            {review &&
            record.status === 'changes_requested' &&
            closeout.can_take_over ? (
              <div>
                <Button
                  type="button"
                  variant="outline"
                  onClick={(event) => show('takeover', event.currentTarget)}
                >
                  {t('closeout.onBehalf.takeOver')}
                </Button>
              </div>
            ) : null}
            {/* #312: only with the submission under review; a decision
                hides it (the row and the audit event keep it). */}
            {record.review.status === 'submitted' &&
            record.review.tutor_note ? (
              <p className="text-sm break-words whitespace-pre-line">
                <span className="font-medium">
                  {t('closeout.review.tutorNote', {
                    when: when(record.review.last_submitted_at),
                  })}
                  :
                </span>{' '}
                {record.review.tutor_note}
              </p>
            ) : null}
          </div>
          {review &&
          (closeout.can_review ||
            closeout.can_request_changes ||
            closeout.can_correct) ? (
            <div className="flex flex-wrap gap-2">
              {closeout.can_review ? (
                <Button
                  type="button"
                  aria-describedby={`${id}-family-visibility`}
                  onClick={(event) => show('approve', event.currentTarget)}
                >
                  {t('closeout.review.approve')}
                </Button>
              ) : null}
              {closeout.can_request_changes ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={(event) => show('changes', event.currentTarget)}
                >
                  {t('closeout.review.requestChanges')}
                </Button>
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
              {/* #322 E1 (#84 E3): approving shows the summary to the
                  class's students and families. */}
              {closeout.can_review ? (
                <p
                  id={`${id}-family-visibility`}
                  className="basis-full text-sm text-muted-foreground"
                >
                  {t('closeout.familyVisibility')}
                </p>
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
              {closeout.can_take_over ? (
                <CloseoutOnBehalfDialog
                  open={dialog === 'takeover'}
                  mode="takeover"
                  tutorName={record.submitted_by.display_name}
                  scheduledMinutes={closeout.scheduled_minutes}
                  allAbsent={closeout.attendance.all_absent}
                  attendance={closeout.attendance}
                  students={students}
                  record={record}
                  onConfirm={async (values) => {
                    await review.onTakeOver(record.version, values);
                    close();
                  }}
                  onCancel={close}
                  onReload={() => {
                    close();
                    review.onReload();
                  }}
                />
              ) : null}
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
