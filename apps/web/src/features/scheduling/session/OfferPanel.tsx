import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import {
  conflictsOf,
  type Option,
  type SchedulingConflict,
  type TutorSuggestion,
  type TutorSuggestionList,
} from '../create/api';
import { ConflictPanel } from '../create/ConflictPanel';
import { OverrideDialog } from '../create/OverrideDialog';
import {
  overrideAction,
  overrideReasonError,
  sessionOverride,
  type SessionOverride,
} from '../create/rules';
import {
  TutorSuggestions,
  type TutorSuggestionsState,
} from '../create/TutorSuggestions';
import type { AssignmentOfferCreated } from './api';
import {
  EMPTY_OFFER,
  offerServerErrors,
  REASON_MAX_LENGTH,
  reasonLength,
  validateOffer,
  type OfferErrors,
  type OfferField,
  type OfferMessage,
  type OfferScope,
  type OfferValues,
} from './rules';
import { SeriesConflictList } from './SeriesConflictList';

// "Offer to a tutor" (#52 H2) on the session page, for holders of
// schedule.assign while the session is unassigned. Without any API call:
// the page passes the tutor options, `onSuggest` (the #51 replacement
// tutors; "Choose" sets the picker, never submits) and `onSubmit` (resolves
// with the offer and its workload warnings, or rejects with the error). An
// optional earlier deadline is a date and time in the center zone (the zone
// is shown). A 409 scheduling_conflict shows the #48 conflict panel and,
// for a holder of schedule.override with only soft conflicts, the #49
// override dialog ("Override and offer"); a 422 goes on the fields; any
// other error (version_conflict, class_session_not_offerable) is an
// ErrorAlert.
//
// For an unassigned occurrence of a series without a tutor (#233 G2) the
// page passes `series`: the panel then offers "This session only" (the
// route above) or "This and following sessions" (the series from this
// occurrence's date). The series choice never has an override (E3): its 409
// lists the conflicts grouped by local date. With `series.only` (#243 D4,
// a cohort's series) the panel offers the series alone, no scope choice.
//
// `variant="substitute"` (#55 H1) is "Find a substitute" for a confirmed
// session: the same picker, suggestions, deadline, conflict panel and
// override dialog, plus a required reason (1-1000 characters, with a
// counter), with its own labels; the session stays with its tutor until the
// substitute accepts.
//
// `variant="confirm"` (#60 H1) confirms a family's session request on its
// review page: the same picker, suggestions (the #51 tutors for the
// request's slot), deadline, conflict panel and override dialog, plus an
// optional classroom (`classrooms`), with its own labels. A 422 on the
// student or the subject (inactive since the request) shows its message.

export interface OfferPanelProps<T = AssignmentOfferCreated> {
  tutors: readonly Option[];
  timeZone: string;
  canOverride: boolean;
  onSuggest: () => Promise<TutorSuggestionList>;
  onSubmit: (values: OfferValues, override?: SessionOverride) => Promise<T>;
  /** After a 201 (the page refreshes the session). */
  onOffered: (created: T) => void;
  /** The series choice (#233 G2), for an occurrence of a tutorless series. */
  series?: {
    onSubmit: (values: OfferValues) => Promise<T>;
    /**
     * Only the series, without "This session only" (#243 D4: a cohort's
     * series), with its own help line and "can't override" text.
     */
    only?: { help: string; noOverride: string; submit: string };
  };
  /** "offer" (#52), "substitute" (#55) or "confirm" (#60). */
  variant?: 'offer' | 'substitute' | 'confirm';
  /** The classroom choice (#60: the request confirmation). */
  classrooms?: readonly Option[];
  /** Stories. */
  initialValues?: OfferValues;
  initialConflicts?: SchedulingConflict[];
  initialScope?: OfferScope;
  initialSeriesConflicts?: SchedulingConflict[];
}

export function OfferPanel<T = AssignmentOfferCreated>({
  tutors,
  timeZone,
  canOverride,
  onSuggest,
  onSubmit,
  onOffered,
  series,
  variant = 'offer',
  classrooms,
  initialValues = EMPTY_OFFER,
  initialConflicts,
  initialScope = 'session',
  initialSeriesConflicts,
}: OfferPanelProps<T>) {
  const { t } = useTranslation();
  const substitute = variant === 'substitute';
  const labels =
    variant === 'confirm'
      ? {
          heading: t('scheduling.requestReview.confirm.heading'),
          intro: t('scheduling.requestReview.confirm.intro'),
          chooseTutor: t('scheduling.offer.chooseTutor'),
          tutor: t('scheduling.fields.tutor'),
          submit: t('scheduling.requestReview.confirm.submit'),
          submitting: t('scheduling.requestReview.confirm.submitting'),
          overrideAction: t('scheduling.requestReview.confirm.overrideAction'),
          overrideDescription: t(
            'scheduling.requestReview.confirm.overrideDescription',
          ),
        }
      : substitute
        ? {
            heading: t('scheduling.substitution.heading'),
            intro: t('scheduling.substitution.intro'),
            chooseTutor: t('scheduling.substitution.chooseTutor'),
            tutor: t('scheduling.substitution.tutor'),
            submit: t('scheduling.substitution.submit'),
            submitting: t('scheduling.substitution.submitting'),
            overrideAction: t('scheduling.substitution.overrideAction'),
            overrideDescription: t(
              'scheduling.substitution.overrideDescription',
            ),
          }
        : {
            heading: t('scheduling.offer.heading'),
            intro: t('scheduling.offer.intro'),
            chooseTutor: t('scheduling.offer.chooseTutor'),
            tutor: t('scheduling.fields.tutor'),
            submit: t('scheduling.offer.submit'),
            submitting: t('scheduling.offer.submitting'),
            overrideAction: t('scheduling.offer.overrideAction'),
            overrideDescription: t('scheduling.offer.overrideDescription'),
          };
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const headingId = `${id}-heading`;
  const zoneId = `${id}-zone`;
  const [values, setValues] = useState<OfferValues>(initialValues);
  const [errors, setErrors] = useState<OfferErrors>({});
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(
    initialConflicts ?? null,
  );
  const [scope, setScope] = useState<OfferScope>(
    series?.only ? 'series' : series ? initialScope : 'session',
  );
  const [seriesConflicts, setSeriesConflicts] = useState<
    SchedulingConflict[] | null
  >(initialSeriesConflicts ?? null);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [overriding, setOverriding] = useState(false);
  const [suggestions, setSuggestions] = useState<TutorSuggestionsState | null>(
    null,
  );
  const [announcement, setAnnouncement] = useState('');
  const inFlight = useRef(false);
  const suggestRequest = useRef(0);
  const overrideButton = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const focusAfterDialog = useRef<'button' | 'panel'>('button');

  const fieldId = (field: OfferField | 'deadlineTime') => `${id}-${field}`;
  const counterId = `${id}-reason-counter`;
  const errorId = (field: OfferField) => `${id}-${field}-error`;

  function message(key: OfferMessage | undefined): string | undefined {
    if (!key) {
      return undefined;
    }
    return key.startsWith('errors:')
      ? tErrors(key.slice('errors:'.length) as 'validation_error')
      : t(key as 'scheduling.offer.validation.tutorRequired');
  }

  /** A 422 on a reference the form doesn't show (#60: the request's student
   * or subject became inactive): its own message. */
  function referenceMessage(error: unknown): string | undefined {
    if (!(error instanceof ApiError)) {
      return undefined;
    }
    const found = error.fieldErrors.find(
      ({ code }) => code === 'student_inactive' || code === 'subject_inactive',
    );
    return found
      ? tErrors(found.code as 'student_inactive' | 'subject_inactive')
      : undefined;
  }

  function suggest() {
    suggestRequest.current += 1;
    const request = suggestRequest.current;
    setSuggestions({ status: 'loading' });
    onSuggest().then(
      (result) => {
        if (request === suggestRequest.current) {
          setSuggestions({ status: 'loaded', result });
        }
      },
      (error: unknown) => {
        if (request === suggestRequest.current) {
          setSuggestions({ status: 'error', error });
        }
      },
    );
  }

  function choose(suggestion: TutorSuggestion) {
    setValues((current) => ({ ...current, tutorId: suggestion.tutor.id }));
    suggestRequest.current += 1;
    setSuggestions(null);
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

  function handleFailure(caught: unknown, forSeries = false) {
    const list = conflictsOf(caught);
    if (list) {
      if (forSeries) {
        setSeriesConflicts(list);
      } else {
        setConflicts(list);
      }
      return;
    }
    if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
      const mapped = offerServerErrors(caught.fieldErrors);
      if (Object.keys(mapped).length > 0) {
        setErrors(mapped);
        focusFirst(mapped);
        return;
      }
    }
    setFailure(caught);
  }

  function focusFirst(found: OfferErrors) {
    const first = (['tutor', 'classroom', 'reason', 'deadline'] as const).find(
      (f) => found[f],
    );
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  async function submit() {
    setConflicts(null);
    setSeriesConflicts(null);
    setFailure(null);
    setAnnouncement('');
    const found = validateOffer(values, { requireReason: substitute });
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    setSubmitting(true);
    const forSeries = scope === 'series' && series !== undefined;
    try {
      onOffered(await (forSeries ? series.onSubmit(values) : onSubmit(values)));
    } catch (caught) {
      handleFailure(caught, forSeries);
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

  async function confirmOverride(reason: string) {
    if (!conflicts) {
      return;
    }
    try {
      const created = await onSubmit(
        values,
        sessionOverride(conflicts, reason),
      );
      setOverriding(false);
      setConflicts(null);
      onOffered(created);
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

  const describedBy = (field: OfferField, extra?: string) =>
    [extra, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  const action = conflicts ? overrideAction(conflicts, canOverride) : null;

  return (
    <section
      aria-labelledby={headingId}
      className="flex w-full max-w-2xl min-w-0 flex-col gap-4 rounded-md border border-border p-4"
    >
      <h2 id={headingId} className="text-lg font-semibold">
        {labels.heading}
      </h2>
      <p className="text-sm text-muted-foreground">{labels.intro}</p>
      {seriesConflicts ? (
        <SeriesConflictList
          conflicts={seriesConflicts}
          noOverride={series?.only?.noOverride}
        />
      ) : null}
      {conflicts ? (
        <div ref={panel} className="flex flex-col gap-3">
          {variant === 'confirm' ? (
            <ConflictPanel
              conflicts={conflicts}
              heading={t('scheduling.requestReview.confirm.conflictsHeading', {
                count: conflicts.length,
              })}
              hint={t('scheduling.requestReview.confirm.conflictsHint')}
            />
          ) : (
            <ConflictPanel conflicts={conflicts} />
          )}
          {action === 'override' ? (
            <div>
              <Button
                ref={overrideButton}
                type="button"
                onClick={() => setOverriding(true)}
              >
                {labels.overrideAction}
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
            labels={{
              title: labels.overrideAction,
              description: labels.overrideDescription,
              submit: labels.overrideAction,
              submitting: labels.submitting,
            }}
          />
        </div>
      ) : null}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {failure ? (
        <ErrorAlert error={failure} message={referenceMessage(failure)} />
      ) : null}
      <form
        method="post"
        noValidate
        aria-labelledby={headingId}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        {series?.only ? (
          <p className="text-sm text-muted-foreground">{series.only.help}</p>
        ) : series ? (
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">
              {t('scheduling.offer.series.scope')}
            </legend>
            {(['session', 'series'] as const).map((value) => (
              <label
                key={value}
                className="flex min-h-9 items-center gap-2 text-sm"
              >
                <input
                  type="radio"
                  name={`${id}-scope`}
                  value={value}
                  checked={scope === value}
                  onChange={() => {
                    setScope(value);
                    setConflicts(null);
                    setSeriesConflicts(null);
                    setFailure(null);
                  }}
                  className="size-4 accent-foreground"
                />
                {t(`scheduling.offer.series.${value}`)}
              </label>
            ))}
            {scope === 'series' ? (
              <p className="text-sm text-muted-foreground">
                {t('scheduling.offer.series.help')}
              </p>
            ) : null}
          </fieldset>
        ) : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('tutor')} className="text-sm font-medium">
            {labels.tutor}
          </label>
          <select
            id={fieldId('tutor')}
            className={SELECT_CLASS}
            value={values.tutorId}
            aria-invalid={errors.tutor ? true : undefined}
            aria-describedby={describedBy('tutor')}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                tutorId: event.target.value,
              }))
            }
          >
            <option value="">{labels.chooseTutor}</option>
            {tutors.map((tutor) => (
              <option key={tutor.id} value={tutor.id}>
                {tutor.name}
              </option>
            ))}
          </select>
          <FieldError id={errorId('tutor')} message={message(errors.tutor)} />
        </div>
        <div className="flex flex-col gap-3">
          <div>
            <Button
              type="button"
              variant="outline"
              disabled={suggestions?.status === 'loading'}
              onClick={suggest}
            >
              {t('scheduling.tutorSuggestions.button')}
            </Button>
          </div>
          {suggestions ? (
            <TutorSuggestions state={suggestions} onChoose={choose} />
          ) : null}
        </div>
        {classrooms ? (
          <div className="flex flex-col gap-2">
            <label
              htmlFor={fieldId('classroom')}
              className="text-sm font-medium"
            >
              {t('scheduling.fields.classroom')}
            </label>
            <select
              id={fieldId('classroom')}
              className={SELECT_CLASS}
              value={values.classroomId ?? ''}
              aria-invalid={errors.classroom ? true : undefined}
              aria-describedby={describedBy('classroom')}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  classroomId: event.target.value,
                }))
              }
            >
              <option value="">{t('scheduling.noClassroom')}</option>
              {classrooms.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.name}
                </option>
              ))}
            </select>
            <FieldError
              id={errorId('classroom')}
              message={message(errors.classroom)}
            />
          </div>
        ) : null}
        {substitute ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={fieldId('reason')} className="text-sm font-medium">
              {t('scheduling.substitution.reason')}
            </label>
            <textarea
              id={fieldId('reason')}
              rows={3}
              required
              value={values.reason ?? ''}
              aria-invalid={errors.reason ? true : undefined}
              aria-describedby={describedBy('reason', counterId)}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  reason: event.target.value,
                }))
              }
              className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
            />
            <FieldError
              id={errorId('reason')}
              message={message(errors.reason)}
            />
            <p
              id={counterId}
              className={
                reasonLength(values.reason ?? '') > REASON_MAX_LENGTH
                  ? 'text-sm text-destructive'
                  : 'text-sm text-muted-foreground'
              }
            >
              {t('scheduling.substitution.counter', {
                length: reasonLength(values.reason ?? ''),
                max: REASON_MAX_LENGTH,
              })}
            </p>
          </div>
        ) : null}
        <fieldset className="flex flex-col gap-3">
          <legend className="text-sm font-medium">
            {t('scheduling.offer.deadline')}
          </legend>
          <p id={zoneId} className="text-sm text-muted-foreground">
            {t('scheduling.offer.deadlineHelp', { zone: timeZone })}
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label
                htmlFor={fieldId('deadline')}
                className="text-sm font-medium"
              >
                {t('scheduling.offer.deadlineDate')}
              </label>
              <Input
                id={fieldId('deadline')}
                type="date"
                value={values.deadlineDate}
                aria-invalid={errors.deadline ? true : undefined}
                aria-describedby={describedBy('deadline', zoneId)}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    deadlineDate: event.target.value,
                  }))
                }
              />
            </div>
            <div className="flex flex-col gap-2">
              <label
                htmlFor={fieldId('deadlineTime')}
                className="text-sm font-medium"
              >
                {t('scheduling.offer.deadlineTime')}
              </label>
              <Input
                id={fieldId('deadlineTime')}
                type="time"
                step={60}
                value={values.deadlineTime}
                aria-invalid={errors.deadline ? true : undefined}
                aria-describedby={describedBy('deadline', zoneId)}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    deadlineTime: event.target.value,
                  }))
                }
              />
            </div>
          </div>
          <FieldError
            id={errorId('deadline')}
            message={message(errors.deadline)}
          />
        </fieldset>
        <div>
          <Button type="submit" disabled={submitting}>
            {submitting ? <Spinner /> : null}
            {submitting
              ? labels.submitting
              : (series?.only?.submit ?? labels.submit)}
          </Button>
        </div>
      </form>
    </section>
  );
}
