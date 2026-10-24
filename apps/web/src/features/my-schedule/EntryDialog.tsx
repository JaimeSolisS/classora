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
import { sessionTimeText } from '@/features/calendar/labels';
import type { ScheduleEntry } from './api';
import { EntryStateLabel } from './EntryContent';
import { dateText, whoText, type LabelContext } from './labels';

// The details of one "My schedule" item (#67 E6; the #57 D8 dialog): only
// the item's own fields, from the loaded data. A tutor's `offered` item
// links to /offers, where the answer is given. `action` is the page's slot
// under the details (#62b A1: "Request a change"). Focus is trapped while
// it is open and goes back to the event on close.

export interface EntryDialogProps {
  entry: ScheduleEntry | null;
  context: LabelContext;
  onClose: () => void;
  onReturnFocus: () => void;
  /** Rendered under the details (#62b A1). */
  action?: ReactNode;
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
}: EntryDialogProps) {
  const { t } = useTranslation();
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
          </dl>
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
