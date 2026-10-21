import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import {
  conflictsOf,
  type ClassSessionCreated,
  type Option,
  type SchedulingConflict,
  type SubjectOption,
} from './api';
import { ConflictPanel } from './ConflictPanel';
import {
  EMPTY_SCHEDULE,
  FIELD_ORDER,
  serverErrors,
  subjectsByLevel,
  validateSchedule,
  type ScheduleErrors,
  type ScheduleField,
  type ScheduleMessage,
  type ScheduleValues,
} from './rules';

// The "Schedule a session" form (#48 F2, F3), without any API call: the
// page passes the options, the student search and `onSubmit` (which
// resolves with the created session or rejects with the error to show).
// Times are local to the center zone, which is shown next to them. A 201
// shows the success panel ("Schedule another" keeps the date); a 409
// scheduling_conflict shows the conflict panel and keeps the form; a 422
// puts the field errors on their fields; anything else is an ErrorAlert.

export interface ScheduleSessionFormProps {
  subjects: readonly SubjectOption[];
  tutors: readonly Option[];
  classrooms: readonly Option[];
  searchStudents: (q: string, signal: AbortSignal) => Promise<Option[]>;
  timeZone: string;
  onSubmit: (values: ScheduleValues) => Promise<ClassSessionCreated>;
  /** The values to start from (stories). */
  initialValues?: ScheduleValues;
  /** The id of the page heading that names the form. */
  labelledBy: string;
}

export function ScheduleSessionForm({
  subjects,
  tutors,
  classrooms,
  searchStudents,
  timeZone,
  onSubmit,
  initialValues = EMPTY_SCHEDULE,
  labelledBy,
}: ScheduleSessionFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const { formatInstant } = useFormatters();
  const id = useId();
  const [values, setValues] = useState<ScheduleValues>(initialValues);
  const [errors, setErrors] = useState<ScheduleErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [created, setCreated] = useState<ClassSessionCreated | null>(null);
  const inFlight = useRef(false);
  const successHeading = useRef<HTMLHeadingElement>(null);

  const fieldId = (field: ScheduleField) => `${id}-${field}`;
  const errorId = (field: ScheduleField) => `${id}-${field}-error`;

  function message(key: ScheduleMessage | undefined): string | undefined {
    if (!key) {
      return undefined;
    }
    return key.startsWith('errors:')
      ? tErrors(key.slice('errors:'.length) as 'validation_error')
      : t(key as 'scheduling.validation.studentRequired');
  }

  function change<K extends keyof ScheduleValues>(
    key: K,
    value: ScheduleValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function focusFirst(found: ScheduleErrors) {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (!first) {
      return;
    }
    const element =
      first === 'student'
        ? document
            .getElementById(`${fieldId('student')}-picker`)
            ?.querySelector('input')
        : document.getElementById(fieldId(first));
    element?.focus();
  }

  async function submit() {
    setConflicts(null);
    setFailure(null);
    const found = validateSchedule(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    setSubmitting(true);
    try {
      const session = await onSubmit(values);
      setCreated(session);
      window.setTimeout(() => successHeading.current?.focus(), 0);
    } catch (caught) {
      const list = conflictsOf(caught);
      if (list) {
        setConflicts(list);
      } else if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        const mapped = serverErrors(caught.fieldErrors);
        if (Object.keys(mapped).length > 0) {
          setErrors(mapped);
          focusFirst(mapped);
        } else {
          setFailure(caught);
        }
      } else {
        setFailure(caught);
      }
    } finally {
      setSubmitting(false);
    }
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    void submit().finally(() => {
      inFlight.current = false;
    });
  }

  function scheduleAnother() {
    setCreated(null);
    setErrors({});
    setValues({ ...EMPTY_SCHEDULE, date: values.date });
  }

  if (created) {
    const tutor = created.tutor;
    return (
      <section
        aria-labelledby={`${id}-success`}
        className="flex w-full max-w-2xl flex-col gap-4 rounded-md border border-border p-4"
      >
        <h2
          id={`${id}-success`}
          ref={successHeading}
          tabIndex={-1}
          className="text-lg font-semibold outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('scheduling.success.heading')}
        </h2>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">{t('scheduling.fields.student')}</dt>
          <dd>{created.participants.map((p) => p.display_name).join(', ')}</dd>
          <dt className="font-medium">{t('scheduling.fields.subject')}</dt>
          <dd>
            {t('scheduling.success.subject', {
              subject: created.subject.name,
              level: created.subject.education_level_name,
            })}
          </dd>
          <dt className="font-medium">{t('scheduling.success.when')}</dt>
          <dd>
            {t('scheduling.success.range', {
              start: formatInstant(created.starts_at, 'dateTime'),
              end: formatInstant(created.ends_at, 'time'),
            })}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.classroom')}</dt>
          <dd>{created.classroom?.name ?? t('scheduling.noClassroom')}</dd>
          <dt className="font-medium">{t('scheduling.success.state')}</dt>
          <dd>
            {tutor
              ? t('scheduling.success.offered', { tutor: tutor.display_name })
              : t('scheduling.success.unassigned')}
          </dd>
        </dl>
        <div>
          <Button type="button" onClick={scheduleAnother}>
            {t('scheduling.success.another')}
          </Button>
        </div>
      </section>
    );
  }

  const describedBy = (field: ScheduleField, extra?: string) =>
    [extra, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  const zoneId = `${id}-zone`;

  function select(
    field: 'subject' | 'tutor' | 'classroom',
    label: string,
    value: string,
    onChange: (value: string) => void,
    options: React.ReactNode,
  ) {
    return (
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId(field)} className="text-sm font-medium">
          {label}
        </label>
        <select
          id={fieldId(field)}
          className={SELECT_CLASS}
          value={value}
          aria-invalid={errors[field] ? true : undefined}
          aria-describedby={describedBy(field)}
          onChange={(event) => onChange(event.target.value)}
        >
          {options}
        </select>
        <FieldError id={errorId(field)} message={message(errors[field])} />
      </div>
    );
  }

  function timeField(field: 'date' | 'start' | 'end', label: string) {
    return (
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId(field)} className="text-sm font-medium">
          {label}
        </label>
        <Input
          id={fieldId(field)}
          type={field === 'date' ? 'date' : 'time'}
          step={field === 'date' ? undefined : 60}
          value={values[field]}
          aria-invalid={errors[field] ? true : undefined}
          aria-describedby={describedBy(
            field,
            field === 'date' ? undefined : zoneId,
          )}
          onChange={(event) => change(field, event.target.value)}
        />
        <FieldError id={errorId(field)} message={message(errors[field])} />
      </div>
    );
  }

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      {conflicts ? <ConflictPanel conflicts={conflicts} /> : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-6"
      >
        <div id={`${fieldId('student')}-picker`}>
          <SearchPicker<Option>
            label={t('scheduling.fields.student')}
            help={
              values.student
                ? t('scheduling.studentChosen', { name: values.student.name })
                : t('scheduling.studentHelp')
            }
            queryKey={['scheduling', 'create', 'students']}
            search={searchStudents}
            optionId={(student) => student.id}
            optionLabel={(student) => student.name}
            selectedId={values.student?.id ?? null}
            onSelect={(student) => change('student', student)}
            errorMessage={message(errors.student)}
          />
        </div>
        {select(
          'subject',
          t('scheduling.fields.subject'),
          values.subjectId,
          (value) => change('subjectId', value),
          <>
            <option value="">{t('scheduling.chooseSubject')}</option>
            {subjectsByLevel(subjects).map(([level, items]) => (
              <optgroup key={level} label={level}>
                {items.map((subject) => (
                  <option key={subject.id} value={subject.id}>
                    {subject.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </>,
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {select(
            'tutor',
            t('scheduling.fields.tutor'),
            values.tutorId,
            (value) => change('tutorId', value),
            <>
              <option value="">{t('scheduling.noTutor')}</option>
              {tutors.map((tutor) => (
                <option key={tutor.id} value={tutor.id}>
                  {tutor.name}
                </option>
              ))}
            </>,
          )}
          {select(
            'classroom',
            t('scheduling.fields.classroom'),
            values.classroomId,
            (value) => change('classroomId', value),
            <>
              <option value="">{t('scheduling.noClassroom')}</option>
              {classrooms.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.name}
                </option>
              ))}
            </>,
          )}
        </div>
        <fieldset className="flex flex-col gap-3">
          <legend className="text-sm font-medium">
            {t('scheduling.fields.when')}
          </legend>
          <p id={zoneId} className="text-sm text-muted-foreground">
            {t('scheduling.zone', { zone: timeZone })}
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {timeField('date', t('scheduling.fields.date'))}
            {timeField('start', t('scheduling.fields.start'))}
            {timeField('end', t('scheduling.fields.end'))}
          </div>
        </fieldset>
        <div>
          <Button type="submit" disabled={submitting}>
            {submitting ? <Spinner /> : null}
            {submitting ? t('scheduling.submitting') : t('scheduling.submit')}
          </Button>
        </div>
      </form>
    </div>
  );
}
