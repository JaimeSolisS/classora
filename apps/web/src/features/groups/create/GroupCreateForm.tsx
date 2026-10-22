import { X } from 'lucide-react';
import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import {
  conflictsOf,
  type Option,
  type SchedulingConflict,
  type SubjectOption,
} from '@/features/scheduling/create/api';
import { subjectsByLevel } from '@/features/scheduling/create/rules';
import { useFieldMessage } from '@/features/scheduling/series/hooks';
import {
  EMPTY_RECURRENCE,
  RECURRENCE_FIELD_ORDER,
  recurrenceFieldId,
  weekdaysFor,
  type RecurrenceErrors,
  type RecurrenceValues,
} from '@/features/scheduling/series/recurrence';
import { RecurrenceFields } from '@/features/scheduling/series/RecurrenceFields';
import { SeriesOverrideSection } from '@/features/scheduling/series/SeriesOverrideSection';
import { SELECT_CLASS } from '@/features/students/styles';
import type {
  ClassroomCapacityOption,
  GroupCreated,
  GroupOverride,
} from '../api';
import {
  CREATE_FIELD_ORDER,
  createAccepts,
  createServerErrors,
  EMPTY_GROUP,
  groupCreateOverride,
  hasServerErrors,
  MAX_STUDENTS,
  NAME_MAX_LENGTH,
  validateGroupCreate,
  type CreateErrors,
  type CreateField,
  type GroupCreateValues,
  type GroupMessage,
} from '../rules';

// The "New group" form (#234 E), without any API call: the page passes the
// options, the student search and `onSubmit` (which resolves with the
// created group or rejects with the error to show). The name, the subject
// (grouped by level), the maximum, the tutor and the classroom (with its
// capacity), "Starts on" and the times in the center zone (shown), the #235
// weekly controls, and the initial students as removable chips (no
// duplicates). The body has the structured fields only (never RRULE text).
//
// A 409 scheduling_conflict shows the date-grouped panel (the group's own
// conflict first, "Whole group"). A schedule.override holder with only
// soft conflicts that POST /groups accepts (E3: `codes` for
// group_capacity_exceeded, `pairs` for the occurrences', the merged #56 D2
// shape) gets "Override and create"; any other soft code says it can't be
// overridden for groups yet. group_name_taken and the 422s go on their
// fields (a student's on its chip); anything else is an ErrorAlert. Nothing
// is lost from the form on any error.

export interface GroupCreateFormProps {
  subjects: readonly SubjectOption[];
  tutors: readonly Option[];
  classrooms: readonly ClassroomCapacityOption[];
  searchStudents: (q: string, signal: AbortSignal) => Promise<Option[]>;
  timeZone: string;
  canOverride: boolean;
  onSubmit: (
    values: GroupCreateValues,
    recurrence: RecurrenceValues,
    override?: GroupOverride,
  ) => Promise<GroupCreated>;
  /** After a 201 (the page navigates to the group). */
  onCreated: (group: GroupCreated) => void;
  /** The id of the page heading that names the form. */
  labelledBy: string;
  /** Stories: values, a rule and an outcome to start from. */
  initialValues?: GroupCreateValues;
  initialRecurrence?: RecurrenceValues;
  initialConflicts?: SchedulingConflict[];
  initialStudentErrors?: Record<number, GroupMessage>;
}

export function GroupCreateForm({
  subjects,
  tutors,
  classrooms,
  searchStudents,
  timeZone,
  canOverride,
  onSubmit,
  onCreated,
  labelledBy,
  initialValues = EMPTY_GROUP,
  initialRecurrence = EMPTY_RECURRENCE,
  initialConflicts,
  initialStudentErrors = {},
}: GroupCreateFormProps) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const id = useId();
  const [values, setValues] = useState<GroupCreateValues>(initialValues);
  const [errors, setErrors] = useState<CreateErrors>({});
  const [studentErrors, setStudentErrors] =
    useState<Record<number, GroupMessage>>(initialStudentErrors);
  const [recurrence, setRecurrence] =
    useState<RecurrenceValues>(initialRecurrence);
  const [recurrenceErrors, setRecurrenceErrors] = useState<RecurrenceErrors>(
    {},
  );
  const weekdaysTouched = useRef(initialRecurrence.weekdays.length > 0);
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(
    initialConflicts ?? null,
  );
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const inFlight = useRef(false);

  const fieldId = (field: CreateField) => `${id}-${field}`;
  const errorId = (field: CreateField) => `${id}-${field}-error`;
  const zoneId = `${id}-zone`;
  const recurrencePrefix = `${id}-recurrence`;

  function change<K extends keyof GroupCreateValues>(
    key: K,
    value: GroupCreateValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    if (key === 'startsOn' && !weekdaysTouched.current) {
      setRecurrence((current) => ({
        ...current,
        weekdays: weekdaysFor(value as string),
      }));
    }
  }

  function changeRecurrence(next: RecurrenceValues) {
    if (next.weekdays !== recurrence.weekdays) {
      weekdaysTouched.current = true;
    }
    setRecurrence(next);
  }

  function addStudent(student: Option) {
    if (values.students.some((s) => s.id === student.id)) {
      return;
    }
    setStudentErrors({});
    change('students', [...values.students, student]);
  }

  function removeStudent(index: number) {
    setStudentErrors({});
    change(
      'students',
      values.students.filter((_, position) => position !== index),
    );
    // The chip is gone: back to the search.
    window.setTimeout(
      () =>
        document
          .getElementById(`${fieldId('students')}-picker`)
          ?.querySelector('input')
          ?.focus(),
      0,
    );
  }

  function focusFirst(found: CreateErrors, rule: RecurrenceErrors) {
    const first = CREATE_FIELD_ORDER.find((field) => found[field]);
    if (first === 'students') {
      document
        .getElementById(`${fieldId('students')}-picker`)
        ?.querySelector('input')
        ?.focus();
      return;
    }
    if (first) {
      document.getElementById(fieldId(first))?.focus();
      return;
    }
    const ruleField = RECURRENCE_FIELD_ORDER.find((field) => rule[field]);
    if (ruleField) {
      document
        .getElementById(recurrenceFieldId(recurrencePrefix, ruleField))
        ?.focus();
    }
  }

  function handleFailure(caught: unknown) {
    const list = conflictsOf(caught);
    if (list) {
      setConflicts(list);
      return;
    }
    setConflicts(null);
    if (caught instanceof ApiError) {
      const found = createServerErrors(caught);
      if (hasServerErrors(found)) {
        setErrors(found.fields);
        setRecurrenceErrors(found.recurrence);
        setStudentErrors(found.students);
        const firstChip = Object.keys(found.students)[0];
        if (Object.keys(found.fields).length === 0 && firstChip !== undefined) {
          document.getElementById(`${id}-chip-${firstChip}`)?.focus();
        } else {
          focusFirst(found.fields, found.recurrence);
        }
        return;
      }
    }
    setFailure(caught);
  }

  async function submit() {
    setFailure(null);
    setConflicts(null);
    setStudentErrors({});
    const found = validateGroupCreate(values, recurrence);
    setErrors(found.errors);
    setRecurrenceErrors(found.recurrence);
    if (
      Object.keys(found.errors).length > 0 ||
      Object.keys(found.recurrence).length > 0
    ) {
      focusFirst(found.errors, found.recurrence);
      return;
    }
    setSubmitting(true);
    try {
      onCreated(await onSubmit(values, recurrence));
    } catch (caught) {
      handleFailure(caught);
    } finally {
      setSubmitting(false);
    }
  }

  async function override(reason: string) {
    if (!conflicts) {
      return;
    }
    onCreated(
      await onSubmit(
        values,
        recurrence,
        groupCreateOverride(conflicts, reason),
      ),
    );
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

  const describedBy = (field: CreateField, extra?: string) =>
    [extra, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;

  function field(
    name: CreateField,
    label: string,
    control: ReactNode,
    help?: ReactNode,
  ) {
    return (
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId(name)} className="text-sm font-medium">
          {label}
        </label>
        {control}
        {help}
        <FieldError id={errorId(name)} message={message(errors[name])} />
      </div>
    );
  }

  function select(
    name: 'subject' | 'tutor' | 'classroom',
    value: string,
    onChange: (value: string) => void,
    options: ReactNode,
  ) {
    return (
      <select
        id={fieldId(name)}
        className={SELECT_CLASS}
        value={value}
        aria-invalid={errors[name] ? true : undefined}
        aria-describedby={describedBy(name)}
        onChange={(event) => onChange(event.target.value)}
      >
        {options}
      </select>
    );
  }

  function timeInput(name: 'startsOn' | 'start' | 'end') {
    const key = name;
    return (
      <Input
        id={fieldId(name)}
        type={name === 'startsOn' ? 'date' : 'time'}
        step={name === 'startsOn' ? undefined : 60}
        value={values[key]}
        aria-invalid={errors[name] ? true : undefined}
        aria-describedby={describedBy(
          name,
          name === 'startsOn' ? undefined : zoneId,
        )}
        onChange={(event) => change(key, event.target.value)}
      />
    );
  }

  const pickedIds = new Set(values.students.map((s) => s.id));

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      {conflicts ? (
        <SeriesOverrideSection
          conflicts={conflicts}
          heading={t('groups.create.conflictsHeading', {
            count: conflicts.length,
          })}
          canOverride={canOverride}
          accepts={createAccepts}
          onOverride={override}
          onRefused={handleFailure}
          labels={{
            action: t('groups.create.overrideAction'),
            submitting: t('groups.create.submitting'),
            description: t('groups.create.overrideDescription'),
            noDateHeading: t('groups.conflicts.wholeGroup'),
            noOverrideLabel: t('groups.conflicts.notYetLabel'),
            notYet: t('groups.conflicts.notYet'),
          }}
        />
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-6"
      >
        {field(
          'name',
          t('groups.fields.name'),
          <Input
            id={fieldId('name')}
            value={values.name}
            maxLength={NAME_MAX_LENGTH + 20}
            aria-invalid={errors.name ? true : undefined}
            aria-describedby={describedBy('name')}
            onChange={(event) => change('name', event.target.value)}
          />,
        )}
        {field(
          'subject',
          t('scheduling.fields.subject'),
          select(
            'subject',
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
          ),
        )}
        <div className="sm:max-w-xs">
          {field(
            'max',
            t('groups.fields.max'),
            <Input
              id={fieldId('max')}
              type="number"
              inputMode="numeric"
              min={1}
              max={MAX_STUDENTS}
              step={1}
              value={values.max}
              aria-invalid={errors.max ? true : undefined}
              aria-describedby={describedBy('max')}
              onChange={(event) => change('max', event.target.value)}
            />,
          )}
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {field(
            'tutor',
            t('scheduling.fields.tutor'),
            select(
              'tutor',
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
            ),
          )}
          {field(
            'classroom',
            t('scheduling.fields.classroom'),
            select(
              'classroom',
              values.classroomId,
              (value) => change('classroomId', value),
              <>
                <option value="">{t('scheduling.noClassroom')}</option>
                {classrooms.map((room) => (
                  <option key={room.id} value={room.id}>
                    {t('groups.create.roomOption', {
                      name: room.name,
                      capacity: room.capacity,
                    })}
                  </option>
                ))}
              </>,
            ),
          )}
        </div>
        <fieldset className="flex flex-col gap-3">
          <legend className="text-sm font-medium">
            {t('groups.create.schedule')}
          </legend>
          <p id={zoneId} className="text-sm text-muted-foreground">
            {t('scheduling.zone', { zone: timeZone })}
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {field(
              'startsOn',
              t('scheduling.series.startsOn'),
              timeInput('startsOn'),
            )}
            {field('start', t('scheduling.fields.start'), timeInput('start'))}
            {field('end', t('scheduling.fields.end'), timeInput('end'))}
          </div>
          <RecurrenceFields
            idPrefix={recurrencePrefix}
            values={recurrence}
            errors={recurrenceErrors}
            onChange={changeRecurrence}
          />
        </fieldset>
        <fieldset
          className="flex min-w-0 flex-col gap-3"
          aria-describedby={errors.students ? errorId('students') : undefined}
        >
          <legend className="text-sm font-medium">
            {t('groups.create.students')}
          </legend>
          <div id={`${fieldId('students')}-picker`}>
            <SearchPicker<Option>
              label={t('groups.create.addStudent')}
              help={t('groups.create.studentsHelp')}
              queryKey={['groups', 'create', 'students']}
              search={searchStudents}
              optionId={(student) => student.id}
              optionLabel={(student) =>
                pickedIds.has(student.id)
                  ? t('groups.create.alreadyAdded', { name: student.name })
                  : student.name
              }
              optionDisabled={(student) => pickedIds.has(student.id)}
              selectedId={null}
              onSelect={addStudent}
            />
          </div>
          {values.students.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t('groups.create.noStudents')}
            </p>
          ) : (
            <ul
              aria-label={t('groups.create.chosen', {
                count: values.students.length,
              })}
              className="flex flex-wrap gap-2"
            >
              {values.students.map((student, index) => {
                const chipError = studentErrors[index];
                return (
                  <li key={student.id} className="flex flex-col gap-1">
                    <span
                      className={
                        chipError
                          ? 'inline-flex items-center gap-1 rounded-md border border-destructive py-0.5 pr-0.5 pl-2 text-sm'
                          : 'inline-flex items-center gap-1 rounded-md border border-border py-0.5 pr-0.5 pl-2 text-sm'
                      }
                    >
                      <span className="break-words">{student.name}</span>
                      <Button
                        id={`${id}-chip-${index}`}
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t('groups.create.remove', {
                          name: student.name,
                        })}
                        aria-describedby={
                          chipError ? `${id}-chip-${index}-error` : undefined
                        }
                        onClick={() => removeStudent(index)}
                      >
                        <X aria-hidden="true" />
                      </Button>
                    </span>
                    <FieldError
                      id={`${id}-chip-${index}-error`}
                      message={message(chipError)}
                    />
                  </li>
                );
              })}
            </ul>
          )}
          <FieldError
            id={errorId('students')}
            message={message(errors.students)}
          />
        </fieldset>
        <div>
          <Button type="submit" disabled={submitting}>
            {submitting ? <Spinner /> : null}
            {submitting
              ? t('groups.create.submitting')
              : t('groups.create.submit')}
          </Button>
        </div>
      </form>
    </div>
  );
}
