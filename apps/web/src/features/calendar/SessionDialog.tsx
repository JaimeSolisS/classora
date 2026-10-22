import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { formatLocalDate, toLocalDate } from '@/lib/time';
import type { CalendarItem, CalendarLoaders } from './api';
import { MarkerLabel, StateLabel } from './EventContent';
import { sessionTimeText, studentNames, type LabelContext } from './labels';

// The details of one session (#57 D8), from the item already loaded (no
// request). No actions yet: offering, cancelling, rescheduling and
// overrides come with #52, #58 and #62. Focus is trapped while it is open
// and goes back to the event on close (`onCloseAutoFocus`). An occurrence
// of a weekly series gets "Open series" (#54b D2), once its session is read
// (`fetchSeriesId`; the range item has no series id).

export interface SessionDialogProps {
  item: CalendarItem | null;
  context: LabelContext;
  onClose: () => void;
  /** Puts the focus back on the event that opened the dialog. */
  onReturnFocus: () => void;
  /** Reads the session's series (#54b D2); none: no "Open series" link. */
  fetchSeriesId?: CalendarLoaders['fetchSeriesId'];
}

function OpenSeriesLink({
  sessionId,
  fetchSeriesId,
}: {
  sessionId: string;
  fetchSeriesId: NonNullable<CalendarLoaders['fetchSeriesId']>;
}) {
  const { t } = useTranslation();
  const seriesId = useQuery({
    queryKey: ['scheduling', 'calendar', 'series-of', sessionId],
    queryFn: ({ signal }) => fetchSeriesId(sessionId, signal),
    retry: false,
  });
  if (!seriesId.data) {
    return null;
  }
  return (
    <Link
      to={`/schedule/series/${encodeURIComponent(seriesId.data)}`}
      className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      {t('scheduling.calendar.dialog.openSeries')}
    </Link>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[8rem_1fr] sm:gap-3">
      <dt className="font-medium">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export function SessionDialog({
  item,
  context,
  onClose,
  onReturnFocus,
  fetchSeriesId,
}: SessionDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={item !== null}
      onOpenChange={(open) => {
        if (!open) {
          onClose();
        }
      }}
    >
      {item ? (
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            onReturnFocus();
          }}
        >
          <DialogHeader>
            <DialogTitle>{item.subject.name}</DialogTitle>
            <DialogDescription>
              {t('scheduling.calendar.dialog.description')}
            </DialogDescription>
          </DialogHeader>
          <dl className="flex flex-col gap-3">
            <Row label={t('scheduling.calendar.dialog.subject')}>
              {t('scheduling.calendar.dialog.subjectValue', {
                subject: item.subject.name,
                level: item.subject.education_level_name,
              })}
            </Row>
            <Row label={t('scheduling.calendar.dialog.kind')}>
              {t(`scheduling.calendar.filters.kinds.${item.kind}`)}
            </Row>
            <Row label={t('scheduling.calendar.dialog.students')}>
              {studentNames(item, context)}
            </Row>
            <Row label={t('scheduling.calendar.dialog.tutor')}>
              {item.tutor?.display_name ?? t('scheduling.calendar.noTutor')}
            </Row>
            <Row label={t('scheduling.calendar.dialog.room')}>
              {item.classroom?.name ?? t('scheduling.calendar.dialog.noRoom')}
            </Row>
            <Row label={t('scheduling.calendar.dialog.date')}>
              {formatLocalDate(toLocalDate(item.starts_at, context.timeZone), {
                language: context.language,
                style: 'long',
              })}
            </Row>
            <Row label={t('scheduling.calendar.dialog.time')}>
              {t('scheduling.calendar.dialog.timeValue', {
                time: sessionTimeText(item, context),
                zone: context.timeZone,
              })}
            </Row>
            <Row label={t('scheduling.calendar.dialog.state')}>
              <StateLabel state={item.state} />
            </Row>
            {item.markers.length > 0 ? (
              <Row label={t('scheduling.calendar.dialog.markers')}>
                <ul className="flex flex-col gap-2">
                  {item.markers.map((code) => (
                    <li key={code} className="flex flex-col gap-1">
                      <MarkerLabel code={code} />
                      <span>{t(`scheduling.markers.${code}.explanation`)}</span>
                    </li>
                  ))}
                </ul>
              </Row>
            ) : null}
          </dl>
          {fetchSeriesId ? (
            <OpenSeriesLink sessionId={item.id} fetchSeriesId={fetchSeriesId} />
          ) : null}
          <DialogFooter showCloseButton />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
