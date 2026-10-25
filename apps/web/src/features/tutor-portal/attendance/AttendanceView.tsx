import { useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { toLanguage } from '@/i18n/language';
import { formatInstant, formatLocalDate, toLocalDate } from '@/lib/time';
import type {
  AttendanceItem,
  AttendanceMark,
  AttendanceStatus,
  SessionAttendance,
} from './api';
import { ATTENDANCE_STATUSES } from './api';
import { CorrectionHistory } from './CorrectionHistory';
import {
  changedMarks,
  initialChoices,
  latestCorrection,
  type Choices,
} from './rules';
import { StatusLabel } from './StatusLabel';

// The tutor's attendance page content (#76 G1), presentational: the class
// (subject, local date and time in the center zone, state), then one radio
// group per participant (Present / Late / Absent; the group is named by the
// student), with the saved status as an icon and text. "Save attendance"
// sends only new or changed marks with the session's version. A row the
// center corrected is read-only ("Corrected by the center", the latest
// reason, the history). When attendance can't be taken the reason replaces
// the form (not started: when it opens; completed: ask the center; other
// states: the state) and the marks are shown read-only. A version conflict
// offers Reload. Phone-first: the Save button stays at the bottom of the
// screen while the list scrolls.

export type AttendanceViewState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; attendance: SessionAttendance };

export interface AttendanceViewProps {
  state: AttendanceViewState;
  timeZone: string;
  /** Sends the marks; resolves once saved (the page reloads the data). */
  onSave: (marks: AttendanceMark[]) => Promise<void>;
  /** Reloads the attendance (after a version conflict). */
  onReload: () => void;
}

export function AttendanceView({
  state,
  timeZone,
  onSave,
  onReload,
}: AttendanceViewProps) {
  const { t } = useTranslation();
  const [notice, setNotice] = useState<string | null>(null);

  if (state.status === 'loading') {
    return <LoadingState rows={4} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { attendance } = state;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ClassHeader attendance={attendance} timeZone={timeZone} />
      {notice ? (
        <p role="status" className="text-sm font-medium">
          {notice}
        </p>
      ) : null}
      {attendance.can_mark ? (
        <MarkForm
          key={attendance.version}
          attendance={attendance}
          timeZone={timeZone}
          onReload={() => {
            setNotice(null);
            onReload();
          }}
          onSave={async (marks) => {
            setNotice(null);
            await onSave(marks);
            setNotice(t('attendance.saved'));
          }}
          onNoChanges={() => setNotice(t('attendance.noChanges'))}
        />
      ) : (
        <>
          <BlockMessage attendance={attendance} timeZone={timeZone} />
          <ReadOnlyList attendance={attendance} timeZone={timeZone} />
        </>
      )}
    </div>
  );
}

function useWhen(timeZone: string) {
  const { i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  return {
    language,
    when: (instant: string, style: 'dateTime' | 'time' = 'dateTime') =>
      formatInstant(instant, { timeZone, language, style }),
  };
}

function ClassHeader({
  attendance,
  timeZone,
}: {
  attendance: SessionAttendance;
  timeZone: string;
}) {
  const { t } = useTranslation();
  const { language, when } = useWhen(timeZone);
  const date = formatLocalDate(toLocalDate(attendance.starts_at, timeZone), {
    language,
    style: 'long',
  });
  return (
    <section className="flex min-w-0 flex-col gap-1">
      <h2 className="text-lg font-semibold break-words">
        {t('scheduling.calendar.dialog.subjectValue', {
          subject: attendance.subject.name,
          level: attendance.subject.education_level_name,
        })}
      </h2>
      <p className="text-sm">
        {t('attendance.when', {
          date,
          start: when(attendance.starts_at, 'time'),
          end: when(attendance.ends_at, 'time'),
          zone: timeZone,
        })}
      </p>
      <p className="text-sm">
        {t('attendance.state', {
          state: t(`scheduling.states.${attendance.state}`),
        })}
      </p>
    </section>
  );
}

function BlockMessage({
  attendance,
  timeZone,
}: {
  attendance: SessionAttendance;
  timeZone: string;
}) {
  const { t } = useTranslation();
  const { when } = useWhen(timeZone);
  let text: string;
  switch (attendance.block) {
    case 'attendance_not_started':
      text = t('attendance.blocked.attendance_not_started', {
        time: when(attendance.starts_at),
      });
      break;
    case 'attendance_locked':
      text = t('attendance.blocked.attendance_locked');
      break;
    case 'attendance_not_recordable':
      text = t('attendance.blocked.attendance_not_recordable', {
        state: t(`scheduling.states.${attendance.state}`),
      });
      break;
    default:
      text = t('attendance.readOnly');
  }
  return (
    <p className="rounded-md border border-border p-3 text-sm font-medium">
      {text}
    </p>
  );
}

function CorrectedNote({
  item,
  timeZone,
}: {
  item: AttendanceItem;
  timeZone: string;
}) {
  const { t } = useTranslation();
  const latest = latestCorrection(item);
  if (!latest) {
    return null;
  }
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="font-medium">{t('attendance.corrected')}</p>
      <p className="break-words">
        {t('attendance.reason', { reason: latest.reason })}
      </p>
      <CorrectionHistory corrections={item.corrections} timeZone={timeZone} />
    </div>
  );
}

function ReadOnlyRow({
  item,
  timeZone,
}: {
  item: AttendanceItem;
  timeZone: string;
}) {
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-3">
      <p className="font-medium break-words">{item.student.display_name}</p>
      <StatusLabel status={item.status} />
      <CorrectedNote item={item} timeZone={timeZone} />
    </li>
  );
}

function ReadOnlyList({
  attendance,
  timeZone,
}: {
  attendance: SessionAttendance;
  timeZone: string;
}) {
  const { t } = useTranslation();
  if (attendance.items.length === 0) {
    return <p className="text-sm">{t('attendance.noStudents')}</p>;
  }
  return (
    <ul className="flex flex-col gap-3" aria-label={t('attendance.listLabel')}>
      {attendance.items.map((item) => (
        <ReadOnlyRow key={item.student.id} item={item} timeZone={timeZone} />
      ))}
    </ul>
  );
}

function MarkForm({
  attendance,
  timeZone,
  onSave,
  onReload,
  onNoChanges,
}: {
  attendance: SessionAttendance;
  timeZone: string;
  onSave: (marks: AttendanceMark[]) => Promise<void>;
  onReload: () => void;
  onNoChanges: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [choices, setChoices] = useState<Choices>(() =>
    initialChoices(attendance),
  );
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const conflict =
    failure instanceof ApiError && failure.code === 'version_conflict';

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    const marks = changedMarks(attendance, choices);
    setFailure(null);
    if (marks.length === 0) {
      onNoChanges();
      return;
    }
    setBusy(true);
    try {
      await onSave(marks);
    } catch (caught) {
      setFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  function choose(studentId: string, status: AttendanceStatus) {
    setChoices((current) => ({ ...current, [studentId]: status }));
  }

  if (attendance.items.length === 0) {
    return <p className="text-sm">{t('attendance.noStudents')}</p>;
  }

  return (
    <form
      noValidate
      onSubmit={(event) => void submit(event)}
      className="flex min-w-0 flex-col gap-4"
    >
      {conflict ? (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-md border border-destructive p-3 text-sm"
        >
          <p className="font-medium">{t('attendance.conflict')}</p>
          <div>
            <Button type="button" variant="outline" onClick={onReload}>
              {t('attendance.reload')}
            </Button>
          </div>
        </div>
      ) : failure ? (
        <ErrorAlert error={failure} />
      ) : null}
      <ul
        className="flex flex-col gap-3"
        aria-label={t('attendance.listLabel')}
      >
        {attendance.items.map((item) =>
          item.corrected ? (
            <ReadOnlyRow
              key={item.student.id}
              item={item}
              timeZone={timeZone}
            />
          ) : (
            <li key={item.student.id} className="min-w-0">
              <fieldset className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-3">
                <legend className="px-1 font-medium break-words">
                  {item.student.display_name}
                </legend>
                <p className="text-sm text-muted-foreground">
                  <span className="sr-only">
                    {t('attendance.savedStatus')}{' '}
                  </span>
                  <StatusLabel status={item.status} />
                </p>
                <div className="flex flex-wrap gap-2">
                  {ATTENDANCE_STATUSES.map((status) => {
                    const inputId = `${id}-${item.student.id}-${status}`;
                    return (
                      <label
                        key={status}
                        htmlFor={inputId}
                        className="flex min-h-11 cursor-pointer items-center gap-2 rounded-md border border-input px-3 py-2 text-sm has-checked:border-ring has-checked:ring-2 has-checked:ring-ring"
                      >
                        <input
                          id={inputId}
                          type="radio"
                          name={`${id}-${item.student.id}`}
                          value={status}
                          checked={choices[item.student.id] === status}
                          disabled={busy}
                          onChange={() => choose(item.student.id, status)}
                          className="size-4"
                        />
                        <StatusLabel status={status} />
                      </label>
                    );
                  })}
                </div>
              </fieldset>
            </li>
          ),
        )}
      </ul>
      <div className="sticky bottom-0 z-10 -mx-4 border-t border-border bg-background px-4 py-3">
        <Button type="submit" disabled={busy} className="w-full sm:w-auto">
          {busy ? (
            <Spinner label={t('attendance.saving')} showLabel />
          ) : (
            t('attendance.save')
          )}
        </Button>
      </div>
    </form>
  );
}
