import { useEffect, useId, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { toLanguage } from '@/i18n/language';
import { formatInstant, formatLocalDate } from '@/lib/time';
import type { SchedulingWarning } from '../create/api';
import { useWarningMessage } from '../create/warningMessages';
import { REQUEST_CANCELLATION_REASONS } from '../change-requests/rules';
import type {
  AssignmentOfferItem,
  ClassSeriesDetail,
  ClassSessionDetail,
} from './api';

// The staff session page's content (#52 H2), presentational: the session
// summary and state, its pending offer, the offer history (status, tutor,
// dates, decline note) and, when the page passes it, the "Offer to a tutor"
// panel. A just-made offer is announced with its workload warnings (#50).
// An occurrence of a series (#233 G2) says so and shows the series'
// assignment: none, pending (the tutor and the deadline) or accepted (the
// tutor and the date it covers from).
//
// #55 H1: a cancelled session shows a "Cancelled" banner (when and why) and
// no actions; a pending substitution shows in the summary; the page passes
// the "Cancel session" action, the substitute panel (in `offerPanel`), the
// substitutions section and a notice for a refused write.
// #62 G2: a pending change request shows a notice with the review link; a
// session cancelled at a family's or tutor's request names that, not the
// system code.
// #63 H3: an open reassignment flag shows "Needs reassignment: the tutor's
// availability changed" with whose and when.

export interface SessionNotice {
  id: number;
  text: string;
  tone: 'alert' | 'status';
}

export type SessionState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      session: ClassSessionDetail;
      offers: readonly AssignmentOfferItem[];
    };

export interface SessionDetailViewProps {
  state: SessionState;
  timeZone: string;
  /** The offer panel (unassigned + schedule.assign), or nothing. */
  offerPanel?: ReactNode;
  /** The offer just made, for the success message. */
  offered?: {
    tutorName: string;
    warnings: readonly SchedulingWarning[];
    series?: boolean;
    substitution?: boolean;
  } | null;
  /** "Cancel session" (#55), or nothing. */
  actions?: ReactNode;
  /** The substitutions section (#55), or nothing. */
  substitutions?: ReactNode;
  /**
   * A page-level notice that survives the panels' remount (#55 H1 / QA D1):
   * a refused write's message (`alert`; the page reloads the session) or a
   * done withdraw (`status`). It takes focus whenever `id` changes, so focus
   * never falls to the body when the panel that had it disappears.
   */
  notice?: SessionNotice | null;
  /** The session's series, once read (#233 G2). */
  series?: ClassSeriesDetail | null;
  /**
   * The link to the review page of the session's pending change request
   * (#62 G2: a holder of schedule.manage), or nothing.
   */
  changeRequestLink?: (changeRequestId: string) => ReactNode;
}

const OFFER_STATUS_KEYS = {
  pending: 'offers.status.pending',
  accepted: 'offers.status.accepted',
  declined: 'offers.status.declined',
  expired: 'offers.status.expired',
  withdrawn: 'offers.status.withdrawn',
} as const;

export function SessionDetailView({
  state,
  timeZone,
  offerPanel,
  offered,
  series,
  actions,
  substitutions,
  notice,
  changeRequestLink,
}: SessionDetailViewProps) {
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
    // After a closing dialog has returned focus (its own deferred step).
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
  const { session, offers } = state;
  const pending = session.pending_offer;
  const pendingSubstitution = session.pending_substitution;
  const cancellation = session.cancellation;

  function seriesAssignmentText(detail: ClassSeriesDetail): string {
    const assignment = detail.assignment;
    if (!assignment) {
      return t('scheduling.session.seriesNoTutor');
    }
    if (assignment.status === 'pending') {
      return assignment.respond_by
        ? t('scheduling.session.seriesPending', {
            tutor: assignment.tutor.display_name,
            when: when(assignment.respond_by),
          })
        : t('scheduling.session.seriesPendingNoDeadline', {
            tutor: assignment.tutor.display_name,
          });
    }
    return t('scheduling.session.seriesAccepted', {
      tutor: assignment.tutor.display_name,
      date: formatLocalDate(assignment.from_date, { language }),
    });
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      {cancellation ? (
        <section
          aria-labelledby={`${id}-cancelled`}
          className="flex flex-col gap-1 rounded-md border-2 border-foreground p-3"
        >
          <h2 id={`${id}-cancelled`} className="font-semibold">
            {t('scheduling.cancel.banner')}
          </h2>
          <p className="text-sm">
            {t('scheduling.cancel.bannerWhen', {
              when: when(cancellation.cancelled_at),
            })}
          </p>
          <p className="text-sm break-words">
            {REQUEST_CANCELLATION_REASONS.has(cancellation.reason)
              ? t(
                  `scheduling.changeRequest.cancellationReasons.${cancellation.reason as 'family_request' | 'tutor_request'}`,
                )
              : t('scheduling.substitution.reasonText', {
                  reason: cancellation.reason,
                })}
          </p>
        </section>
      ) : null}
      {session.reassignment ? (
        <section
          aria-labelledby={`${id}-reassignment`}
          className="flex flex-col gap-1 rounded-md border-2 border-foreground p-3"
        >
          <h2 id={`${id}-reassignment`} className="font-semibold">
            {t('scheduling.session.reassignment.title')}
          </h2>
          <p className="text-sm break-words">
            {t('scheduling.session.reassignment.text', {
              tutor: session.reassignment.tutor.display_name,
              when: when(session.reassignment.flagged_at),
            })}
          </p>
        </section>
      ) : null}
      {session.pending_change_request_id && changeRequestLink ? (
        <section
          aria-labelledby={`${id}-change-request`}
          className="flex flex-col gap-1 rounded-md border border-border p-3"
        >
          <h2 id={`${id}-change-request`} className="font-semibold">
            {t('scheduling.changeRequest.pendingNotice')}
          </h2>
          <p className="text-sm">
            {changeRequestLink(session.pending_change_request_id)}
          </p>
        </section>
      ) : null}
      {notice ? (
        <p
          ref={noticeRef}
          role={notice.tone}
          tabIndex={-1}
          className="text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {notice.text}
        </p>
      ) : null}
      {offered ? (
        <div
          role="status"
          className="flex flex-col gap-1 rounded-md border border-border p-3"
        >
          <p className="font-medium">
            {offered.substitution
              ? t('scheduling.substitution.offeredTo', {
                  tutor: offered.tutorName,
                })
              : offered.series
                ? t('scheduling.session.seriesOfferedTo', {
                    tutor: offered.tutorName,
                  })
                : t('scheduling.session.offeredTo', {
                    tutor: offered.tutorName,
                  })}
          </p>
          {offered.warnings.length > 0 ? (
            <>
              <p className="text-sm">{t('scheduling.warnings.heading')}</p>
              <ul className="list-disc pl-5 text-sm break-words">
                {offered.warnings.map((warning, index) => (
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
          {t('scheduling.session.summary')}
        </h2>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">{t('scheduling.session.state')}</dt>
          <dd>{t(`scheduling.states.${session.state}`)}</dd>
          <dt className="font-medium">{t('scheduling.fields.subject')}</dt>
          <dd className="break-words">
            {t('scheduling.success.subject', {
              subject: session.subject.name,
              level: session.subject.education_level_name,
            })}
          </dd>
          <dt className="font-medium">{t('scheduling.success.when')}</dt>
          <dd>
            {t('scheduling.success.range', {
              start: when(session.starts_at),
              end: when(session.ends_at, 'time'),
            })}{' '}
            <span className="text-sm text-muted-foreground">
              {t('scheduling.session.zone', { zone: timeZone })}
            </span>
          </dd>
          <dt className="font-medium">{t('scheduling.fields.student')}</dt>
          <dd className="break-words">
            {session.participants.map((p) => p.display_name).join(', ')}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.tutor')}</dt>
          <dd>
            {session.tutor?.display_name ?? t('scheduling.session.noTutor')}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.classroom')}</dt>
          <dd>{session.classroom?.name ?? t('scheduling.noClassroom')}</dd>
          {pending ? (
            <>
              <dt className="font-medium">
                {t('scheduling.session.pendingOffer')}
              </dt>
              <dd>
                {t(
                  pending.scope === 'series'
                    ? 'scheduling.session.pendingSeriesOfferText'
                    : 'scheduling.session.pendingOfferText',
                  {
                    tutor: pending.tutor.display_name,
                    when: when(pending.respond_by),
                  },
                )}
              </dd>
            </>
          ) : null}
          {pendingSubstitution ? (
            <>
              <dt className="font-medium">
                {t('scheduling.substitution.pendingLabel')}
              </dt>
              <dd className="break-words">
                {t('scheduling.session.pendingOfferText', {
                  tutor: pendingSubstitution.substitute.display_name,
                  when: when(pendingSubstitution.respond_by),
                })}
              </dd>
            </>
          ) : null}
          {session.series_id ? (
            <>
              <dt className="font-medium">{t('scheduling.session.series')}</dt>
              <dd className="break-words">
                {t('scheduling.session.partOfSeries')}
                {series ? (
                  <>
                    {' · '}
                    {seriesAssignmentText(series)}
                  </>
                ) : null}
              </dd>
            </>
          ) : null}
        </dl>
      </section>
      {cancellation ? null : actions}
      {cancellation ? null : offerPanel}
      {substitutions}
      <section
        aria-labelledby={`${id}-history`}
        className="flex flex-col gap-3"
      >
        <h2 id={`${id}-history`} className="text-lg font-semibold">
          {t('scheduling.session.history')}
        </h2>
        {offers.length === 0 ? (
          <p className="text-sm">{t('scheduling.session.noOffers')}</p>
        ) : (
          <ol className="flex flex-col gap-3">
            {offers.map((offer) => (
              <li
                key={offer.id}
                className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-sm"
              >
                <p className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-xs font-medium">
                    {t(OFFER_STATUS_KEYS[offer.status])}
                  </span>
                  <span className="font-medium break-words">
                    {offer.tutor.display_name}
                  </span>
                </p>
                <p className="break-words">
                  {t('scheduling.session.offeredBy', {
                    name: offer.offered_by.display_name,
                    when: when(offer.offered_at),
                  })}
                </p>
                <p>
                  {offer.responded_at
                    ? t('scheduling.session.respondedAt', {
                        when: when(offer.responded_at),
                      })
                    : t('scheduling.session.respondBy', {
                        when: when(offer.respond_by),
                      })}
                </p>
                {offer.decline_note ? (
                  <p className="break-words text-muted-foreground">
                    {t('offers.card.note', { note: offer.decline_note })}
                  </p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
