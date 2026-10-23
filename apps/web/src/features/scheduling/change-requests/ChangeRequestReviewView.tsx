import {
  useEffect,
  useId,
  useRef,
  type ReactNode,
  type RefObject,
} from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { SchedulingWarning } from '../create/api';
import { useWarningMessage } from '../create/warningMessages';
import type { ChangeRequestOutcome, ChangeRequestReview } from './api';
import { classificationKey } from './rules';

// "Review change request" (#62 G1), presentational. The summary is all
// text: the status, the kind, who asked (family or tutor, and the name),
// the student, the subject, the session's current local date and time with
// the zone named (and its tutor and room), the proposed time of a
// reschedule, the note, the policy outcome ("Late — no makeup", "On time —
// makeup until …"; never shown by colour alone) and the policy version's
// date. While pending, the page passes the decision panel. A decided
// request shows its outcome: approved (as an exception or not) or rejected,
// with the reason, and a link to the session. `notice` is a page-level
// message (a refused decision: the page reloads the request) that takes the
// focus; `approved` is the polite status after an approval, with the #50
// workload warnings of a move.

export type ChangeRequestReviewState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; review: ChangeRequestReview };

export interface ChangeRequestNotice {
  id: number;
  text: string;
}

export interface ChangeRequestReviewViewProps {
  state: ChangeRequestReviewState;
  timeZone: string;
  /** The decision panel (shown while the request is pending). */
  decisionPanel?: ReactNode;
  /** The link to the session page (a router Link in the app). */
  sessionLink: (sessionId: string) => ReactNode;
  notice?: ChangeRequestNotice | null;
  approved?: { warnings: readonly SchedulingWarning[] } | null;
  /** The outcome heading (the page focuses it after a decision). */
  outcomeRef?: RefObject<HTMLHeadingElement | null>;
}

export function ChangeRequestReviewView({
  state,
  timeZone,
  decisionPanel,
  sessionLink,
  notice,
  approved,
  outcomeRef,
}: ChangeRequestReviewViewProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const warningMessage = useWarningMessage();
  const id = useId();
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const noticeId = notice?.id;
  useEffect(() => {
    if (noticeId === undefined) {
      return undefined;
    }
    // After a closing dialog has returned the focus (its deferred step).
    const timer = window.setTimeout(() => noticeRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [noticeId]);
  const when = (instant: string, style: 'dateTime' | 'time' = 'dateTime') =>
    formatInstant(instant, { timeZone, language, style });
  const outcomeText = (outcome: ChangeRequestOutcome) =>
    t('scheduling.changeRequest.outcomeText', {
      classification: t(classificationKey(outcome)),
      makeup: outcome.makeup_until
        ? t('scheduling.changeRequest.makeupUntil', {
            date: when(outcome.makeup_until),
          })
        : t('scheduling.changeRequest.noMakeup'),
    });

  if (state.status === 'loading') {
    return <LoadingState />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { review } = state;
  const pending = review.status === 'pending';
  const zone = (
    <span className="text-sm text-muted-foreground">
      {t('scheduling.session.zone', { zone: timeZone })}
    </span>
  );

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      {notice ? (
        <p
          ref={noticeRef}
          role="alert"
          tabIndex={-1}
          className="text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {notice.text}
        </p>
      ) : null}
      {approved ? (
        <div
          role="status"
          className="flex flex-col gap-1 rounded-md border border-border p-3"
        >
          <p className="font-medium">
            {review.kind === 'reschedule'
              ? t('scheduling.changeRequest.approvedMoved')
              : t('scheduling.changeRequest.approvedCancelled')}
          </p>
          {approved.warnings.length > 0 ? (
            <>
              <p className="text-sm">{t('scheduling.warnings.heading')}</p>
              <ul className="list-disc pl-5 text-sm break-words">
                {approved.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>
                    {warningMessage(warning)}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
      <section
        aria-labelledby={`${id}-summary`}
        className="flex flex-col gap-3"
      >
        <h2 id={`${id}-summary`} className="text-lg font-semibold">
          {t('scheduling.changeRequest.summary')}
        </h2>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">
            {t('scheduling.changeRequest.status')}
          </dt>
          <dd>{t(`scheduling.changeRequest.statuses.${review.status}`)}</dd>
          <dt className="font-medium">{t('scheduling.changeRequest.kind')}</dt>
          <dd>{t(`scheduling.changeRequest.kinds.${review.kind}`)}</dd>
          <dt className="font-medium">
            {t('scheduling.changeRequest.requestedBy')}
          </dt>
          <dd className="break-words">
            {t('scheduling.changeRequest.requestedByText', {
              name: review.requested_by.display_name,
              role: t(
                `scheduling.changeRequest.initiators.${review.initiated_by}`,
              ),
              when: when(review.created_at),
            })}
          </dd>
          {review.student ? (
            <>
              <dt className="font-medium">{t('scheduling.fields.student')}</dt>
              <dd className="break-words">{review.student.display_name}</dd>
            </>
          ) : null}
          <dt className="font-medium">{t('scheduling.fields.subject')}</dt>
          <dd className="break-words">
            {t('scheduling.success.subject', {
              subject: review.subject.name,
              level: review.subject.education_level_name,
            })}
          </dd>
          <dt className="font-medium">
            {t('scheduling.changeRequest.session')}
          </dt>
          <dd>
            {t('scheduling.success.range', {
              start: when(review.session.starts_at),
              end: when(review.session.ends_at, 'time'),
            })}{' '}
            {zone}
            <span className="block text-sm break-words">
              {t('scheduling.changeRequest.sessionState', {
                state: t(`scheduling.states.${review.session.state}`),
              })}
            </span>
          </dd>
          <dt className="font-medium">{t('scheduling.fields.tutor')}</dt>
          <dd className="break-words">
            {review.session.tutor?.display_name ?? t('scheduling.noTutor')}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.classroom')}</dt>
          <dd className="break-words">
            {review.session.classroom?.name ?? t('scheduling.noClassroom')}
          </dd>
          {review.proposed_starts_at && review.proposed_ends_at ? (
            <>
              <dt className="font-medium">
                {t('scheduling.changeRequest.proposed')}
              </dt>
              <dd>
                {t('scheduling.success.range', {
                  start: when(review.proposed_starts_at),
                  end: when(review.proposed_ends_at, 'time'),
                })}{' '}
                {zone}
              </dd>
            </>
          ) : null}
          {review.note ? (
            <>
              <dt className="font-medium">
                {t('scheduling.changeRequest.note')}
              </dt>
              <dd className="break-words whitespace-pre-line">{review.note}</dd>
            </>
          ) : null}
          <dt className="font-medium">
            {t('scheduling.changeRequest.policyOutcome')}
          </dt>
          <dd className="break-words">
            {outcomeText(review.request_outcome)}
            <span className="block text-sm text-muted-foreground">
              {t('scheduling.changeRequest.policyVersion', {
                date: when(review.policy.effective_from),
              })}
            </span>
          </dd>
        </dl>
      </section>
      {pending ? null : (
        <section
          aria-labelledby={`${id}-outcome`}
          className="flex flex-col gap-2 rounded-md border border-border p-4"
        >
          <h2
            id={`${id}-outcome`}
            ref={outcomeRef}
            tabIndex={-1}
            className="text-lg font-semibold outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('scheduling.changeRequest.outcome')}
          </h2>
          {review.status === 'approved' ? (
            <>
              <p>
                {review.decision?.exception
                  ? t('scheduling.changeRequest.approvedExceptionText')
                  : t('scheduling.changeRequest.approvedText')}
              </p>
              {review.final_outcome ? (
                <p className="break-words">
                  {t('scheduling.changeRequest.finalOutcome', {
                    outcome: outcomeText(review.final_outcome),
                  })}
                </p>
              ) : null}
            </>
          ) : review.status === 'rejected' ? (
            <p>{t('scheduling.changeRequest.rejectedText')}</p>
          ) : (
            <p>{t('scheduling.changeRequest.withdrawnText')}</p>
          )}
          {review.decision?.reason ? (
            <p className="break-words whitespace-pre-line">
              {t('scheduling.changeRequest.reasonText', {
                reason: review.decision.reason,
              })}
            </p>
          ) : null}
          {review.decided_by ? (
            <p className="text-sm text-muted-foreground">
              {t('scheduling.changeRequest.decidedBy', {
                name: review.decided_by.display_name,
                when: review.resolved_at ? when(review.resolved_at) : '',
              })}
            </p>
          ) : null}
          <p>{sessionLink(review.session.id)}</p>
        </section>
      )}
      {pending ? decisionPanel : null}
    </div>
  );
}
