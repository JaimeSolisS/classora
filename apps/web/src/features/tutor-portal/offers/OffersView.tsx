import { useId, useRef, useState, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { ErrorMessageKey } from '@/app/errors';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import type { WindowWeekday } from '@/components/weekly-windows/windows';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { toLanguage } from '@/i18n/language';
import { cn } from '@/lib/utils';
import type { AssignmentOfferItem, OfferTab } from './api';
import { DeclineDialog } from './DeclineDialog';
import {
  addMinutesToTime,
  deadlineText,
  isDueSoon,
  localTimeText,
  sessionTimes,
  staleAnswer,
  weekdayList,
} from './rules';
import { formatLocalDate } from '@/lib/time';

// "My offers" (#52 H1), presentational: the page passes the tab's list
// state and the answers. Two tabs (Pending, Answered; ARIA tabs with the
// arrow keys). A pending card shows the subject and level, the date and
// times in the center zone (the zone is shown), the classroom, the
// students, the deadline ("Answer by …", "Due soon" under 24 hours), and
// Accept (asks for confirmation) / Decline (a dialog with an optional
// note). A `409 offer_not_pending` / `offer_expired` shows its message on
// the card (or above the list once the refreshed list no longer has it);
// the page refreshes the list. A success is announced politely and the card
// moves to Answered (the page refreshes both tabs). Cards stack on phones
// (320 px).
//
// A series offer (#233 G1) has its own card: "Weekly series" with the
// weekdays and times in the center zone, the classroom and students, the
// from-date and the end (until, count or none), how many sessions are
// scheduled so far, the first sessions ("and n more" when truncated) and
// the deadline. Accepting it confirms every current and future session.

export type OffersState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly AssignmentOfferItem[] };

export interface OffersViewProps {
  tab: OfferTab;
  onTab: (tab: OfferTab) => void;
  state: OffersState;
  timeZone: string;
  /** "Now" for the due-soon badge (stories and tests pin it). */
  now?: Date;
  /** Accepts; rejects with the error. */
  onAccept: (offer: AssignmentOfferItem) => Promise<void>;
  /** Declines with the trimmed note; rejects with the error. */
  onDecline: (offer: AssignmentOfferItem, note: string) => Promise<void>;
  /** Refreshes the list after a stale answer. */
  onStale: () => void;
  /** The decline dialog to start with open (stories). */
  initialDecline?: { offerId: string; error?: unknown };
}

const TABS: readonly OfferTab[] = ['pending', 'answered'];

interface Notice {
  offerId: string;
  key: ErrorMessageKey;
}

export function OffersView({
  tab,
  onTab,
  state,
  timeZone,
  now = new Date(),
  onAccept,
  onDecline,
  onStale,
  initialDecline,
}: OffersViewProps) {
  const { t, i18n } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const tabRefs = useRef<Record<OfferTab, HTMLButtonElement | null>>({
    pending: null,
    answered: null,
  });
  const [accepting, setAccepting] = useState<AssignmentOfferItem | null>(null);
  const [declining, setDeclining] = useState<string | null>(
    initialDecline?.offerId ?? null,
  );
  const [notice, setNotice] = useState<Notice | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [failure, setFailure] = useState<{
    offerId: string;
    error: unknown;
  } | null>(null);

  const items = state.status === 'ready' ? state.items : [];
  const declineOffer = items.find((offer) => offer.id === declining) ?? null;

  function label(offer: AssignmentOfferItem): string {
    if (offer.series) {
      return t('offers.series.label', {
        subject: offer.series.subject.name,
        date: formatLocalDate(offer.series.from_date, { language }),
      });
    }
    const first = offer.sessions[0];
    if (!first) {
      return '';
    }
    const times = sessionTimes(first, timeZone, language);
    return t('offers.card.label', {
      subject: first.subject.name,
      date: times.date,
      start: times.start,
    });
  }

  function handleError(offer: AssignmentOfferItem, caught: unknown): boolean {
    const key = staleAnswer(caught);
    if (!key) {
      return false;
    }
    setNotice({ offerId: offer.id, key });
    onStale();
    return true;
  }

  async function confirmAccept() {
    const offer = accepting;
    if (!offer) {
      return;
    }
    try {
      await onAccept(offer);
      setNotice(null);
      setAnnouncement(t('offers.accepted', { offer: label(offer) }));
    } catch (caught) {
      if (!handleError(offer, caught)) {
        setNotice(null);
        setFailure({ offerId: offer.id, error: caught });
      }
    } finally {
      setAccepting(null);
    }
  }

  async function confirmDecline(note: string) {
    if (!declineOffer) {
      return;
    }
    const offer = declineOffer;
    try {
      await onDecline(offer, note);
      setDeclining(null);
      setNotice(null);
      setAnnouncement(t('offers.declined', { offer: label(offer) }));
    } catch (caught) {
      if (handleError(offer, caught)) {
        setDeclining(null);
        return;
      }
      throw caught;
    }
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    const index = TABS.indexOf(tab);
    let next: OfferTab | undefined;
    if (event.key === 'ArrowRight') {
      next = TABS[(index + 1) % TABS.length];
    } else if (event.key === 'ArrowLeft') {
      next = TABS[(index - 1 + TABS.length) % TABS.length];
    } else if (event.key === 'Home') {
      next = TABS[0];
    } else if (event.key === 'End') {
      next = TABS[TABS.length - 1];
    }
    if (next) {
      event.preventDefault();
      onTab(next);
      tabRefs.current[next]?.focus();
    }
  }

  const orphanNotice =
    notice && !items.some((offer) => offer.id === notice.offerId)
      ? notice
      : null;

  let content;
  if (state.status === 'loading') {
    content = <LoadingState />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (items.length === 0) {
    content = (
      <p className="text-sm">
        {tab === 'pending'
          ? t('offers.emptyPending')
          : t('offers.emptyAnswered')}
      </p>
    );
  } else {
    content = (
      <ul className="flex flex-col gap-4">
        {items.map((offer) => (
          <li key={offer.id}>
            <OfferCard
              offer={offer}
              timeZone={timeZone}
              now={now}
              notice={
                notice?.offerId === offer.id ? tErrors(notice.key) : undefined
              }
              failure={
                failure?.offerId === offer.id ? failure.error : undefined
              }
              onAccept={() => {
                setFailure(null);
                setAccepting(offer);
              }}
              onDecline={() => {
                setFailure(null);
                setDeclining(offer.id);
              }}
              label={label(offer)}
            />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-4">
      <div
        role="tablist"
        aria-label={t('offers.tabs.label')}
        className="flex flex-wrap gap-1 border-b border-border"
      >
        {TABS.map((entry) => (
          <button
            key={entry}
            ref={(element) => {
              tabRefs.current[entry] = element;
            }}
            type="button"
            role="tab"
            id={`${id}-tab-${entry}`}
            aria-selected={tab === entry}
            aria-controls={`${id}-panel`}
            tabIndex={tab === entry ? 0 : -1}
            onClick={() => onTab(entry)}
            onKeyDown={onTabKey}
            className={cn(
              'inline-flex min-h-9 items-center rounded-t-md border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring',
              tab === entry
                ? 'border-foreground text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {t(`offers.tabs.${entry}`)}
          </button>
        ))}
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      <div
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-tab-${tab}`}
        className="flex flex-col gap-4"
      >
        <p className="text-sm text-muted-foreground">
          {t('offers.zone', { zone: timeZone })}
        </p>
        {orphanNotice ? (
          <p role="alert" className="text-sm font-medium">
            {tErrors(orphanNotice.key)}
          </p>
        ) : null}
        {content}
      </div>
      <ConfirmDialog
        open={accepting !== null}
        title={
          accepting?.series
            ? t('offers.series.acceptTitle')
            : t('offers.accept.title')
        }
        body={
          accepting
            ? t(
                accepting.series
                  ? 'offers.series.acceptDescription'
                  : 'offers.accept.description',
                { offer: label(accepting) },
              )
            : ''
        }
        confirmLabel={t('offers.accept.submit')}
        onConfirm={confirmAccept}
        onCancel={() => setAccepting(null)}
      />
      <DeclineDialog
        open={declineOffer !== null}
        offerLabel={declineOffer ? label(declineOffer) : ''}
        onConfirm={confirmDecline}
        onCancel={() => setDeclining(null)}
        initialError={initialDecline?.error}
      />
    </div>
  );
}

const STATUS_KEYS = {
  pending: 'offers.status.pending',
  accepted: 'offers.status.accepted',
  declined: 'offers.status.declined',
  expired: 'offers.status.expired',
  withdrawn: 'offers.status.withdrawn',
} as const;

function OfferCard({
  offer,
  timeZone,
  now,
  notice,
  failure,
  onAccept,
  onDecline,
  label,
}: {
  offer: AssignmentOfferItem;
  timeZone: string;
  now: Date;
  notice?: string;
  failure?: unknown;
  onAccept: () => void;
  onDecline: () => void;
  label: string;
}) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const pending = offer.status === 'pending';
  const dueSoon = pending && isDueSoon(offer.respond_by, now);
  const headingId = `${id}-heading`;

  return (
    <article
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-3 rounded-md border border-border p-4"
    >
      {offer.series ? (
        <SeriesDetails
          offer={offer}
          series={offer.series}
          headingId={headingId}
          timeZone={timeZone}
        />
      ) : null}
      {(offer.series ? [] : offer.sessions).map((session, index) => {
        const times = sessionTimes(session, timeZone, language);
        return (
          <div key={session.id} className="flex min-w-0 flex-col gap-1">
            <h2
              id={index === 0 ? headingId : undefined}
              className="font-semibold break-words"
            >
              {t('offers.card.subject', {
                subject: session.subject.name,
                level: session.subject.education_level_name,
              })}
            </h2>
            <p className="break-words">
              {t('offers.card.when', {
                date: times.date,
                start: times.start,
                end: times.end,
              })}
            </p>
            <dl className="grid grid-cols-1 gap-x-3 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
              <dt className="font-medium">{t('offers.card.classroom')}</dt>
              <dd className="break-words">
                {session.classroom?.name ?? t('offers.card.noClassroom')}
              </dd>
              <dt className="font-medium">{t('offers.card.students')}</dt>
              <dd className="break-words">
                {session.participants.map((p) => p.display_name).join(', ')}
              </dd>
            </dl>
          </div>
        );
      })}
      {pending ? (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span>
            {t('offers.card.answerBy', {
              when: deadlineText(offer.respond_by, timeZone, language),
            })}
          </span>
          {dueSoon ? (
            <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-xs font-medium">
              {t('offers.card.dueSoon')}
            </span>
          ) : null}
        </p>
      ) : (
        <div className="flex flex-col gap-1 text-sm">
          <p>
            <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-xs font-medium">
              {t(STATUS_KEYS[offer.status])}
            </span>
            {offer.responded_at ? (
              <span className="ml-2">
                {t('offers.card.answeredAt', {
                  when: deadlineText(offer.responded_at, timeZone, language),
                })}
              </span>
            ) : null}
          </p>
          {offer.decline_note ? (
            <p className="break-words text-muted-foreground">
              {t('offers.card.note', { note: offer.decline_note })}
            </p>
          ) : null}
        </div>
      )}
      {notice ? (
        <p role="alert" className="text-sm font-medium">
          {notice}
        </p>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      {pending ? (
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            aria-label={t('offers.accept.for', { offer: label })}
            onClick={onAccept}
          >
            {t('offers.accept.action')}
          </Button>
          <Button
            type="button"
            variant="outline"
            aria-label={t('offers.decline.for', { offer: label })}
            onClick={onDecline}
          >
            {t('offers.decline.action')}
          </Button>
        </div>
      ) : null}
    </article>
  );
}

function SeriesDetails({
  offer,
  series,
  headingId,
  timeZone,
}: {
  offer: AssignmentOfferItem;
  series: NonNullable<AssignmentOfferItem['series']>;
  headingId: string;
  timeZone: string;
}) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const first = offer.sessions[0];
  const days = weekdayList(
    series.weekdays,
    (day) => t(`weeklyWindows.weekdays.${day as WindowWeekday}`),
    language,
  );
  const start = localTimeText(series.start_time, language);
  const end = localTimeText(
    addMinutesToTime(series.start_time, series.duration_minutes),
    language,
  );
  let ends: string;
  if (series.until) {
    ends = t('offers.series.until', {
      date: formatLocalDate(series.until, { language }),
    });
  } else if (series.count) {
    ends = t('offers.series.count', { count: series.count });
  } else {
    ends = t('offers.series.noEnd');
  }
  const more = offer.session_count - offer.sessions.length;

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <h2 id={headingId} className="font-semibold break-words">
        {t('offers.series.heading', {
          subject: series.subject.name,
          level: series.subject.education_level_name,
        })}
      </h2>
      <p className="break-words">
        {series.interval > 1
          ? t('offers.series.everyWeeks', {
              count: series.interval,
              days,
              start,
              end,
            })
          : t('offers.series.every', { days, start, end })}{' '}
        <span className="text-muted-foreground">({timeZone})</span>
      </p>
      <p className="break-words">
        {t('offers.series.from', {
          date: formatLocalDate(series.from_date, { language }),
        })}{' '}
        · {ends}
      </p>
      <dl className="grid grid-cols-1 gap-x-3 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="font-medium">{t('offers.card.classroom')}</dt>
        <dd className="break-words">
          {first?.classroom?.name ?? t('offers.card.noClassroom')}
        </dd>
        {first ? (
          <>
            <dt className="font-medium">{t('offers.card.students')}</dt>
            <dd className="break-words">
              {first.participants.map((p) => p.display_name).join(', ')}
            </dd>
          </>
        ) : null}
      </dl>
      <p className="text-sm">
        {t('offers.series.scheduled', { count: offer.session_count })}
      </p>
      {offer.sessions.length > 0 ? (
        <ul
          aria-label={t('offers.series.sessions')}
          className="flex flex-col gap-1 text-sm"
        >
          {offer.sessions.map((session) => {
            const times = sessionTimes(session, timeZone, language);
            return (
              <li key={session.id} className="break-words">
                {t('offers.card.when', {
                  date: times.date,
                  start: times.start,
                  end: times.end,
                })}
              </li>
            );
          })}
          {offer.sessions_truncated && more > 0 ? (
            <li className="text-muted-foreground">
              {t('offers.series.more', { count: more })}
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
