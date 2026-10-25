import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type {
  AttendanceItem,
  AttendanceStatus,
  SessionAttendance,
} from '@/features/tutor-portal/attendance/api';
import { CorrectionHistory } from '@/features/tutor-portal/attendance/CorrectionHistory';
import { StatusLabel } from '@/features/tutor-portal/attendance/StatusLabel';
import { CorrectAttendanceDialog } from './CorrectAttendanceDialog';

// The session's attendance on the staff session page (#76 G3),
// presentational: a table (student, status as an icon and text, who marked
// it and when, a "Corrected" badge) with each row's correction history.
// Holders of attendance.correct get "Correct" per row while a correction is
// possible (`can_correct`), which opens the dialog. Before the class starts
// it only says when attendance opens. The page renders it for confirmed and
// completed sessions only.

export type AttendanceSectionState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; attendance: SessionAttendance };

export interface AttendanceSectionProps {
  state: AttendanceSectionState;
  timeZone: string;
  /** Whether the user holds attendance.correct. */
  canCorrect: boolean;
  /** Sends a correction; resolves once saved, rejects with the error. */
  onCorrect: (
    item: AttendanceItem,
    status: AttendanceStatus,
    reason: string,
  ) => Promise<void>;
  /** Reloads the attendance (after a version conflict in the dialog). */
  onReload: () => void;
}

export function AttendanceSection({
  state,
  timeZone,
  canCorrect,
  onCorrect,
  onReload,
}: AttendanceSectionProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const [correcting, setCorrecting] = useState<AttendanceItem | null>(null);
  const when = (instant: string) =>
    formatInstant(instant, { timeZone, language, style: 'dateTime' });

  let body;
  let scrolls = false;
  if (state.status === 'loading') {
    body = <LoadingState rows={2} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.attendance.block === 'attendance_not_started') {
    body = <p className="text-sm">{t('attendance.section.notStarted')}</p>;
  } else if (state.attendance.items.length === 0) {
    body = <p className="text-sm">{t('attendance.noStudents')}</p>;
  } else {
    const { attendance } = state;
    const showCorrect = canCorrect && attendance.can_correct;
    // #311: when the table is wider than the section it scrolls inside this
    // region, never the page. `relative` makes the region the containing
    // block of the absolutely positioned `sr-only` header ("Actions"), which
    // otherwise sat outside the scroller and widened the page. Read-only
    // rows hold nothing focusable, so the region takes the focus (axe
    // scrollable-region-focusable, WCAG 2.1.1) and is named by the heading;
    // the section then drops its own name, so there is one "Attendance"
    // landmark (axe landmark-unique).
    scrolls = true;
    body = (
      <div
        role="region"
        aria-labelledby={`${id}-heading`}
        tabIndex={0}
        className="relative min-w-0 overflow-x-auto rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        <table className="w-full min-w-[32rem] text-left text-sm">
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className="py-2 pr-3 font-medium">
                {t('attendance.section.student')}
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                {t('attendance.section.status')}
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                {t('attendance.section.marked')}
              </th>
              {showCorrect ? (
                <th scope="col" className="py-2 font-medium">
                  <span className="sr-only">
                    {t('attendance.section.actions')}
                  </span>
                </th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {attendance.items.map((item) => (
              <tr
                key={item.student.id}
                className="border-b border-border align-top"
              >
                <th scope="row" className="py-2 pr-3 font-medium break-words">
                  {item.student.display_name}
                </th>
                <td className="py-2 pr-3">
                  <div className="flex flex-col gap-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <StatusLabel status={item.status} />
                      {item.corrected ? (
                        <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-xs font-medium">
                          {t('attendance.section.correctedBadge')}
                        </span>
                      ) : null}
                    </span>
                    <CorrectionHistory
                      corrections={item.corrections}
                      timeZone={timeZone}
                    />
                  </div>
                </td>
                <td className="py-2 pr-3 break-words">
                  {item.marked_by && item.marked_at
                    ? t('attendance.markedBy', {
                        name: item.marked_by.display_name,
                        when: when(item.marked_at),
                      })
                    : t('attendance.notRecorded')}
                </td>
                {showCorrect ? (
                  <td className="py-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label={t('attendance.section.correctFor', {
                        name: item.student.display_name,
                      })}
                      onClick={() => setCorrecting(item)}
                    >
                      {t('attendance.section.correct')}
                    </Button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <section
      aria-labelledby={scrolls ? undefined : `${id}-heading`}
      className="flex flex-col gap-3"
    >
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('attendance.section.heading')}
      </h2>
      {body}
      <CorrectAttendanceDialog
        item={correcting}
        onCancel={() => setCorrecting(null)}
        onConfirm={async (status, reason) => {
          if (correcting) {
            await onCorrect(correcting, status, reason);
          }
          setCorrecting(null);
        }}
        onReload={() => {
          setCorrecting(null);
          onReload();
        }}
      />
    </section>
  );
}
