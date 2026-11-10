import { useQuery } from '@tanstack/react-query';
import {
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Combobox, type OptionsPage } from '@/components/ui/combobox';
import { Input } from '@/components/ui/input';
import { TimeField } from '@/components/ui/time-field';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import { availabilityKey, type TutorAvailability } from '@/features/tutors/api';
import { useFormatters } from '@/lib/formatters';
import { toLanguage } from '@/i18n/language';
import { addMinutes } from '@/lib/timeOfDay';
import type { ClassSeriesCreated } from '../series/api';
import { describeRecurrence } from '../series/describeRecurrence';
import {
  clearRecurrenceServerErrors,
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
  type SeriesTutorSuggestionList,
  type SlotSuggestions,
  type SubjectOption,
  type SuggestedSlot,
  type TutorSuggestion,
  type TutorSuggestionList,
} from './api';
import { ConflictPanel } from './ConflictPanel';
import { OverrideDialog } from './OverrideDialog';
import {
  canFindFreeTimes,
  clearServerError,
  DEFAULT_DURATION_MINUTES,
  EMPTY_SCHEDULE,
  endAfterStartChange,
  FIELD_ORDER,
  isChosenTutorUnsuggested,
  isConflictCode,
  isMakeupTaken,
  isRoomTooSmall,
  overrideAction,
  overrideReasonError,
  ownerRefusal,
  READABLE_TIMES,
  ownerServerErrors,
  serverErrors,
  sessionOverride,
  slotValues,
  splitServerErrors,
  subjectsByLevel,
  takenMakeupId,
  TUTOR_SUGGESTIONS_DELAY_MS,
  tutorSuggestionKey,
  validateOwnerSchedule,
  validateSchedule,
  VALUE_FIELDS,
  weeklyAvailabilityText,
  withTimeFormat,
  type OwnerKind,
  type OwnerOption,
  type OwnerSummary,
  type ScheduleErrors,
  type ScheduleField,
  type ScheduleMessage,
  type ScheduleValues,
  type SessionKindChoice,
  type SessionOverride,
  type UnreadableTimes,
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
// Suggested tutors (#51 G, HF-05b B): with `onSuggestTutors` (the page
// passes it only to holders of schedule.assign) the panel sits under the
// Tutor and Classroom pickers, which come after "When". It loads by itself
// 400 ms after the subject, date, start and end (and, in series mode, the
// rule, through `onSuggestSeriesTutors`) become valid or change, and after a
// classroom or repeats change; never on a tutor change. A change aborts the
// request in flight and a stale answer is dropped; inputs back to the last
// loaded ones show that list again without a request. "Choose" sets the
// Tutor picker (nothing is submitted), keeps the list and marks the row
// "Chosen"; a pre-filled or picked tutor is marked too. A chosen tutor
// missing from a complete list gets a note under the picker (B6). A 422
// goes onto the form's fields; the focus never moves on a load.
//
// Tutor chosen (HF-05b C): with `loadTutorAvailability` (tutor.view) a line
// under the picker lists the tutor's weekly windows; with `onFindFreeTimes`
// (one-off mode) "Find free times" lists the tutor's nearest free times over
// 7 days; choosing one fills the date and times like the times after a
// conflict. Any change to the form hides a shown list.
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
//
// Makeup mode (#436 B): with `makeup` the student and subject are read-only
// text (their 422s go to the page's alert, with the link's own refusals), the
// notice sits on top, a taken target links to its makeup, and the success
// panel links to the target; "Schedule another" is the page's, to leave the
// mode.
//
// Group and course sessions (HF-11b, #593): with `owners` a "Class type"
// radio group (Individual, the default; Group; Course) sits before the
// student, never in makeup mode. Group or Course hides the student, the
// subject, "Repeats weekly" and the suggested and free times, and shows a
// type-ahead picker of the active groups or current cohorts, then the
// chosen one's subject, a cohort's dates and its enrolled students (an
// empty roster is allowed: later enrollments join). Tutor suggestions use
// the derived subject; the classroom options show their capacity, with a
// warning when the roster is larger. Switching type keeps the date, times,
// tutor and classroom and clears the other types' selection. A
// `student_overlap` names the student; `group_ended` / `cohort_ended` clear
// the selection and `roster_changed` reads the roster again, both as an
// alert. Individual mode is unchanged.

/** A group or course session's sources (HF-11b). */
export interface ScheduleOwners {
  /** The active groups (`q`: the name contains it); none: no Group option. */
  searchGroups?:
    | ((
        q: string | null,
        signal: AbortSignal,
      ) => Promise<OptionsPage<OwnerOption>>)
    | undefined;
  /** The current cohorts; none: no Course option. */
  searchCohorts?:
    | ((
        q: string | null,
        signal: AbortSignal,
      ) => Promise<OptionsPage<OwnerOption>>)
    | undefined;
  /** The chosen group or cohort with its students enrolled now. */
  loadOwner: (
    kind: OwnerKind,
    id: string,
    signal: AbortSignal,
  ) => Promise<OwnerSummary>;
  /** Creates the session (with the override when given); rejects with the error. */
  onSubmit: (
    kind: OwnerKind,
    ownerId: string,
    values: ScheduleValues,
    override?: SessionOverride,
  ) => Promise<ClassSessionCreated>;
  /** Each active room's capacity, by id; none: no capacity shown. */
  classroomCapacities?: Readonly<Record<string, number>> | undefined;
  /** A link to the group's or cohort's page (the success panel). */
  ownerLink?: (kind: OwnerKind, id: string) => ReactNode;
}

/** Makeup mode: what the form shows around the fixed values (#436). */
export interface ScheduleMakeup {
  studentName: string;
  subjectName: string;
  /** The session being made up, and the deadline (B4). */
  notice: ReactNode;
  /** The link to the session being made up (B8). */
  targetLink: ReactNode;
  /** The link to the makeup that took the target (C2). */
  takenLink: (sessionId: string) => ReactNode;
  /** Leaves makeup mode (B8). */
  onScheduleAnother: () => void;
}

export interface ScheduleSessionFormProps {
  subjects: readonly SubjectOption[];
  tutors: readonly Option[];
  classrooms: readonly Option[];
  searchStudents: (
    q: string | null,
    signal: AbortSignal,
  ) => Promise<OptionsPage<Option>>;
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
  /** The eligible tutors for the slot (#51; schedule.assign); none: no panel. */
  onSuggestTutors?: (
    values: ScheduleValues,
    signal: AbortSignal,
  ) => Promise<TutorSuggestionList>;
  /** The tutors free for every occurrence of the series (HF-05b B2). */
  onSuggestSeriesTutors?: (
    values: ScheduleValues,
    recurrence: RecurrenceValues,
    signal: AbortSignal,
  ) => Promise<SeriesTutorSuggestionList>;
  /** The link to assign subjects (qualification.review; HF-05b B5). */
  assignSubjectsLink?: ReactNode;
  /** The chosen tutor's availability (tutor.view; HF-05b C1). */
  loadTutorAvailability?: (
    tutorId: string,
    signal: AbortSignal,
  ) => Promise<TutorAvailability>;
  /** The link to edit a tutor's availability (tutor.manage; HF-05b C1). */
  tutorAvailabilityLink?: (tutorId: string) => ReactNode;
  /** The chosen tutor's nearest free times (HF-05b C2); none: no button. */
  onFindFreeTimes?: (values: ScheduleValues) => Promise<SlotSuggestions>;
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
  /**
   * "View in calendar" on both success panels, for the session's (or the
   * series' first) date (HF-02 C7); the page passes it when pre-filled.
   */
  calendarLink?: (date: string) => ReactNode;
  /** Whether "Repeats weekly" starts on (stories). */
  initialRepeats?: boolean;
  /** The rule to start from (stories). */
  initialRecurrence?: RecurrenceValues;
  /** Makeup mode (#436); none: the plain form. */
  makeup?: ScheduleMakeup | undefined;
  /** Group and course sessions (HF-11b); none: individual only. */
  owners?: ScheduleOwners | undefined;
  /** The type to start from (stories). */
  initialKind?: SessionKindChoice;
  /** The group or cohort to start from (stories). */
  initialOwner?: OwnerOption | null;
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
  onSuggestSeriesTutors,
  assignSubjectsLink,
  loadTutorAvailability,
  tutorAvailabilityLink,
  onFindFreeTimes,
  initialValues = EMPTY_SCHEDULE,
  labelledBy,
  sessionLink,
  onSubmitSeries,
  seriesLink,
  calendarLink,
  initialRepeats = false,
  initialRecurrence = EMPTY_RECURRENCE,
  makeup,
  owners,
  initialKind = 'individual',
  initialOwner = null,
}: ScheduleSessionFormProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const weekdayFormat = new Intl.DateTimeFormat(language, {
    weekday: 'short',
    timeZone: 'UTC',
  });
  // 2024-01-01 is a Monday (ISO weekday 1).
  const weekdayName = (weekday: number) =>
    weekdayFormat.format(Date.UTC(2024, 0, weekday));
  const { t: tErrors } = useTranslation('errors');
  const { formatInstant, formatLocalDate } = useFormatters();
  const id = useId();
  const [values, setValues] = useState<ScheduleValues>(initialValues);
  // HF-13 (#598 E4): after the first submit attempt the client checks run
  // on every render; the server's field errors stay until their field
  // changes. `unreadable`: a time field holds text that isn't a time.
  const [checked, setChecked] = useState(false);
  const [serverFieldErrors, setServerFieldErrors] = useState<ScheduleErrors>(
    {},
  );
  const [unreadable, setUnreadable] = useState<UnreadableTimes>(READABLE_TIMES);
  const [submitting, setSubmitting] = useState(false);
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [created, setCreated] = useState<ClassSessionCreated | null>(null);
  // HF-11b: the type, the chosen group or cohort, and what a 201 was for.
  const [kind, setKind] = useState<SessionKindChoice>(initialKind);
  const [owner, setOwner] = useState<OwnerOption | null>(initialOwner);
  const [createdFor, setCreatedFor] = useState<{
    kind: OwnerKind;
    owner: OwnerOption;
  } | null>(null);
  const [repeats, setRepeats] = useState(
    initialRepeats && onSubmitSeries !== undefined,
  );
  const [recurrence, setRecurrence] =
    useState<RecurrenceValues>(initialRecurrence);
  const [ruleChecked, setRuleChecked] = useState(false);
  const [serverRuleErrors, setServerRuleErrors] = useState<RecurrenceErrors>(
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
  // The last load's answer for its key (null: nothing to show, e.g. a 422),
  // and the last list that loaded (shown again when its key comes back).
  const [tutorLoad, setTutorLoad] = useState<{
    key: string;
    state: TutorSuggestionsState | null;
  } | null>(null);
  const [tutorSettled, setTutorSettled] = useState<{
    key: string;
    state: TutorSuggestionsState;
  } | null>(null);
  const tutorRequest = useRef(0);
  // "Find free times" (C2), for the values it was asked with.
  const [freeTimes, setFreeTimes] = useState<{
    key: string;
    state: SuggestionsState;
  } | null>(null);
  const freeTimesRequest = useRef(0);
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

  const fixed = makeup !== undefined;
  const ownerKinds: OwnerKind[] = fixed
    ? []
    : [
        ...(owners?.searchGroups ? (['group'] as const) : []),
        ...(owners?.searchCohorts ? (['course'] as const) : []),
      ];
  // The group or course being scheduled; null: an individual session.
  const ownerKind: OwnerKind | null =
    kind !== 'individual' && ownerKinds.includes(kind) ? kind : null;
  const ownerSummary = useQuery({
    queryKey: ['scheduling', 'create', 'owner', ownerKind, owner?.id ?? ''],
    queryFn: ({ signal }) =>
      owners && ownerKind && owner
        ? owners.loadOwner(ownerKind, owner.id, signal)
        : Promise.reject(new Error('no group or cohort')),
    enabled: ownerKind !== null && owner !== null,
  });
  const summary: OwnerSummary | undefined =
    ownerKind && owner ? ownerSummary.data : undefined;
  // What the tutor suggestions load for: a group or course has its subject.
  const slotSource: ScheduleValues = ownerKind
    ? { ...values, student: null, subjectId: summary?.subject.id ?? '' }
    : values;
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

  /** The client checks of the current type (one-off or series fields). */
  function checkFields(forValues: ScheduleValues): ScheduleErrors {
    const found = ownerKind
      ? validateOwnerSchedule(forValues, ownerKind, owner?.id ?? null)
      : validateSchedule(forValues);
    return withTimeFormat(found, unreadable);
  }

  /** A server field error goes once its field changes (#598 E4). */
  function changedField(field: ScheduleField) {
    setServerFieldErrors((current) => clearServerError(current, field));
  }

  function change<K extends keyof ScheduleValues>(
    key: K,
    value: ScheduleValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    changedField(VALUE_FIELDS[key]);
    if (key === 'date' && !weekdaysTouched.current) {
      setRecurrence((current) => ({
        ...current,
        weekdays: weekdaysFor(value as string),
      }));
    }
  }

  /**
   * A typed or picked start or end (#598). A new start moves the end
   * (E3): it keeps the duration, or is the start + 1 h.
   */
  function changeTime(
    field: 'start' | 'end',
    value: string,
    isUnreadable: boolean,
  ) {
    if (field === 'start') {
      const end = endAfterStartChange(values.start, value, values.end);
      setValues((current) => ({ ...current, start: value, end }));
      setUnreadable((current) => ({
        start: isUnreadable,
        end: end !== values.end ? false : current.end,
      }));
    } else {
      setValues((current) => ({ ...current, end: value }));
      setUnreadable((current) => ({ ...current, end: isUnreadable }));
    }
    changedField(field);
  }

  function toggleRepeats(on: boolean) {
    setRepeats(on);
    setConflicts(null);
    setSeriesConflicts(null);
    clearSuggestions();
    setFailure(null);
    // The rule's checks start with the next series submit.
    setRuleChecked(false);
    setServerRuleErrors({});
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
    setServerRuleErrors((current) =>
      clearRecurrenceServerErrors(current, recurrence, next),
    );
  }

  function focusRecurrence(found: RecurrenceErrors) {
    const first = RECURRENCE_FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document
        .getElementById(recurrenceFieldId(`${id}-recurrence`, first))
        ?.focus();
    }
  }

  const seriesMode =
    ownerKind === null && repeats && onSubmitSeries !== undefined;
  // What the fields show (#598 E4): the client checks once checked (they
  // win), else the server's errors.
  const errors: ScheduleErrors = checked
    ? { ...serverFieldErrors, ...checkFields(values) }
    : serverFieldErrors;
  const recurrenceErrors: RecurrenceErrors =
    seriesMode && ruleChecked
      ? {
          ...serverRuleErrors,
          ...validateRecurrence(recurrence, values.date),
        }
      : serverRuleErrors;
  const suggestionKey = onSuggestTutors
    ? tutorSuggestionKey(slotSource, seriesMode, recurrence)
    : null;
  let tutorSuggestions: TutorSuggestionsState | null = null;
  if (suggestionKey !== null) {
    if (tutorLoad?.key === suggestionKey) {
      tutorSuggestions = tutorLoad.state;
    } else if (tutorSettled?.key === suggestionKey) {
      tutorSuggestions = tutorSettled.state;
    } else {
      // Waiting for the debounce, or the request in flight.
      tutorSuggestions = { status: 'loading' };
    }
  }

  const isSettled = useEffectEvent((key: string) => tutorSettled?.key === key);

  const loadTutorSuggestions = useEffectEvent(
    (key: string, signal: AbortSignal) => {
      if (!onSuggestTutors) {
        return;
      }
      tutorRequest.current += 1;
      const request = tutorRequest.current;
      const series = seriesMode && onSuggestSeriesTutors !== undefined;
      const call = series
        ? onSuggestSeriesTutors(slotSource, recurrence, signal)
        : onSuggestTutors(slotSource, signal);
      call.then(
        (result) => {
          if (request !== tutorRequest.current || signal.aborted) {
            return;
          }
          const state: TutorSuggestionsState = { status: 'loaded', result };
          setTutorLoad({ key, state });
          setTutorSettled({ key, state });
        },
        (error: unknown) => {
          if (request !== tutorRequest.current || signal.aborted) {
            return;
          }
          if (error instanceof ApiError && error.fieldErrors.length > 0) {
            const mapped = splitServerErrors(error.fieldErrors, fixed).fields;
            const mappedRule = series
              ? recurrenceServerErrors(error.fieldErrors)
              : {};
            if (
              Object.keys(mapped).length > 0 ||
              Object.keys(mappedRule).length > 0
            ) {
              // On the fields, without moving the focus (D1).
              setTutorLoad({ key, state: null });
              setServerFieldErrors(mapped);
              setServerRuleErrors(mappedRule);
              return;
            }
          }
          setTutorLoad({ key, state: { status: 'error', error } });
        },
      );
    },
  );

  useEffect(() => {
    if (suggestionKey === null || isSettled(suggestionKey)) {
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(
      () => loadTutorSuggestions(suggestionKey, controller.signal),
      TUTOR_SUGGESTIONS_DELAY_MS,
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [suggestionKey]);

  // The chosen tutor's weekly windows (C1), cached by tutor.
  const availability = useQuery({
    queryKey: availabilityKey(values.tutorId),
    queryFn: ({ signal }) =>
      loadTutorAvailability
        ? loadTutorAvailability(values.tutorId, signal)
        : Promise.reject(new Error('no loader')),
    enabled: loadTutorAvailability !== undefined && values.tutorId !== '',
    staleTime: 60_000,
  });

  function chooseTutor(suggestion: TutorSuggestion) {
    setValues((current) => ({ ...current, tutorId: suggestion.tutor.id }));
    changedField('tutor');
    setAnnouncement(
      t('scheduling.tutorSuggestions.chosen', {
        name: suggestion.tutor.display_name,
      }),
    );
  }

  // "Find free times" (C2): shown only for the values it was asked with.
  const freeTimesKey = JSON.stringify(values);
  const shownFreeTimes =
    !seriesMode && freeTimes?.key === freeTimesKey ? freeTimes.state : null;

  function findFreeTimes() {
    if (!onFindFreeTimes || !canFindFreeTimes(values)) {
      return;
    }
    freeTimesRequest.current += 1;
    const request = freeTimesRequest.current;
    const key = freeTimesKey;
    setFreeTimes({ key, state: { status: 'loading' } });
    onFindFreeTimes(values).then(
      (result) => {
        if (request === freeTimesRequest.current) {
          setFreeTimes({ key, state: { status: 'loaded', result } });
        }
      },
      (error: unknown) => {
        if (request === freeTimesRequest.current) {
          setFreeTimes({ key, state: { status: 'error', error } });
        }
      },
    );
  }

  function chooseFreeTime(slot: SuggestedSlot) {
    freeTimesRequest.current += 1;
    setFreeTimes(null);
    chooseSlot(slot);
  }

  function focusFirst(found: ScheduleErrors) {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (!first) {
      return;
    }
    const element =
      first === 'student' || first === 'owner'
        ? document
            .getElementById(`${fieldId(first)}-picker`)
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
    setUnreadable(READABLE_TIMES);
    setServerFieldErrors({});
    setConflicts(null);
    clearSuggestions();
    setAnnouncement(t('scheduling.suggestions.chosen', slotParts(slot)));
    window.setTimeout(() => submitButton.current?.focus(), 0);
  }

  async function submitSeries(
    submitSeriesFn: NonNullable<typeof onSubmitSeries>,
  ) {
    const found = checkFields(values);
    const foundRule = validateRecurrence(recurrence, values.date);
    setChecked(true);
    setRuleChecked(true);
    setServerFieldErrors({});
    setServerRuleErrors({});
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
        setServerFieldErrors(mapped);
        setServerRuleErrors(mappedRule);
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
    setServerRuleErrors({});
    clearSuggestions();
    setAnnouncement('');
    setFailure(null);
    if (seriesMode && onSubmitSeries) {
      await submitSeries(onSubmitSeries);
      return;
    }
    const found = checkFields(values);
    setChecked(true);
    setServerFieldErrors({});
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    setSubmitting(true);
    try {
      const session = await create();
      showCreated(session);
    } catch (caught) {
      handleFailure(caught);
    } finally {
      setSubmitting(false);
    }
  }

  /** The create for the current type (an override only when given). */
  function create(override?: SessionOverride): Promise<ClassSessionCreated> {
    if (ownerKind && owners && owner) {
      return override
        ? owners.onSubmit(ownerKind, owner.id, values, override)
        : owners.onSubmit(ownerKind, owner.id, values);
    }
    return override ? onSubmit(values, override) : onSubmit(values);
  }

  function showCreated(session: ClassSessionCreated) {
    setCreated(session);
    setCreatedFor(ownerKind && owner ? { kind: ownerKind, owner } : null);
    window.setTimeout(() => successHeading.current?.focus(), 0);
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
    const refusal = ownerKind ? ownerRefusal(caught) : null;
    if (list) {
      setConflicts(list);
      if (!ownerKind) {
        suggest(values);
      }
    } else if (refusal) {
      // An ended group or cohort can't be chosen again; a changed roster
      // is read again (#592).
      if (refusal === 'ended') {
        setOwner(null);
      } else {
        void ownerSummary.refetch();
      }
      setFailure(caught);
    } else if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
      const { fields, page } = ownerKind
        ? { fields: ownerServerErrors(caught.fieldErrors, ownerKind), page: [] }
        : splitServerErrors(caught.fieldErrors, fixed);
      const onFields = Object.keys(fields).length > 0;
      if (onFields) {
        setServerFieldErrors(fields);
        focusFirst(fields);
      }
      if (page.length > 0 || !onFields) {
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
      const session = await create(sessionOverride(conflicts, reason));
      // The success panel replaces the form (and the dialog with it).
      setOverriding(false);
      setConflicts(null);
      clearSuggestions();
      showCreated(session);
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
      const refused = ownerKind !== null && ownerRefusal(caught) !== null;
      if (list || onForm || isMakeupTaken(caught) || refused) {
        // A new conflict list, a 422 on the form's own fields, a taken
        // makeup target or an ended group or cohort or changed roster:
        // close the dialog and show it on the page.
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
    if (makeup) {
      makeup.onScheduleAnother();
      return;
    }
    setCreated(null);
    setCreatedFor(null);
    setOwner(null);
    setCreatedSeries(null);
    setRepeats(false);
    setRecurrence(EMPTY_RECURRENCE);
    setRuleChecked(false);
    setServerRuleErrors({});
    weekdaysTouched.current = false;
    setAnnouncement('');
    setChecked(false);
    setServerFieldErrors({});
    setUnreadable(READABLE_TIMES);
    setValues({ ...EMPTY_SCHEDULE, date: values.date });
  }

  /** Switching type keeps the date, times, tutor and classroom (HF-11b). */
  function changeKind(next: SessionKindChoice) {
    if (next === kind) {
      return;
    }
    setKind(next);
    setOwner(null);
    setValues((current) => ({ ...current, student: null, subjectId: '' }));
    setRepeats(false);
    setRuleChecked(false);
    setServerRuleErrors({});
    setChecked(false);
    setServerFieldErrors({});
    setConflicts(null);
    setSeriesConflicts(null);
    clearSuggestions();
    setFreeTimes(null);
    setFailure(null);
  }

  function chooseOwner(next: OwnerOption) {
    setOwner(next);
    changedField('owner');
    setConflicts(null);
    setFailure(null);
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
          {calendarLink ? calendarLink(values.date) : null}
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
          {createdFor
            ? createdFor.kind === 'group'
              ? t('scheduling.create.owner.groupScheduled')
              : t('scheduling.create.owner.courseScheduled')
            : t('scheduling.success.heading')}
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
          {createdFor ? (
            <>
              <dt className="font-medium">
                {createdFor.kind === 'group'
                  ? t('scheduling.create.owner.group')
                  : t('scheduling.create.owner.course')}
              </dt>
              <dd className="break-words">{createdFor.owner.name}</dd>
              <dt className="font-medium">
                {t('scheduling.create.owner.participants')}
              </dt>
              <dd>
                {t('scheduling.create.owner.participantCount', {
                  count: created.participants.length,
                })}
              </dd>
            </>
          ) : (
            <>
              <dt className="font-medium">{t('scheduling.fields.student')}</dt>
              <dd>
                {created.participants.map((p) => p.display_name).join(', ')}
              </dd>
            </>
          )}
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
          {makeup ? (
            <>
              <dt className="font-medium">{t('scheduling.makeup.of')}</dt>
              <dd>{makeup.targetLink}</dd>
            </>
          ) : null}
          <dt className="font-medium">{t('scheduling.success.state')}</dt>
          <dd>
            {tutor
              ? t('scheduling.success.offered', { tutor: tutor.display_name })
              : t('scheduling.success.unassigned')}
          </dd>
        </dl>
        <div className="flex flex-wrap items-center gap-4">
          {sessionLink ? sessionLink(created.id) : null}
          {createdFor && owners?.ownerLink
            ? owners.ownerLink(createdFor.kind, createdFor.owner.id)
            : null}
          {calendarLink ? calendarLink(values.date) : null}
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
    extra: { describedBy?: string | undefined; after?: ReactNode } = {},
  ) {
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <label htmlFor={fieldId(field)} className="text-sm font-medium">
          {label}
        </label>
        <select
          id={fieldId(field)}
          className={SELECT_CLASS}
          value={value}
          aria-invalid={errors[field] ? true : undefined}
          aria-describedby={describedBy(field, extra.describedBy)}
          onChange={(event) => onChange(event.target.value)}
        >
          {options}
        </select>
        <FieldError id={errorId(field)} message={message(errors[field])} />
        {extra.after}
      </div>
    );
  }

  function timeField(
    field: 'date' | 'start' | 'end',
    label: string,
    help?: string,
  ) {
    const helpId = `${fieldId(field)}-help`;
    const labelId = `${fieldId(field)}-label`;
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <label
          id={labelId}
          htmlFor={fieldId(field)}
          className="text-sm font-medium"
        >
          {label}
        </label>
        {field === 'date' ? (
          <Input
            id={fieldId(field)}
            type="date"
            value={values.date}
            aria-invalid={errors.date ? true : undefined}
            aria-describedby={describedBy(field, help ? helpId : undefined)}
            onChange={(event) => change('date', event.target.value)}
          />
        ) : (
          // Type or pick (#598 E1 a); the end's list opens at start + 1 h.
          <TimeField
            id={fieldId(field)}
            labelId={labelId}
            value={values[field]}
            scrollTo={
              field === 'end'
                ? (addMinutes(values.start, DEFAULT_DURATION_MINUTES) ??
                  undefined)
                : undefined
            }
            invalid={errors[field] !== undefined}
            aria-describedby={describedBy(field, zoneId)}
            onChange={(value, isUnreadable) =>
              changeTime(field, value, isUnreadable)
            }
          />
        )}
        {help ? (
          <p id={helpId} className="text-sm text-muted-foreground">
            {help}
          </p>
        ) : null}
        <FieldError id={errorId(field)} message={message(errors[field])} />
      </div>
    );
  }

  const tutorName =
    tutors.find((tutor) => tutor.id === values.tutorId)?.name ?? '';
  const subjectName = ownerKind
    ? summary?.subject.name
    : makeup
      ? makeup.subjectName
      : subjects.find((subject) => subject.id === values.subjectId)?.name;
  const loadedSuggestions =
    tutorSuggestions?.status === 'loaded' ? tutorSuggestions.result : null;
  const unsuggestedId = `${id}-tutor-unsuggested`;
  const unsuggested =
    loadedSuggestions !== null &&
    isChosenTutorUnsuggested(loadedSuggestions, values.tutorId);
  let suggestionSummary: string | undefined;
  if (loadedSuggestions && subjectName !== undefined) {
    suggestionSummary =
      loadedSuggestions.occurrence_count === undefined
        ? t('scheduling.tutorSuggestions.summary', {
            subject: subjectName,
            date: formatLocalDate(values.date, 'long'),
            start: values.start,
            end: values.end,
          })
        : t('scheduling.tutorSuggestions.seriesSummary', {
            subject: subjectName,
            count: loadedSuggestions.occurrence_count,
          });
  }
  let availabilityLine: ReactNode = null;
  if (loadTutorAvailability && values.tutorId) {
    if (availability.data) {
      const text = weeklyAvailabilityText(
        availability.data.weekly,
        weekdayName,
      );
      availabilityLine = (
        <div className="flex flex-col gap-1 text-sm break-words">
          <p>
            {text === null
              ? t('scheduling.tutorAvailability.none', { name: tutorName })
              : t('scheduling.tutorAvailability.weekly', {
                  name: tutorName,
                  windows: text,
                })}
            {text === null && tutorAvailabilityLink ? (
              <> {tutorAvailabilityLink(values.tutorId)}</>
            ) : null}
          </p>
          <p className="text-muted-foreground">
            {t('scheduling.tutorAvailability.exceptions')}
          </p>
        </div>
      );
    } else if (availability.isError) {
      availabilityLine = (
        <ErrorAlert
          error={availability.error}
          onRetry={() => void availability.refetch()}
        />
      );
    }
  }

  const action = conflicts ? overrideAction(conflicts, canOverride) : null;
  const refusal = ownerKind ? ownerRefusal(failure) : null;
  let failureMessage: string | undefined;
  if (refusal === 'ended') {
    failureMessage =
      ownerKind === 'group'
        ? t('scheduling.create.owner.groupEnded')
        : t('scheduling.create.owner.courseEnded');
  } else if (refusal === 'rosterChanged') {
    failureMessage = t('scheduling.create.owner.rosterChanged');
  } else if (failure instanceof ApiError) {
    failureMessage =
      splitServerErrors(failure.fieldErrors, fixed)
        .page.map(message)
        .join(' ') || undefined;
  }
  const rosterNames = new Map(
    (summary?.students ?? []).map((student) => [student.id, student.name]),
  );
  const studentName = ownerKind
    ? (studentId: string) => rosterNames.get(studentId)
    : undefined;
  const capacities = owners?.classroomCapacities;
  const searchOwners =
    ownerKind === 'group' ? owners?.searchGroups : owners?.searchCohorts;
  const roomCapacity = values.classroomId
    ? capacities?.[values.classroomId]
    : undefined;
  const roomWarningId = `${id}-room-warning`;
  const roomTooSmall =
    ownerKind !== null &&
    summary !== undefined &&
    isRoomTooSmall(roomCapacity, summary.students.length);
  const cohortDates =
    ownerKind === 'course' && summary?.firstSessionOn && summary.lastSessionOn
      ? {
          first: formatLocalDate(summary.firstSessionOn),
          last: formatLocalDate(summary.lastSessionOn),
        }
      : null;
  const takenId = takenMakeupId(failure);

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      {makeup?.notice}
      {conflicts ? (
        <div ref={panel} className="flex flex-col gap-3">
          <ConflictPanel conflicts={conflicts} studentName={studentName} />
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
      {failure ? <ErrorAlert error={failure} message={failureMessage} /> : null}
      {makeup && takenId ? <p>{makeup.takenLink(takenId)}</p> : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-6"
      >
        {ownerKinds.length > 0 ? (
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">
              {t('scheduling.create.type.legend')}
            </legend>
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              {(['individual', ...ownerKinds] as const).map((choice) => (
                <label
                  key={choice}
                  className="flex min-h-9 items-center gap-2 text-sm"
                >
                  <input
                    type="radio"
                    name={`${id}-kind`}
                    value={choice}
                    checked={kind === choice}
                    onChange={() => changeKind(choice)}
                    className="size-4 accent-foreground"
                  />
                  {t(`scheduling.create.type.${choice}`)}
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}
        {ownerKind && searchOwners ? (
          <>
            <div id={`${fieldId('owner')}-picker`}>
              <Combobox<OwnerOption>
                key={ownerKind}
                label={
                  ownerKind === 'group'
                    ? t('scheduling.create.owner.group')
                    : t('scheduling.create.owner.course')
                }
                help={
                  ownerKind === 'group'
                    ? t('scheduling.create.owner.groupHelp')
                    : t('scheduling.create.owner.courseHelp')
                }
                queryKey={['scheduling', 'create', 'owners', ownerKind]}
                load={searchOwners}
                optionId={(option) => option.id}
                optionLabel={(option) => option.name}
                optionNote={(option) => option.subjectName}
                emptyText={
                  ownerKind === 'group'
                    ? t('scheduling.create.owner.noGroups')
                    : t('scheduling.create.owner.noCourses')
                }
                selected={owner ? { id: owner.id, label: owner.name } : null}
                onSelect={chooseOwner}
                errorMessage={message(errors.owner)}
              />
            </div>
            <p aria-live="polite" className="sr-only">
              {owner && summary
                ? t('scheduling.create.owner.announce', {
                    name: owner.name,
                    subject: summary.subject.name,
                    count: summary.students.length,
                  })
                : ''}
            </p>
            {owner ? (
              <section
                aria-labelledby={`${id}-owner-summary`}
                className="flex min-w-0 flex-col gap-3 rounded-md border border-border p-4"
              >
                <h2
                  id={`${id}-owner-summary`}
                  className="font-medium break-words"
                >
                  {owner.name}
                </h2>
                {summary ? (
                  <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
                    <dt className="font-medium">
                      {t('scheduling.fields.subject')}
                    </dt>
                    <dd className="break-words">
                      {t('scheduling.success.subject', {
                        subject: summary.subject.name,
                        level: summary.subject.levelName,
                      })}
                    </dd>
                    {ownerKind === 'course' ? (
                      <>
                        <dt className="font-medium">
                          {t('scheduling.create.owner.dates')}
                        </dt>
                        <dd>
                          {cohortDates
                            ? t(
                                'scheduling.create.owner.dateRange',
                                cohortDates,
                              )
                            : t('scheduling.create.owner.noDates')}
                        </dd>
                      </>
                    ) : null}
                    <dt className="font-medium">
                      {t('scheduling.create.owner.enrolled', {
                        count: summary.students.length,
                      })}
                    </dt>
                    <dd className="min-w-0">
                      {summary.students.length > 0 ? (
                        <ul className="flex flex-wrap gap-x-4 gap-y-1">
                          {summary.students.map((student) => (
                            <li key={student.id} className="break-words">
                              {student.name}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p>{t('scheduling.create.owner.emptyRoster')}</p>
                      )}
                    </dd>
                  </dl>
                ) : ownerSummary.isError ? (
                  <ErrorAlert
                    error={ownerSummary.error}
                    onRetry={() => void ownerSummary.refetch()}
                  />
                ) : (
                  <LoadingState rows={2} />
                )}
              </section>
            ) : null}
          </>
        ) : makeup ? (
          <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
            <dt className="font-medium">{t('scheduling.fields.student')}</dt>
            <dd className="break-words">{makeup.studentName}</dd>
            <dt className="font-medium">{t('scheduling.fields.subject')}</dt>
            <dd className="break-words">{makeup.subjectName}</dd>
          </dl>
        ) : (
          <div id={`${fieldId('student')}-picker`}>
            <Combobox<Option>
              label={t('scheduling.fields.student')}
              help={
                values.student
                  ? t('scheduling.studentChosen', { name: values.student.name })
                  : t('scheduling.studentHelp')
              }
              queryKey={['scheduling', 'create', 'students']}
              load={searchStudents}
              optionId={(student) => student.id}
              optionLabel={(student) => student.name}
              emptyText={t('combobox.emptyStudents')}
              selected={
                values.student
                  ? { id: values.student.id, label: values.student.name }
                  : null
              }
              onSelect={(student) => change('student', student)}
              errorMessage={message(errors.student)}
            />
          </div>
        )}
        {makeup || ownerKind
          ? null
          : select(
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
        <fieldset className="flex flex-col gap-3">
          <legend className="text-sm font-medium">
            {t('scheduling.fields.when')}
          </legend>
          <p id={zoneId} className="text-sm text-muted-foreground">
            {t('scheduling.zone', { zone: timeZone })}
          </p>
          {onSubmitSeries && !ownerKind ? (
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
              cohortDates
                ? t('scheduling.create.owner.dateHelp', cohortDates)
                : undefined,
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
            {
              describedBy: unsuggested ? unsuggestedId : undefined,
              after: (
                <>
                  {unsuggested ? (
                    <p
                      id={unsuggestedId}
                      className="border-s-4 border-chart-2 ps-2 text-sm break-words"
                    >
                      {t('scheduling.tutorSuggestions.notSuggested', {
                        name: tutorName,
                      })}
                    </p>
                  ) : null}
                  {availabilityLine}
                </>
              ),
            },
          )}
          {select(
            'classroom',
            t('scheduling.fields.classroom'),
            values.classroomId,
            (value) => change('classroomId', value),
            <>
              <option value="">{t('scheduling.noClassroom')}</option>
              {classrooms.map((room) => {
                const capacity = ownerKind ? capacities?.[room.id] : undefined;
                return (
                  <option key={room.id} value={room.id}>
                    {capacity === undefined
                      ? room.name
                      : t('scheduling.create.owner.roomOption', {
                          name: room.name,
                          capacity,
                        })}
                  </option>
                );
              })}
            </>,
            {
              describedBy: roomTooSmall ? roomWarningId : undefined,
              after:
                roomTooSmall && summary ? (
                  <p
                    id={roomWarningId}
                    className="border-s-4 border-chart-2 ps-2 text-sm break-words"
                  >
                    {t('scheduling.create.owner.roomTooSmall', {
                      count: summary.students.length,
                      capacity: roomCapacity,
                    })}
                  </p>
                ) : null,
            },
          )}
        </div>
        {onFindFreeTimes && !seriesMode && !ownerKind && values.tutorId ? (
          <div className="flex flex-col gap-3">
            <div>
              <Button
                type="button"
                variant="outline"
                disabled={
                  !canFindFreeTimes(values) ||
                  shownFreeTimes?.status === 'loading'
                }
                onClick={findFreeTimes}
              >
                {t('scheduling.freeTimes.button')}
              </Button>
            </div>
            {shownFreeTimes ? (
              <SuggestedSlots
                state={shownFreeTimes}
                timeZone={timeZone}
                onChoose={chooseFreeTime}
                heading={t('scheduling.freeTimes.heading', {
                  name: tutorName,
                })}
                intro={t('scheduling.freeTimes.intro')}
                emptyText={t('scheduling.freeTimes.empty', {
                  name: tutorName,
                })}
              />
            ) : null}
          </div>
        ) : null}
        {tutorSuggestions ? (
          <TutorSuggestions
            state={tutorSuggestions}
            onChoose={chooseTutor}
            chosenId={values.tutorId}
            summary={suggestionSummary}
            subjectName={subjectName}
            emptyLink={assignSubjectsLink}
            series={seriesMode}
            collapse
          />
        ) : null}
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
