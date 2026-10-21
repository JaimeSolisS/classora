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
  validateOffer,
  type OfferErrors,
  type OfferField,
  type OfferMessage,
  type OfferValues,
} from './rules';

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

export interface OfferPanelProps {
  tutors: readonly Option[];
  timeZone: string;
  canOverride: boolean;
  onSuggest: () => Promise<TutorSuggestionList>;
  onSubmit: (
    values: OfferValues,
    override?: SessionOverride,
  ) => Promise<AssignmentOfferCreated>;
  /** After a 201 (the page refreshes the session). */
  onOffered: (created: AssignmentOfferCreated) => void;
  /** Stories. */
  initialValues?: OfferValues;
  initialConflicts?: SchedulingConflict[];
}

export function OfferPanel({
  tutors,
  timeZone,
  canOverride,
  onSuggest,
  onSubmit,
  onOffered,
  initialValues = EMPTY_OFFER,
  initialConflicts,
}: OfferPanelProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const headingId = `${id}-heading`;
  const zoneId = `${id}-zone`;
  const [values, setValues] = useState<OfferValues>(initialValues);
  const [errors, setErrors] = useState<OfferErrors>({});
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(
    initialConflicts ?? null,
  );
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
  const errorId = (field: OfferField) => `${id}-${field}-error`;

  function message(key: OfferMessage | undefined): string | undefined {
    if (!key) {
      return undefined;
    }
    return key.startsWith('errors:')
      ? tErrors(key.slice('errors:'.length) as 'validation_error')
      : t(key as 'scheduling.offer.validation.tutorRequired');
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

  function handleFailure(caught: unknown) {
    const list = conflictsOf(caught);
    if (list) {
      setConflicts(list);
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
    const first = (['tutor', 'deadline'] as const).find((f) => found[f]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  async function submit() {
    setConflicts(null);
    setFailure(null);
    setAnnouncement('');
    const found = validateOffer(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    setSubmitting(true);
    try {
      onOffered(await onSubmit(values));
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
        {t('scheduling.offer.heading')}
      </h2>
      <p className="text-sm text-muted-foreground">
        {t('scheduling.offer.intro')}
      </p>
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
                {t('scheduling.offer.overrideAction')}
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
              title: t('scheduling.offer.overrideAction'),
              description: t('scheduling.offer.overrideDescription'),
              submit: t('scheduling.offer.overrideAction'),
              submitting: t('scheduling.offer.submitting'),
            }}
          />
        </div>
      ) : null}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        aria-labelledby={headingId}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('tutor')} className="text-sm font-medium">
            {t('scheduling.fields.tutor')}
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
            <option value="">{t('scheduling.offer.chooseTutor')}</option>
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
              ? t('scheduling.offer.submitting')
              : t('scheduling.offer.submit')}
          </Button>
        </div>
      </form>
    </section>
  );
}
