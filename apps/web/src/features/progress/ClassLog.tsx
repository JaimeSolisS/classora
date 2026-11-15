import { Check, CircleHelp, Clock, X } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/feedback/Spinner';
import { BADGE_CLASS, SMALL_LINK_CLASS } from '@/features/academics/styles';
import { useFormatters } from '@/lib/formatters';
import type { SessionLogItem, SessionLogMark } from './api';
import { TutorNotesDisclosure } from './TutorNotesDisclosure';

// "Classes" (#322 C4, C5): the class log, newest first as the API orders
// it. Each item: the date and time in the center zone, the subject, the
// kind, "with {tutor}", the student's attendance as a text badge with an
// icon, and the approved summary as a labelled description list (plain
// text, never HTML), or "Summary not available yet.". Families get the
// lazy "Notes from the tutor" disclosure; staff and tutors only the count.
// Staff with schedule.view get "Session details". "Load more" follows
// next_cursor and moves focus to the first new item once it's loaded.
// Loading and error states are local to the section.

export interface LogState {
  status: 'loading' | 'error' | 'ready';
  error?: unknown;
  items: readonly SessionLogItem[];
  hasMore: boolean;
  loadingMore: boolean;
  onRetry: () => void;
  onMore: () => Promise<unknown>;
}

export type LogAudience = 'family' | 'staff';

const SUMMARY_FIELDS = [
  'topics',
  'performance',
  'homework',
  'next_steps',
] as const;

const MARK_ICONS = {
  present: Check,
  late: Clock,
  absent: X,
} as const;

export function AttendanceBadge({ mark }: { mark: SessionLogMark | null }) {
  const { t } = useTranslation();
  const Icon = mark ? MARK_ICONS[mark] : CircleHelp;
  return (
    <span className={`${BADGE_CLASS} gap-1`}>
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {mark ? t(`attendance.statuses.${mark}`) : t('attendance.notRecorded')}
    </span>
  );
}

function LogItem({
  item,
  audience,
  studentId,
  showsSessionLink,
  focusRef,
}: {
  item: SessionLogItem;
  audience: LogAudience;
  studentId: string;
  showsSessionLink: boolean;
  focusRef?: (element: HTMLElement | null) => void;
}) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const id = useId();
  const summary = item.summary;
  return (
    <li className="min-w-0">
      <article
        ref={focusRef}
        tabIndex={-1}
        aria-labelledby={`${id}-title`}
        className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-card p-4 text-card-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <h3
            id={`${id}-title`}
            className="min-w-0 text-base font-semibold break-words"
          >
            {formatInstant(item.starts_at)}
          </h3>
          <AttendanceBadge mark={item.attendance} />
        </div>
        <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm">
          <span className="break-words">{item.subject.name}</span>
          <span>{t(`progress.log.kinds.${item.session_kind}`)}</span>
          {item.tutor ? (
            <span className="break-words">
              {t('progress.log.with', { name: item.tutor.display_name })}
            </span>
          ) : null}
        </p>
        {summary ? (
          <dl className="grid min-w-0 gap-x-4 gap-y-1 text-sm sm:grid-cols-[10rem_1fr]">
            {SUMMARY_FIELDS.filter((field) => summary[field] !== null).map(
              (field) => (
                <div key={field} className="contents">
                  <dt className="font-medium">
                    {t(`progress.log.fields.${field}`)}
                  </dt>
                  <dd className="min-w-0 break-words whitespace-pre-line">
                    {summary[field]}
                  </dd>
                </div>
              ),
            )}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">
            {t('progress.log.noSummary')}
          </p>
        )}
        {item.published_note_count > 0 ? (
          audience === 'family' ? (
            <TutorNotesDisclosure
              studentId={studentId}
              sessionId={item.class_session_id}
              count={item.published_note_count}
            />
          ) : (
            <p className="text-sm">
              {t('progress.log.noteCount', {
                count: item.published_note_count,
              })}
            </p>
          )
        ) : null}
        {showsSessionLink ? (
          <Link
            to={`/schedule/sessions/${encodeURIComponent(item.class_session_id)}`}
            className={`${SMALL_LINK_CLASS} self-start`}
          >
            {t('progress.log.details')}
          </Link>
        ) : null}
      </article>
    </li>
  );
}

export function ClassLog({
  state,
  audience,
  studentId,
  showsSessionLink = false,
}: {
  state: LogState;
  audience: LogAudience;
  studentId: string;
  /** Staff with schedule.view: "Session details" per item. */
  showsSessionLink?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  // After "Load more": the index of the first new item, focused once the
  // page has arrived (the button may disappear with the last page).
  const [focusIndex, setFocusIndex] = useState<number | null>(null);
  const target = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (focusIndex !== null && target.current) {
      target.current.focus();
      setFocusIndex(null);
    }
  }, [focusIndex, state.items.length]);

  async function more() {
    const first = state.items.length;
    target.current = null;
    await state.onMore();
    setFocusIndex(first);
  }

  let content;
  if (state.status === 'loading') {
    content = <LoadingState rows={3} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.items.length === 0) {
    content = <p className="text-sm">{t('progress.log.empty')}</p>;
  } else {
    content = (
      <div className="flex min-w-0 flex-col gap-3">
        <ul className="flex min-w-0 flex-col gap-3">
          {state.items.map((item, index) => (
            <LogItem
              key={item.class_session_id}
              item={item}
              audience={audience}
              studentId={studentId}
              showsSessionLink={showsSessionLink}
              focusRef={
                index === focusIndex
                  ? (element) => {
                      target.current = element;
                    }
                  : undefined
              }
            />
          ))}
        </ul>
        {state.hasMore ? (
          <Button
            type="button"
            variant="outline"
            className="self-start"
            disabled={state.loadingMore}
            onClick={() => void more()}
          >
            {state.loadingMore ? (
              <Spinner label={t('progress.log.loadingMore')} showLabel />
            ) : (
              t('progress.log.loadMore')
            )}
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('progress.log.heading')}
      </h2>
      {content}
    </section>
  );
}
