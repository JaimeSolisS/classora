import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import type { ClassroomCapacityOption } from '@/features/groups/api';
import {
  conflictsOf,
  type Option,
  type SchedulingConflict,
} from '@/features/scheduling/create/api';
import { subjectsByLevel } from '@/features/scheduling/create/rules';
import { describeRecurrence } from '@/features/scheduling/series/describeRecurrence';
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
import { toLanguage } from '@/i18n/language';
import type { CohortCreated, CohortOverride, TemplateOption } from '../api';
import {
  cohortCreateOverride,
  cohortRule,
  createAccepts,
  createServerErrors,
  CREATE_FIELD_ORDER,
  EMPTY_COHORT,
  hasServerErrors,
  MAX_STUDENTS,
  NAME_MAX_LENGTH,
  templateSubjects,
  validateCohortCreate,
  withTemplate,
  type CohortCreateValues,
  type CreateErrors,
  type CreateField,
} from '../rules';

// The "New cohort" form (#243 C), without any API call: the page passes
// the options and `onSubmit` (which resolves with the created cohort or
// rejects with the error to show). The name; an active course template,
// whose sessions show read-only ("10 sessions of 90 min") and whose
// subjects alone fill the subject select (its only one preselected); the
// maximum; the classroom (with its capacity); the tutor (the offer covers
// the whole course) and, with a tutor, an optional "Respond by" in the
// center zone; "Starts on", the start time and #235's weekly controls
// without "Ends" (the server derives the count around the known closures)
// with the rule summary. The body has the structured fields only.
//
// A 409 scheduling_conflict shows #235's date-grouped panel; a holder of
// schedule.override with only soft conflicts of single occurrences gets
// #236's override dialog (POST /cohorts takes the series `pairs`), any
// other soft one says it can't be overridden for cohorts yet.
// cohort_name_taken and the 422s go on their fields; anything else is an
// ErrorAlert. Nothing typed is lost on any error.

export interface CohortCreateFormProps {
  templates: readonly TemplateOption[];
  tutors: readonly Option[];
  classrooms: readonly ClassroomCapacityOption[];
  timeZone: string;
  canOverride: boolean;
  onSubmit: (
    values: CohortCreateValues,
    recurrence: RecurrenceValues,
    override?: CohortOverride,
  ) => Promise<CohortCreated>;
  /** After a 201 (the page navigates to the cohort). */
  onCreated: (cohort: CohortCreated) => void;
  /** The id of the page heading that names the form. */
  labelledBy: string;
  /** Stories: values, a rule and an outcome to start from. */
  initialValues?: CohortCreateValues;
  initialRecurrence?: RecurrenceValues;
  initialConflicts?: SchedulingConflict[];
  initialErrors?: CreateErrors;
  initialRecurrenceErrors?: RecurrenceErrors;
}

export function CohortCreateForm({
  templates,
  tutors,
  classrooms,
  timeZone,
  canOverride,
  onSubmit,
  onCreated,
  labelledBy,
  initialValues = EMPTY_COHORT,
  initialRecurrence = EMPTY_RECURRENCE,
  initialConflicts,
  initialErrors = {},
  initialRecurrenceErrors = {},
}: CohortCreateFormProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const message = useFieldMessage();
  const id = useId();
  const [values, setValues] = useState<CohortCreateValues>(initialValues);
  const [errors, setErrors] = useState<CreateErrors>(initialErrors);
  const [recurrence, setRecurrence] =
    useState<RecurrenceValues>(initialRecurrence);
  const [recurrenceErrors, setRecurrenceErrors] = useState<RecurrenceErrors>(
    initialRecurrenceErrors,
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
  const sessionsId = `${id}-sessions`;
  const tutorHelpId = `${id}-tutor-help`;
  const recurrencePrefix = `${id}-recurrence`;
  const template = templates.find((item) => item.id === values.templateId);
  const subjects = templateSubjects(template);

  function change<K extends keyof CohortCreateValues>(
    key: K,
    value: CohortCreateValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    if (key === 'startsOn' && !weekdaysTouched.current) {
      setRecurrence((current) => ({
        ...current,
        weekdays: weekdaysFor(value),
      }));
    }
  }

  function changeRecurrence(next: RecurrenceValues) {
    if (next.weekdays !== recurrence.weekdays) {
      weekdaysTouched.current = true;
    }
    setRecurrence(next);
  }

  function focusFirst(found: CreateErrors, rule: RecurrenceErrors) {
    const first = CREATE_FIELD_ORDER.find((field) => found[field]);
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
        focusFirst(found.fields, found.recurrence);
        return;
      }
    }
    setFailure(caught);
  }

  async function submit() {
    setFailure(null);
    setConflicts(null);
    const found = validateCohortCreate(values, recurrence);
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
        cohortCreateOverride(conflicts, reason),
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

  const describedBy = (field: CreateField, ...extra: (string | undefined)[]) =>
    [...extra, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;

  function field(
    name: CreateField,
    label: string,
    control: ReactNode,
    help?: ReactNode,
  ) {
    return (
      <div className="flex min-w-0 flex-col gap-2">
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
    name: 'template' | 'subject' | 'tutor' | 'classroom',
    value: string,
    onChange: (value: string) => void,
    options: ReactNode,
    extra?: { describedBy?: string; disabled?: boolean },
  ) {
    return (
      <select
        id={fieldId(name)}
        className={SELECT_CLASS}
        value={value}
        disabled={extra?.disabled}
        aria-invalid={errors[name] ? true : undefined}
        aria-describedby={describedBy(name, extra?.describedBy)}
        onChange={(event) => onChange(event.target.value)}
      >
        {options}
      </select>
    );
  }

  const summary =
    template && recurrence.weekdays.length > 0
      ? describeRecurrence(
          cohortRule(recurrence, template.session_count),
          language,
        )
      : null;

  return (
    <div className="flex w-full max-w-2xl min-w-0 flex-col gap-6">
      {conflicts ? (
        <SeriesOverrideSection
          conflicts={conflicts}
          heading={t('courses.cohorts.create.conflictsHeading', {
            count: conflicts.length,
          })}
          canOverride={canOverride}
          accepts={createAccepts}
          onOverride={override}
          onRefused={handleFailure}
          labels={{
            action: t('courses.cohorts.create.overrideAction'),
            submitting: t('courses.cohorts.create.submitting'),
            description: t('courses.cohorts.create.overrideDescription'),
            noDateHeading: t('courses.cohorts.create.wholeCohort'),
            noOverrideLabel: t('courses.cohorts.create.notYetLabel'),
            notYet: t('courses.cohorts.create.notYet'),
          }}
        />
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex min-w-0 flex-col gap-6"
      >
        {field(
          'name',
          t('courses.cohorts.fields.name'),
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
          'template',
          t('courses.cohorts.fields.template'),
          select(
            'template',
            values.templateId,
            (value) =>
              setValues((current) =>
                withTemplate(
                  current,
                  templates.find((item) => item.id === value),
                ),
              ),
            <>
              <option value="">
                {t('courses.cohorts.create.chooseTemplate')}
              </option>
              {templates.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </>,
            { describedBy: template ? sessionsId : undefined },
          ),
          templates.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t('courses.cohorts.create.noTemplates')}
            </p>
          ) : template ? (
            <p id={sessionsId} className="text-sm">
              {t('courses.cohorts.create.templateSessions', {
                count: template.session_count,
                minutes: template.session_minutes,
              })}
            </p>
          ) : null,
        )}
        {field(
          'subject',
          t('scheduling.fields.subject'),
          select(
            'subject',
            values.subjectId,
            (value) => change('subjectId', value),
            <>
              <option value="">
                {template
                  ? t('scheduling.chooseSubject')
                  : t('courses.cohorts.create.chooseTemplateFirst')}
              </option>
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
            { disabled: !template },
          ),
        )}
        <div className="sm:max-w-xs">
          {field(
            'max',
            t('courses.cohorts.fields.max'),
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
                    {t('courses.cohorts.create.roomOption', {
                      name: room.name,
                      capacity: room.capacity,
                    })}
                  </option>
                ))}
              </>,
            ),
          )}
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
              { describedBy: tutorHelpId },
            ),
            <p id={tutorHelpId} className="text-sm text-muted-foreground">
              {t('courses.cohorts.create.tutorHelp')}
            </p>,
          )}
        </div>
        {values.tutorId ? (
          <fieldset
            className="flex min-w-0 flex-col gap-3"
            aria-describedby={
              errors.respondBy ? errorId('respondBy') : undefined
            }
          >
            <legend className="text-sm font-medium">
              {t('courses.cohorts.create.respondBy')}
            </legend>
            <p
              id={`${id}-respond-help`}
              className="text-sm text-muted-foreground"
            >
              {t('scheduling.offer.deadlineHelp', { zone: timeZone })}
            </p>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex min-w-0 flex-col gap-2">
                <label
                  htmlFor={fieldId('respondBy')}
                  className="text-sm font-medium"
                >
                  {t('scheduling.offer.deadlineDate')}
                </label>
                <Input
                  id={fieldId('respondBy')}
                  type="date"
                  value={values.respondByDate}
                  aria-invalid={errors.respondBy ? true : undefined}
                  aria-describedby={describedBy(
                    'respondBy',
                    `${id}-respond-help`,
                  )}
                  onChange={(event) =>
                    change('respondByDate', event.target.value)
                  }
                />
              </div>
              <div className="flex min-w-0 flex-col gap-2">
                <label
                  htmlFor={`${fieldId('respondBy')}-time`}
                  className="text-sm font-medium"
                >
                  {t('scheduling.offer.deadlineTime')}
                </label>
                <Input
                  id={`${fieldId('respondBy')}-time`}
                  type="time"
                  step={60}
                  value={values.respondByTime}
                  aria-invalid={errors.respondBy ? true : undefined}
                  aria-describedby={describedBy(
                    'respondBy',
                    `${id}-respond-help`,
                  )}
                  onChange={(event) =>
                    change('respondByTime', event.target.value)
                  }
                />
              </div>
            </div>
            <FieldError
              id={errorId('respondBy')}
              message={message(errors.respondBy)}
            />
          </fieldset>
        ) : null}
        <fieldset className="flex min-w-0 flex-col gap-3">
          <legend className="text-sm font-medium">
            {t('courses.cohorts.create.schedule')}
          </legend>
          <p id={zoneId} className="text-sm text-muted-foreground">
            {t('scheduling.zone', { zone: timeZone })}
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {field(
              'startsOn',
              t('scheduling.series.startsOn'),
              <Input
                id={fieldId('startsOn')}
                type="date"
                value={values.startsOn}
                aria-invalid={errors.startsOn ? true : undefined}
                aria-describedby={describedBy('startsOn')}
                onChange={(event) => change('startsOn', event.target.value)}
              />,
            )}
            {field(
              'start',
              t('scheduling.fields.start'),
              <Input
                id={fieldId('start')}
                type="time"
                step={60}
                value={values.start}
                aria-invalid={errors.start ? true : undefined}
                aria-describedby={describedBy('start', zoneId)}
                onChange={(event) => change('start', event.target.value)}
              />,
            )}
          </div>
          <RecurrenceFields
            idPrefix={recurrencePrefix}
            values={recurrence}
            errors={recurrenceErrors}
            onChange={changeRecurrence}
            showEnds={false}
          />
          {summary ? (
            <p className="text-sm break-words">
              <span className="font-medium">
                {t('courses.cohorts.create.summary')}:{' '}
              </span>
              {summary}
            </p>
          ) : null}
          <p className="text-sm text-muted-foreground">
            {t('courses.cohorts.create.help')}
          </p>
        </fieldset>
        <div>
          <Button type="submit" disabled={submitting}>
            {submitting ? <Spinner /> : null}
            {submitting
              ? t('courses.cohorts.create.submitting')
              : t('courses.cohorts.create.submit')}
          </Button>
        </div>
      </form>
    </div>
  );
}
