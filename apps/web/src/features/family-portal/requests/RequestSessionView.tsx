import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { toLanguage } from '@/i18n/language';
import { addDays, formatLocalDate, isoWeekday } from '@/lib/time';
import {
  DURATIONS,
  type AvailableSlot,
  type AvailableSlots,
  type RequestDuration,
  type StudentChoice,
  type SubjectChoice,
} from './api';
import { ConfirmRequestDialog } from './ConfirmRequestDialog';
import {
  canGoBack,
  canGoForward,
  previousWeek,
  retryMinutes,
  slotTimes,
  subjectsByLevel,
  WEEK_DAYS,
  weekRange,
} from './rules';

// "Request a session" (#59 F2, F3), presentational and mobile first (one
// column, usable at 320 px). The page passes the options, the choices and
// the week's slots. Student: shown as text when there is one, a radio group
// when there are several. Subject: a select grouped by level. Length: a
// radio group. Week: seven days from the chosen start, with "Previous week"
// (never before today) and "Next week" (never after today + 60 days). Each
// day has a heading and slot buttons labelled by their local time in the
// center zone (named once); a slot this student already requested is
// disabled and says "Requested"; a day without slots says so. Choosing a
// slot opens the confirmation (ConfirmRequestDialog). After a request, a
// polite status panel links to "My session requests".

export type LoadState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; data: T };

export type SlotsState = { status: 'idle' } | LoadState<AvailableSlots>;

export interface RequestSessionViewProps {
  students: LoadState<StudentChoice[]>;
  subjects: LoadState<SubjectChoice[]>;
  studentId: string | null;
  onStudent: (id: string) => void;
  subjectId: string | null;
  onSubject: (id: string) => void;
  duration: RequestDuration;
  onDuration: (duration: RequestDuration) => void;
  /** The first date of the week shown (center zone). */
  weekStart: string;
  /** Today in the center zone. */
  today: string;
  onWeek: (start: string) => void;
  slots: SlotsState;
  timeZone: string;
  /** Sends the request; rejects with the error to show in the dialog. */
  onRequest: (slot: AvailableSlot, note: string) => Promise<void>;
  /** Whether a request was just sent (the success panel). */
  sent: boolean;
  /** Stories: the dialog open on this slot. */
  initialSlot?: AvailableSlot;
  initialFailure?: unknown;
}

export function RequestSessionView({
  students,
  subjects,
  studentId,
  onStudent,
  subjectId,
  onSubject,
  duration,
  onDuration,
  weekStart,
  today,
  onWeek,
  slots,
  timeZone,
  onRequest,
  sent,
  initialSlot,
  initialFailure,
}: RequestSessionViewProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const success = useRef<HTMLDivElement>(null);
  const [chosen, setChosen] = useState<AvailableSlot | null>(
    initialSlot ?? null,
  );

  const studentList = students.status === 'ready' ? students.data : [];
  const subjectList = subjects.status === 'ready' ? subjects.data : [];
  const student = studentList.find((item) => item.id === studentId);
  const subject = subjectList.find((item) => item.id === subjectId);
  const range = weekRange(weekStart, today);

  async function confirm(note: string) {
    if (!chosen) {
      return;
    }
    await onRequest(chosen, note);
    setChosen(null);
    // The status notice takes the focus (the slot may be gone or disabled).
    window.setTimeout(() => success.current?.focus(), 0);
  }

  if (students.status === 'loading' || subjects.status === 'loading') {
    return <LoadingState rows={6} />;
  }
  if (students.status === 'error') {
    return <ErrorAlert error={students.error} onRetry={students.onRetry} />;
  }
  if (subjects.status === 'error') {
    return <ErrorAlert error={subjects.error} onRetry={subjects.onRetry} />;
  }
  if (studentList.length === 0) {
    return (
      <p className="text-muted-foreground">
        {t('sessionRequests.new.noStudents')}
      </p>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div
        ref={success}
        tabIndex={-1}
        role="status"
        aria-live="polite"
        className="outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        {sent ? (
          <div className="flex flex-col gap-2 rounded-md border border-border p-4">
            <p className="font-medium">{t('sessionRequests.new.sent')}</p>
            <Link
              to="/requests"
              className="w-fit text-sm font-medium underline underline-offset-4"
            >
              {t('sessionRequests.new.viewRequests')}
            </Link>
          </div>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-col gap-4">
        {studentList.length === 1 ? (
          <p>
            <span className="font-medium">
              {t('sessionRequests.new.student')}:
            </span>{' '}
            {studentList[0]!.name}
          </p>
        ) : (
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-1 font-medium">
              {t('sessionRequests.new.student')}
            </legend>
            {studentList.map((item) => (
              <label
                key={item.id}
                className="flex min-h-6 items-center gap-2 break-words"
              >
                <input
                  type="radio"
                  name={`${id}-student`}
                  value={item.id}
                  checked={item.id === studentId}
                  onChange={() => onStudent(item.id)}
                  className="size-4 shrink-0"
                />
                {item.name}
              </label>
            ))}
          </fieldset>
        )}

        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-subject`} className="font-medium">
            {t('sessionRequests.new.subject')}
          </label>
          <select
            id={`${id}-subject`}
            value={subjectId ?? ''}
            onChange={(event) => onSubject(event.target.value)}
            className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-2.5 text-base md:text-sm"
          >
            <option value="" disabled>
              {t('sessionRequests.new.chooseSubject')}
            </option>
            {subjectsByLevel(subjectList).map(([level, list]) => (
              <optgroup key={level} label={level}>
                {list.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>

        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-1 font-medium">
            {t('sessionRequests.new.length')}
          </legend>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {DURATIONS.map((minutes) => (
              <label key={minutes} className="flex min-h-6 items-center gap-2">
                <input
                  type="radio"
                  name={`${id}-length`}
                  value={minutes}
                  checked={minutes === duration}
                  onChange={() => onDuration(minutes)}
                  className="size-4 shrink-0"
                />
                {t('sessionRequests.minutes', { minutes })}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      <section
        aria-labelledby={`${id}-week`}
        className="flex min-w-0 flex-col gap-4"
      >
        <h2 id={`${id}-week`} className="text-lg font-semibold">
          {t('sessionRequests.new.week', {
            from: formatLocalDate(range.from, { language }),
            to: formatLocalDate(range.to, { language }),
          })}
        </h2>
        <p className="text-sm text-muted-foreground">
          {t('sessionRequests.new.zone', { zone: timeZone })}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={!canGoBack(weekStart, today)}
            onClick={() => onWeek(previousWeek(weekStart, today))}
          >
            {t('sessionRequests.new.previousWeek')}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!canGoForward(weekStart, today)}
            onClick={() => onWeek(addDays(weekStart, WEEK_DAYS))}
          >
            {t('sessionRequests.new.nextWeek')}
          </Button>
        </div>
        <SlotList
          slots={slots}
          ready={Boolean(student && subject)}
          timeZone={timeZone}
          onChoose={setChosen}
        />
      </section>

      <ConfirmRequestDialog
        slot={chosen}
        summary={{
          studentName: student?.name ?? '',
          subjectName: subject
            ? t('sessionRequests.subject', {
                subject: subject.name,
                level: subject.levelName,
              })
            : '',
          duration,
        }}
        timeZone={timeZone}
        onConfirm={confirm}
        onCancel={() => setChosen(null)}
        initialFailure={initialFailure}
      />
    </div>
  );
}

/**
 * The days and slot buttons of a slot search (also the reschedule picker
 * of #62b). With `selected` (a slot's `starts_at`, or null), the buttons
 * are toggle buttons and the chosen one is pressed; without it, as on
 * "Request a session", a button only reports the choice.
 */
export function SlotList({
  slots,
  ready,
  timeZone,
  onChoose,
  selected,
}: {
  slots: SlotsState;
  ready: boolean;
  timeZone: string;
  onChoose: (slot: AvailableSlot) => void;
  selected?: string | null;
}) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  if (!ready || slots.status === 'idle') {
    return (
      <p className="text-muted-foreground">
        {t('sessionRequests.new.chooseFirst')}
      </p>
    );
  }
  if (slots.status === 'loading') {
    return <LoadingState rows={5} label={t('sessionRequests.new.loading')} />;
  }
  if (slots.status === 'error') {
    const minutes = retryMinutes(slots.error);
    return (
      <ErrorAlert
        error={slots.error}
        onRetry={slots.onRetry}
        message={
          minutes !== null
            ? t('sessionRequests.tooMany', { count: minutes })
            : undefined
        }
      />
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-5">
      {slots.data.days.map((day) => {
        const headingId = `${id}-${day.date}`;
        return (
          <section key={day.date} aria-labelledby={headingId}>
            <h3 id={headingId} className="mb-2 font-medium">
              {t('sessionRequests.new.day', {
                weekday: t(
                  `weeklyWindows.weekdays.${isoWeekday(day.date) as 1 | 2 | 3 | 4 | 5 | 6 | 7}`,
                ),
                date: formatLocalDate(day.date, { language }),
              })}
            </h3>
            {day.slots.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t('sessionRequests.new.noSlots')}
              </p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {day.slots.map((slot) => {
                  const times = slotTimes(slot, timeZone, language);
                  const label = t('sessionRequests.new.slot', {
                    start: times.start,
                    end: times.end,
                  });
                  const pressed =
                    selected === undefined
                      ? undefined
                      : selected === slot.starts_at;
                  return (
                    <li key={slot.starts_at}>
                      <Button
                        type="button"
                        variant={pressed ? 'default' : 'outline'}
                        aria-pressed={pressed}
                        className="h-auto min-h-11 min-w-24 py-2"
                        disabled={slot.already_requested}
                        onClick={() => onChoose(slot)}
                      >
                        {slot.already_requested
                          ? t('sessionRequests.new.requestedSlot', {
                              slot: label,
                            })
                          : label}
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
