import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { buttonVariants } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { sessionTimeText } from '@/features/calendar/labels';
import { attendancePath } from '@/features/tutor-portal/attendance/api';
import {
  showsFamilyAttendance,
  tutorAttendanceLink,
} from '@/features/tutor-portal/attendance/rules';
import { StatusLabel } from '@/features/tutor-portal/attendance/StatusLabel';
import {
  closeoutKey,
  closeoutPath,
  fetchCloseout,
} from '@/features/tutor-portal/closeout/api';
import { tutorCloseoutLink } from '@/features/tutor-portal/closeout/rules';
import { notesPath } from '@/features/tutor-portal/notes/api';
import { tutorNotesLink } from '@/features/tutor-portal/notes/rules';
import type { ScheduleEntry } from './api';
import { EntryStateLabel } from './EntryContent';
import { dateText, whoText, type LabelContext } from './labels';

// The details of one "My schedule" item (#67 E6; the #57 D8 dialog): only
// the item's own fields, from the loaded data. A tutor's `offered` item
// links to /offers, where the answer is given. `action` is the page's slot
// under the details (#62b A1: "Request a change"). Focus is trapped while
// it is open and goes back to the event on close.
// #76 G2: a tutor's confirmed item that has started links to "Take
// attendance", a completed one to "View attendance"; a family item that has
// started (confirmed or completed) shows the student's own attendance.
// #77 G2: next to it, a started confirmed item offers "Close out session"
// (primary) and a completed one "View closeout".
// #305 C1: a tutor's completed item reads its closeout when the dialog
// opens (shared with the closeout page's cache); changes requested shows a
// "Changes requested" badge and "Fix closeout" (primary). While loading,
// on an error or in any other status: "View closeout", nothing else.
// #79 H2: a tutor's confirmed or completed item links to "Notes" (the
// session's notes page); a family's shared notes come in the `action` slot.

export interface EntryDialogProps {
  entry: ScheduleEntry | null;
  context: LabelContext;
  onClose: () => void;
  onReturnFocus: () => void;
  /** Rendered under the details (#62b A1). */
  action?: ReactNode;
  /** The clock (tests). */
  now?: () => number;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[8rem_1fr] sm:gap-3">
      <dt className="font-medium">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export function EntryDialog({
  entry,
  context,
  onClose,
  onReturnFocus,
  action,
  now = Date.now,
}: EntryDialogProps) {
  const { t } = useTranslation();
  const completedTutorId =
    entry?.audience === 'tutor' && entry.item.state === 'completed'
      ? entry.item.id
      : null;
  const closeout = useQuery({
    queryKey: closeoutKey(completedTutorId ?? ''),
    queryFn: ({ signal }) => fetchCloseout(completedTutorId ?? '', signal),
    enabled: completedTutorId !== null,
    retry: false,
  });
  const closeoutStatus =
    completedTutorId !== null ? closeout.data?.closeout?.status : undefined;
  const attendanceLink =
    entry?.audience === 'tutor' ? tutorAttendanceLink(entry.item, now()) : null;
  const closeoutLink =
    entry?.audience === 'tutor'
      ? tutorCloseoutLink(entry.item, now(), closeoutStatus)
      : null;
  const notesLink = entry?.audience === 'tutor' && tutorNotesLink(entry.item);
  return (
    <Dialog
      open={entry !== null}
      onOpenChange={(open) => {
        if (!open) {
          onClose();
        }
      }}
    >
      {entry ? (
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            onReturnFocus();
          }}
        >
          <DialogHeader>
            <DialogTitle>{entry.item.subject.name}</DialogTitle>
            <DialogDescription>
              {t('scheduling.calendar.dialog.description')}
            </DialogDescription>
          </DialogHeader>
          <dl className="flex flex-col gap-3">
            <Row label={t('scheduling.calendar.dialog.subject')}>
              {t('scheduling.calendar.dialog.subjectValue', {
                subject: entry.item.subject.name,
                level: entry.item.subject.education_level_name,
              })}
            </Row>
            <Row label={t('scheduling.calendar.dialog.kind')}>
              {t(`scheduling.calendar.filters.kinds.${entry.item.kind}`)}
            </Row>
            <Row
              label={
                entry.audience === 'family'
                  ? t('scheduling.calendar.dialog.tutor')
                  : t('scheduling.calendar.dialog.students')
              }
            >
              {entry.audience === 'family'
                ? (entry.item.tutor?.display_name ?? t('mySchedule.noTutor'))
                : whoText(entry, t)}
            </Row>
            <Row label={t('scheduling.calendar.dialog.room')}>
              {entry.item.classroom?.name ??
                t('scheduling.calendar.dialog.noRoom')}
            </Row>
            <Row label={t('scheduling.calendar.dialog.date')}>
              {dateText(entry, context)}
            </Row>
            <Row label={t('scheduling.calendar.dialog.time')}>
              {t('scheduling.calendar.dialog.timeValue', {
                time: sessionTimeText(entry.item, context),
                zone: context.timeZone,
              })}
            </Row>
            <Row label={t('scheduling.calendar.dialog.state')}>
              <EntryStateLabel entry={entry} context={context} />
            </Row>
            {entry.audience === 'family' &&
            showsFamilyAttendance(entry.item, now()) ? (
              <Row label={t('attendance.familyRow')}>
                <StatusLabel status={entry.item.attendance} />
              </Row>
            ) : null}
          </dl>
          {closeoutLink || attendanceLink || notesLink ? (
            <div className="flex flex-wrap items-center gap-3">
              {closeoutLink === 'fix' ? (
                <span className="rounded-md border border-border px-2 py-0.5 text-sm font-medium">
                  {t('closeout.tutorView.badge')}
                </span>
              ) : null}
              {closeoutLink ? (
                <Link
                  to={closeoutPath(entry.item.id)}
                  className={
                    closeoutLink === 'view'
                      ? 'text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring'
                      : buttonVariants({ size: 'sm' })
                  }
                >
                  {closeoutLink === 'submit'
                    ? t('closeout.closeOut')
                    : closeoutLink === 'fix'
                      ? t('closeout.tutorView.fix')
                      : t('closeout.view')}
                </Link>
              ) : null}
              {attendanceLink ? (
                <Link
                  to={attendancePath(entry.item.id)}
                  className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {attendanceLink === 'take'
                    ? t('attendance.take')
                    : t('attendance.view')}
                </Link>
              ) : null}
              {notesLink ? (
                <Link
                  to={notesPath(entry.item.id)}
                  className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {t('notes.open')}
                </Link>
              ) : null}
            </div>
          ) : null}
          {entry.audience === 'tutor' && entry.item.state === 'offered' ? (
            <Link
              to="/offers"
              className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
            >
              {t('mySchedule.answerOffer')}
            </Link>
          ) : null}
          {action}
          <DialogFooter showCloseButton />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
