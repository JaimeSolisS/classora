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
import { Button } from '@/components/ui/button';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { SchedulingWarning } from '../create/api';
import { useWarningMessage } from '../create/warningMessages';
import type { SessionRequestReview } from './api';

// "Review session request" (#60 H1), presentational: the request's summary
// (student, subject and level, local date and time with the zone named,
// length, requester, note and status, all as text), and while it can be
// decided the confirm panel (the page passes the #52 OfferPanel in its
// "confirm" variant) and "Reject request". A decided request shows its
// outcome: the link to the created session, or the rejection reason.
// `notice` is a page-level message (a refused decision, the request having
// changed: the page reloads it) that takes the focus; `confirmed` is the
// polite status after a confirmation, with the session link and the #50
// workload warnings.

export type RequestReviewState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; review: SessionRequestReview };

export interface RequestReviewNotice {
  id: number;
  text: string;
}

export interface RequestReviewViewProps {
  state: RequestReviewState;
  timeZone: string;
  /** The confirm panel (shown while the request can be decided). */
  confirmPanel?: ReactNode;
  /** Opens the reject dialog (shown while the request is pending). */
  onReject?: () => void;
  /** The link to a session page (a router Link in the app). */
  sessionLink: (sessionId: string) => ReactNode;
  notice?: RequestReviewNotice | null;
  confirmed?: {
    sessionId: string;
    tutorName: string;
    warnings: readonly SchedulingWarning[];
  } | null;
  /** The outcome heading (the page focuses it after a rejection). */
  outcomeRef?: RefObject<HTMLHeadingElement | null>;
}

export function RequestReviewView({
  state,
  timeZone,
  confirmPanel,
  onReject,
  sessionLink,
  notice,
  confirmed,
  outcomeRef,
}: RequestReviewViewProps) {
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

  if (state.status === 'loading') {
    return <LoadingState />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { review } = state;
  const pending = review.status === 'pending';

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
      {confirmed ? (
        <div
          role="status"
          className="flex flex-col gap-1 rounded-md border border-border p-3"
        >
          <p className="font-medium">
            {t('scheduling.requestReview.confirmedTo', {
              tutor: confirmed.tutorName,
            })}
          </p>
          <p>{sessionLink(confirmed.sessionId)}</p>
          {confirmed.warnings.length > 0 ? (
            <>
              <p className="text-sm">{t('scheduling.warnings.heading')}</p>
              <ul className="list-disc pl-5 text-sm break-words">
                {confirmed.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>
                    {warningMessage(warning)}
                  </li>
                ))}
              </ul>
              <p className="text-sm text-muted-foreground">
                {t('scheduling.warnings.intro')}
              </p>
            </>
          ) : null}
        </div>
      ) : null}
      <section
        aria-labelledby={`${id}-summary`}
        className="flex flex-col gap-3"
      >
        <h2 id={`${id}-summary`} className="text-lg font-semibold">
          {t('scheduling.requestReview.summary')}
        </h2>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">
            {t('scheduling.requestReview.status')}
          </dt>
          <dd>{t(`sessionRequests.status.${review.status}`)}</dd>
          <dt className="font-medium">{t('scheduling.fields.student')}</dt>
          <dd className="break-words">
            {review.student.is_active
              ? review.student.display_name
              : t('scheduling.requestReview.inactive', {
                  name: review.student.display_name,
                })}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.subject')}</dt>
          <dd className="break-words">
            {t('scheduling.success.subject', {
              subject: review.subject.name,
              level: review.subject.education_level_name,
            })}
            {review.subject.is_active
              ? null
              : ` ${t('scheduling.requestReview.subjectInactive')}`}
          </dd>
          <dt className="font-medium">{t('scheduling.success.when')}</dt>
          <dd>
            {t('scheduling.success.range', {
              start: when(review.starts_at),
              end: when(review.ends_at, 'time'),
            })}{' '}
            <span className="text-sm text-muted-foreground">
              {t('scheduling.session.zone', { zone: timeZone })}
            </span>
          </dd>
          <dt className="font-medium">
            {t('scheduling.requestReview.length')}
          </dt>
          <dd>
            {t('sessionRequests.minutes', {
              minutes: review.duration_minutes,
            })}
          </dd>
          <dt className="font-medium">
            {t('scheduling.requestReview.requestedBy')}
          </dt>
          <dd className="break-words">
            {t('scheduling.requestReview.requestedOn', {
              name: review.requested_by.display_name,
              when: when(review.created_at),
            })}
          </dd>
          {review.note ? (
            <>
              <dt className="font-medium">
                {t('scheduling.requestReview.note')}
              </dt>
              <dd className="break-words whitespace-pre-line">{review.note}</dd>
            </>
          ) : null}
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
            {t('scheduling.requestReview.outcome')}
          </h2>
          {review.status === 'confirmed' ? (
            <>
              <p>{t('scheduling.requestReview.confirmedText')}</p>
              {review.class_session_id &&
              review.class_session_id !== confirmed?.sessionId ? (
                <p>{sessionLink(review.class_session_id)}</p>
              ) : null}
            </>
          ) : review.status === 'rejected' ? (
            <>
              <p>{t('scheduling.requestReview.rejectedText')}</p>
              <p className="break-words whitespace-pre-line">
                {t('scheduling.requestReview.reasonText', {
                  reason: review.rejection_reason ?? '',
                })}
              </p>
            </>
          ) : (
            <p>{t('scheduling.requestReview.withdrawnText')}</p>
          )}
        </section>
      )}
      {pending && review.can_decide ? confirmPanel : null}
      {pending && !review.can_decide ? (
        <p className="text-sm">{t('scheduling.requestReview.startedHelp')}</p>
      ) : null}
      {pending && onReject ? (
        <section
          aria-labelledby={`${id}-reject`}
          className="flex w-full max-w-2xl min-w-0 flex-col gap-3 rounded-md border border-border p-4"
        >
          <h2 id={`${id}-reject`} className="text-lg font-semibold">
            {t('scheduling.requestReview.reject.heading')}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t('scheduling.requestReview.reject.intro')}
          </p>
          <div>
            <Button type="button" variant="outline" onClick={onReject}>
              {t('scheduling.requestReview.reject.open')}
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
