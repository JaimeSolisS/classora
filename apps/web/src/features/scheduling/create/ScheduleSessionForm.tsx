import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
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
import { toLanguage } from '@/i18n/language';
import type { ClassSeriesCreated } from '../series/api';
import { describeRecurrence } from '../series/describeRecurrence';
import {
  EMPTY_RECURRENCE,
  RECURRENCE_FIELD_ORDER,
  recurrenceFieldId,
  recurrenceServerErrors,
  validateRecurrence,
  weekdaysFor,
  type RecurrenceErrors,
  type RecurrenceValues,
} from '../series/recurrence';
import { RecurrenceFields } from '../series/RecurrenceFields';
import {
  overriddenCount,
  seriesOverride,
  type SeriesOverride,
} from '../series/overrides';
import { SeriesOverrideSection } from '../series/SeriesOverrideSection';
import {
  conflictsOf,
  type ClassSessionCreated,
  type Option,
  type SchedulingConflict,
  type SlotSuggestions,
  type SubjectOption,
  type SuggestedSlot,
  type TutorSuggestion,
  type TutorSuggestionList,
} from './api';
import { ConflictPanel } from './ConflictPanel';
import { OverrideDialog } from './OverrideDialog';
import {
  canSuggestTutors,
  EMPTY_SCHEDULE,
  FIELD_ORDER,
  isConflictCode,
  overrideAction,
  overrideReasonError,
  serverErrors,
  sessionOverride,
  SLOT_FIELDS,
  slotValues,
  subjectsByLevel,
  validateSchedule,
  type ScheduleErrors,
  type ScheduleField,
  type ScheduleMessage,
  type ScheduleValues,
  type SessionOverride,
} from './rules';
import { useSlotParts } from './slotParts';
import { SuggestedSlots, type SuggestionsState } from './SuggestedSlots';
import {
  TutorSuggestions,
  type TutorSuggestionsState,
} from './TutorSuggestions';
import { useWarningMessage } from './warningMessages';

// The "Schedule a session" form (#48 F2, F3), without any API call: the
// page passes the options, the student search and `onSubmit` (which
// resolves with the created session or rejects with the error to show).
// Times are local to the center zone, which is shown next to them. A 201
// shows the success panel ("Schedule another" keeps the date); a 409
// scheduling_conflict shows the conflict panel and keeps the form; a 422
// puts the field errors on their fields; anything else is an ErrorAlert.
//
// Overrides (#49 F2-F4): under the panel, a holder of schedule.override
// (`canOverride`) gets "Override and schedule" when every conflict is soft,
// or "fix the conflicts that aren't allowed first"; anyone else reads who
// can override. The dialog resends the same values with every soft code and
// the reason: a 201 shows the success panel with the overridden rules, a new
// 409 closes the dialog and shows the new list (focus on its heading), a
// 422 on the reason stays in the dialog, and a 403 shows its ErrorAlert.
//
// Suggested times (#50 C1): after a 409 the form asks `onSuggest` for free
// times with the same values and shows them under the panel. Choosing one
// fills in the date, start and end (it doesn't submit), closes the panel,
// moves the focus to the submit button and announces the new time in a
// polite live region. The success panel lists the 201's workload warnings
// (#50 C2).
//
// Suggested tutors (#51 G): with `onSuggestTutors` (the page passes it only
// to holders of schedule.assign) a "Suggest tutors" button sits under the
// Tutor picker, enabled once the subject, date, start and end are valid. The
// panel lists the eligible tutors; "Choose" sets the Tutor picker (nothing
// is submitted), closes the panel, moves the focus to the picker and
// announces the tutor. Changing the subject, classroom, date or times
// clears a shown panel, so stale suggestions are never applied. A 422 goes
// onto the form's fields.
//
// Repeats weekly (#54b A1-A4): with `onSubmitSeries` a switch (off by
// default, so the one-off flow is unchanged) turns the form into a weekly
// series: the date becomes "Starts on", and the weekdays (pre-checked with
// the start date's weekday until the user changes them), "Every N weeks"
// and "Ends" appear. A 201 shows the series success panel (the rule
// summary, the sessions created so far, how many are skipped for a
// closure, the series link); a 409 shows every occurrence's conflicts
// grouped by local date, without suggested times; a 422 goes onto the
// fields, `recurrence.*` included. Under that panel the #49 branches apply
// to the series (#236 H1, H2): "Override and schedule" resends the series
// with every soft (occurrence, code) pair and the reason, and the success
// panel says on how many occurrences it was overridden.

export interface ScheduleSessionFormProps {
  subjects: readonly SubjectOption[];
  tutors: readonly Option[];
  classrooms: readonly Option[];
  searchStudents: (q: string, signal: AbortSignal) => Promise<Option[]>;
  timeZone: string;
  /** Creates the session (with the override when given); rejects with the error. */
  onSubmit: (
    values: ScheduleValues,
    override?: SessionOverride,
  ) => Promise<ClassSessionCreated>;
  /** Whether the user holds schedule.override. */
  canOverride: boolean;
  /** Free times for the same values after a conflict (#50); none: no section. */
  onSuggest?: (values: ScheduleValues) => Promise<SlotSuggestions>;
  /** The eligible tutors for the slot (#51; schedule.assign); none: no button. */
  onSuggestTutors?: (values: ScheduleValues) => Promise<TutorSuggestionList>;
  /** The values to start from (stories). */
  initialValues?: ScheduleValues;
  /** A link to the created session's page (#52 H2; the page passes it). */
  sessionLink?: (sessionId: string) => ReactNode;
  /** The id of the page heading that names the form. */
  labelledBy: string;
  /** Creates a weekly series (#54b); none: no "Repeats weekly" switch. */
  onSubmitSeries?: (
    values: ScheduleValues,
    recurrence: RecurrenceValues,
    override?: SeriesOverride,
  ) => Promise<ClassSeriesCreated>;
  /** A link to the created series' page (#54b D1). */
  seriesLink?: (seriesId: string) => ReactNode;
  /** Whether "Repeats weekly" starts on (stories). */
  initialRepeats?: boolean;
  /** The rule to start from (stories). */
  initialRecurrence?: RecurrenceValues;
}

export function ScheduleSessionForm({
  subjects,
  tutors,
  classrooms,
  searchStudents,
  timeZone,
  onSubmit,
  canOverride,
  onSuggest,
  onSuggestTutors,
  initialValues = EMPTY_SCHEDULE,
  labelledBy,
  sessionLink,
  onSubmitSeries,
  seriesLink,
  initialRepeats = false,
  initialRecurrence = EMPTY_RECURRENCE,
}: ScheduleSessionFormProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const { t: tErrors } = useTranslation('errors');
  const { formatInstant } = useFormatters();
  const id = useId();
  const [values, setValues] = useState<ScheduleValues>(initialValues);
  const [errors, setErrors] = useState<ScheduleErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [created, setCreated] = useState<ClassSessionCreated | null>(null);
  const [repeats, setRepeats] = useState(
    initialRepeats && onSubmitSeries !== undefined,
  );
  const [recurrence, setRecurrence] =
    useState<RecurrenceValues>(initialRecurrence);
  const [recurrenceErrors, setRecurrenceErrors] = useState<RecurrenceErrors>(
    {},
  );
  // Until the user changes the weekdays, they follow the start date.
  const weekdaysTouched = useRef(initialRecurrence.weekdays.length > 0);
  const [seriesConflicts, setSeriesConflicts] = useState<
    SchedulingConflict[] | null
  >(null);
  const [createdSeries, setCreatedSeries] = useState<ClassSeriesCreated | null>(
    null,
  );
  const [overriding, setOverriding] = useState(false);
  const [suggestions, setSuggestions] = useState<SuggestionsState | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const suggestionRequest = useRef(0);
  const [tutorSuggestions, setTutorSuggestions] =
    useState<TutorSuggestionsState | null>(null);
  const tutorRequest = useRef(0);
  const submitButton = useRef<HTMLButtonElement>(null);
  const warningMessage = useWarningMessage();
  const slotParts = useSlotParts(timeZone);
  const inFlight = useRef(false);
  const successHeading = useRef<HTMLHeadingElement>(null);
  const overrideButton = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  // Where the focus goes once the dialog closes: back to its button
  // (cancel), or to the new conflict list (a new 409) or the success heading.
  const focusAfterDialog = useRef<'button' | 'panel'>('button');

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
    if (SLOT_FIELDS.has(key)) {
      clearTutorSuggestions();
    }
    if (key === 'date' && !weekdaysTouched.current) {
      setRecurrence((current) => ({
        ...current,
        weekdays: weekdaysFor(value as string),
      }));
    }
  }

  function toggleRepeats(on: boolean) {
    setRepeats(on);
    setConflicts(null);
    setSeriesConflicts(null);
    clearSuggestions();
    setFailure(null);
    setRecurrenceErrors({});
    if (on && !weekdaysTouched.current) {
      setRecurrence((current) => ({
        ...current,
        weekdays: weekdaysFor(values.date),
      }));
    }
  }

  function changeRecurrence(next: RecurrenceValues) {
    if (next.weekdays !== recurrence.weekdays) {
      weekdaysTouched.current = true;
    }
    setRecurrence(next);
  }

  function focusRecurrence(found: RecurrenceErrors) {
    const first = RECURRENCE_FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document
        .getElementById(recurrenceFieldId(`${id}-recurrence`, first))
        ?.focus();
    }
  }

  function clearTutorSuggestions() {
    // A later answer of an earlier request is ignored.
    tutorRequest.current += 1;
    setTutorSuggestions(null);
  }

  function suggestTutors() {
    if (!onSuggestTutors || !canSuggestTutors(values)) {
      return;
    }
    tutorRequest.current += 1;
    const request = tutorRequest.current;
    setTutorSuggestions({ status: 'loading' });
    onSuggestTutors(values).then(
      (result) => {
        if (request === tutorRequest.current) {
          setTutorSuggestions({ status: 'loaded', result });
        }
      },
      (error: unknown) => {
        if (request !== tutorRequest.current) {
          return;
        }
        const mapped =
          error instanceof ApiError ? serverErrors(error.fieldErrors) : {};
        if (Object.keys(mapped).length > 0) {
          setTutorSuggestions(null);
          setErrors(mapped);
          focusFirst(mapped);
        } else {
          setTutorSuggestions({ status: 'error', error });
        }
      },
    );
  }

  function chooseTutor(suggestion: TutorSuggestion) {
    setValues((current) => ({ ...current, tutorId: suggestion.tutor.id }));
    clearTutorSuggestions();
    setAnnouncement(
      t('scheduling.tutorSuggestions.chosen', {
        name: suggestion.tutor.display_name,
      }),
    );
    window.setTimeout(
      () => document.getElementById(fieldId('tutor'))?.focus(),
      0,
    );
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

  function clearSuggestions() {
    // A later answer of an earlier request is ignored.
    suggestionRequest.current += 1;
    setSuggestions(null);
  }

  function suggest(forValues: ScheduleValues) {
    if (!onSuggest) {
      return;
    }
    suggestionRequest.current += 1;
    const request = suggestionRequest.current;
    setSuggestions({ status: 'loading' });
    onSuggest(forValues).then(
      (result) => {
        if (request === suggestionRequest.current) {
          setSuggestions({ status: 'loaded', result });
        }
      },
      (error: unknown) => {
        if (request === suggestionRequest.current) {
          setSuggestions({ status: 'error', error });
        }
      },
    );
  }

  function chooseSlot(slot: SuggestedSlot) {
    setValues((current) => ({ ...current, ...slotValues(slot, timeZone) }));
    setErrors({});
    setConflicts(null);
    clearSuggestions();
    setAnnouncement(t('scheduling.suggestions.chosen', slotParts(slot)));
    window.setTimeout(() => submitButton.current?.focus(), 0);
  }

  async function submitSeries(
    submitSeriesFn: NonNullable<typeof onSubmitSeries>,
  ) {
    const found = validateSchedule(values);
    const foundRule = validateRecurrence(recurrence, values.date);
    setErrors(found);
    setRecurrenceErrors(foundRule);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    if (Object.keys(foundRule).length > 0) {
      focusRecurrence(foundRule);
      return;
    }
    setSubmitting(true);
    try {
      const series = await submitSeriesFn(values, recurrence);
      setCreatedSeries(series);
      window.setTimeout(() => successHeading.current?.focus(), 0);
    } catch (caught) {
      handleSeriesFailure(caught);
    } finally {
      setSubmitting(false);
    }
  }

  function handleSeriesFailure(caught: unknown) {
    const list = conflictsOf(caught);
    if (list) {
      setSeriesConflicts(list);
      return;
    }
    setSeriesConflicts(null);
    if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
      const mapped = serverErrors(caught.fieldErrors);
      const mappedRule = recurrenceServerErrors(caught.fieldErrors);
      if (
        Object.keys(mapped).length > 0 ||
        Object.keys(mappedRule).length > 0
      ) {
        setErrors(mapped);
        setRecurrenceErrors(mappedRule);
        if (Object.keys(mapped).length > 0) {
          focusFirst(mapped);
        } else {
          focusRecurrence(mappedRule);
        }
        return;
      }
    }
    setFailure(caught);
  }

  async function overrideSeries(reason: string) {
    if (!onSubmitSeries || !seriesConflicts) {
      return;
    }
    const series = await onSubmitSeries(
      values,
      recurrence,
      seriesOverride(seriesConflicts, reason),
    );
    // The success panel replaces the form (and the dialog with it).
    setSeriesConflicts(null);
    setCreatedSeries(series);
    window.setTimeout(() => successHeading.current?.focus(), 0);
  }

  async function submit() {
    setConflicts(null);
    setSeriesConflicts(null);
    setRecurrenceErrors({});
    clearSuggestions();
    clearTutorSuggestions();
    setAnnouncement('');
    setFailure(null);
    if (repeats && onSubmitSeries) {
      await submitSeries(onSubmitSeries);
      return;
    }
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
      handleFailure(caught);
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

  function handleFailure(caught: unknown) {
    const list = conflictsOf(caught);
    if (list) {
      setConflicts(list);
      suggest(values);
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
  }

  async function confirmOverride(reason: string) {
    if (!conflicts) {
      return;
    }
    try {
      const session = await onSubmit(
        values,
        sessionOverride(conflicts, reason),
      );
      // The success panel replaces the form (and the dialog with it).
      setOverriding(false);
      setConflicts(null);
      clearSuggestions();
      setCreated(session);
      window.setTimeout(() => successHeading.current?.focus(), 0);
    } catch (caught) {
      const list = conflictsOf(caught);
      const onReason =
        caught instanceof ApiError
          ? overrideReasonError(caught.fieldErrors)
          : null;
      const onForm =
        caught instanceof ApiError &&
        !onReason &&
        caught.fieldErrors.length > 0;
      if (list || onForm) {
        // A new conflict list, or a 422 on the form's own fields: close the
        // dialog and show it on the page.
        focusAfterDialog.current = list ? 'panel' : 'button';
        setOverriding(false);
        handleFailure(caught);
        return;
      }
      throw caught;
    }
  }

  function afterDialog(event: Event) {
    event.preventDefault();
    const target = focusAfterDialog.current;
    focusAfterDialog.current = 'button';
    if (target === 'panel') {
      panel.current?.querySelector<HTMLElement>('h2')?.focus();
    } else {
      overrideButton.current?.focus();
    }
  }

  function scheduleAnother() {
    setCreated(null);
    setCreatedSeries(null);
    setRepeats(false);
    setRecurrence(EMPTY_RECURRENCE);
    setRecurrenceErrors({});
    weekdaysTouched.current = false;
    clearTutorSuggestions();
    setAnnouncement('');
    setErrors({});
    setValues({ ...EMPTY_SCHEDULE, date: values.date });
  }

  const ruleName = (code: string) =>
    isConflictCode(code) ? t(`scheduling.rules.${code}`) : code;
  const overriddenCodes = created
    ? [...new Set(created.overrides.flatMap((o) => o.codes))]
    : [];

  if (createdSeries) {
    const skipped = createdSeries.occurrences.filter(
      (o) => o.state === 'skipped_closure',
    ).length;
    const overridden = overriddenCount(createdSeries.occurrences);
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
          {t('scheduling.series.success.heading')}
        </h2>
        {overridden > 0 ? (
          <p className="font-medium">
            {t('scheduling.series.override.scheduledWith', {
              count: overridden,
            })}
          </p>
        ) : null}
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">{t('scheduling.fields.student')}</dt>
          <dd className="break-words">
            {createdSeries.participants.map((p) => p.display_name).join(', ')}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.subject')}</dt>
          <dd className="break-words">
            {t('scheduling.success.subject', {
              subject: createdSeries.subject.name,
              level: createdSeries.subject.education_level_name,
            })}
          </dd>
          <dt className="font-medium">{t('scheduling.series.rule')}</dt>
          <dd className="break-words">
            {describeRecurrence(createdSeries.recurrence, language)}
          </dd>
          <dt className="font-medium">{t('scheduling.success.state')}</dt>
          <dd>
            {createdSeries.tutor
              ? t('scheduling.success.offered', {
                  tutor: createdSeries.tutor.display_name,
                })
              : t('scheduling.success.unassigned')}
          </dd>
        </dl>
        <p>
          {t('scheduling.series.success.upcoming', {
            count: createdSeries.occurrences.length,
          })}
          {skipped > 0
            ? ` ${t('scheduling.series.success.skipped', { count: skipped })}`
            : null}
        </p>
        <div className="flex flex-wrap items-center gap-4">
          {seriesLink ? seriesLink(createdSeries.id) : null}
          <Button type="button" onClick={scheduleAnother}>
            {t('scheduling.success.another')}
          </Button>
        </div>
      </section>
    );
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
        {overriddenCodes.length > 0 ? (
          <div className="flex flex-col gap-1">
            <p className="font-medium">
              {t('scheduling.override.scheduledWith')}
            </p>
            <p className="text-sm text-muted-foreground">
              {t('scheduling.override.overriddenRules', {
                rules: overriddenCodes
                  .map((code) => ruleName(code))
                  .join(t('scheduling.reasons.separator')),
              })}
            </p>
          </div>
        ) : null}
        {created.warnings.length > 0 ? (
          <div
            role="status"
            className="flex flex-col gap-1 rounded-md border border-border p-3"
          >
            <p className="font-medium">{t('scheduling.warnings.heading')}</p>
            <ul className="list-disc pl-5 text-sm break-words">
              {created.warnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`}>
                  {warningMessage(warning)}
                </li>
              ))}
            </ul>
            <p className="text-sm text-muted-foreground">
              {t('scheduling.warnings.intro')}
            </p>
          </div>
        ) : null}
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
        <div className="flex flex-wrap items-center gap-4">
          {sessionLink ? sessionLink(created.id) : null}
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

  const action = conflicts ? overrideAction(conflicts, canOverride) : null;

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      {conflicts ? (
        <div ref={panel} className="flex flex-col gap-3">
          <ConflictPanel conflicts={conflicts} />
          {action === 'override' ? (
            <div>
              <Button
                ref={overrideButton}
                type="button"
                onClick={() => setOverriding(true)}
              >
                {t('scheduling.override.action')}
              </Button>
            </div>
          ) : (
            <p className="text-sm">
              {action === 'fixHardFirst'
                ? t('scheduling.override.fixHardFirst')
                : t('scheduling.override.notAllowed')}
            </p>
          )}
          <OverrideDialog
            open={overriding && action === 'override'}
            conflicts={conflicts.filter((c) => c.severity === 'soft')}
            onConfirm={confirmOverride}
            onCancel={() => setOverriding(false)}
            onCloseAutoFocus={afterDialog}
          />
        </div>
      ) : null}
      {suggestions ? (
        <SuggestedSlots
          state={suggestions}
          timeZone={timeZone}
          onChoose={chooseSlot}
        />
      ) : null}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {seriesConflicts ? (
        <SeriesOverrideSection
          conflicts={seriesConflicts}
          heading={t('scheduling.series.conflicts.createHeading', {
            count: seriesConflicts.length,
          })}
          canOverride={canOverride}
          onOverride={overrideSeries}
          onRefused={handleSeriesFailure}
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
        {onSuggestTutors ? (
          <div className="flex flex-col gap-3">
            <div>
              <Button
                type="button"
                variant="outline"
                disabled={
                  !canSuggestTutors(values) ||
                  tutorSuggestions?.status === 'loading'
                }
                onClick={suggestTutors}
              >
                {t('scheduling.tutorSuggestions.button')}
              </Button>
            </div>
            {tutorSuggestions ? (
              <TutorSuggestions
                state={tutorSuggestions}
                onChoose={chooseTutor}
              />
            ) : null}
          </div>
        ) : null}
        <fieldset className="flex flex-col gap-3">
          <legend className="text-sm font-medium">
            {t('scheduling.fields.when')}
          </legend>
          <p id={zoneId} className="text-sm text-muted-foreground">
            {t('scheduling.zone', { zone: timeZone })}
          </p>
          {onSubmitSeries ? (
            <label className="flex min-h-9 w-fit items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                role="switch"
                checked={repeats}
                onChange={(event) => toggleRepeats(event.target.checked)}
                className="size-4 accent-foreground"
              />
              {t('scheduling.series.repeats')}
            </label>
          ) : null}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {timeField(
              'date',
              repeats
                ? t('scheduling.series.startsOn')
                : t('scheduling.fields.date'),
            )}
            {timeField('start', t('scheduling.fields.start'))}
            {timeField('end', t('scheduling.fields.end'))}
          </div>
          {repeats ? (
            <RecurrenceFields
              idPrefix={`${id}-recurrence`}
              values={recurrence}
              errors={recurrenceErrors}
              onChange={changeRecurrence}
            />
          ) : null}
        </fieldset>
        <div>
          <Button ref={submitButton} type="submit" disabled={submitting}>
            {submitting ? <Spinner /> : null}
            {submitting
              ? t('scheduling.submitting')
              : repeats
                ? t('scheduling.series.submit')
                : t('scheduling.submit')}
          </Button>
        </div>
      </form>
    </div>
  );
}
