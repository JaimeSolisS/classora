import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { SchedulingWarning } from '../create/api';
import { useWarningMessage } from '../create/warningMessages';
import type { AssignmentOfferItem, ClassSessionDetail } from './api';

// The staff session page's content (#52 H2), presentational: the session
// summary and state, its pending offer, the offer history (status, tutor,
// dates, decline note) and, when the page passes it, the "Offer to a tutor"
// panel. A just-made offer is announced with its workload warnings (#50).

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
  } | null;
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
}: SessionDetailViewProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const warningMessage = useWarningMessage();
  const id = useId();
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

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      {offered ? (
        <div
          role="status"
          className="flex flex-col gap-1 rounded-md border border-border p-3"
        >
          <p className="font-medium">
            {t('scheduling.session.offeredTo', { tutor: offered.tutorName })}
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
                {t('scheduling.session.pendingOfferText', {
                  tutor: pending.tutor.display_name,
                  when: when(pending.respond_by),
                })}
              </dd>
            </>
          ) : null}
        </dl>
      </section>
      {offerPanel}
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
