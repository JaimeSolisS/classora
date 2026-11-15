import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useId, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import type { EligibleAuthor, ScheduleCadence, ScheduleCreate } from './api';
import {
  classifyScheduleError,
  firstPeriodOptions,
  scheduleBody,
  scheduleFormSchema,
  type ScheduleField,
  type ScheduleFormValues,
} from './scheduleRules';
import { monthLabel, useReportMessage } from './text';

// "Add schedule" (#316 E3): Tutor (the tutors who currently teach the
// student, S1's eligible_authors), Cadence (Weekly / Monthly), Subject ("All
// subjects" or one the chosen tutor teaches the student; reset when the tutor
// changes) and First period (the current week or month and the next 11, in
// the center's calendar from the injected `today`; reset when the cadence
// changes). React Hook Form + Zod. The server's field errors land on their
// field (focus on the first); an existing schedule and any other refusal
// show in the dialog's alert region, which takes focus. The panel closes the
// dialog and moves focus once `onAdd` resolves (after its refetch); Cancel
// and Escape leave focus to the panel too (the trigger).

const CADENCES: readonly ScheduleCadence[] = ['weekly', 'monthly'];

type FormFields = ScheduleFormValues;

const FORM_FIELD: Record<ScheduleField, keyof FormFields> = {
  author_user_id: 'author',
  subject_id: 'subject',
  starts_on: 'firstPeriod',
};

export interface ReportScheduleDialogProps {
  open: boolean;
  today: string;
  authors: readonly EligibleAuthor[];
  /** Resolves once the schedule is added (and the panel refetched). */
  onAdd: (body: ScheduleCreate) => Promise<void>;
  onCancel: () => void;
}

export function ReportScheduleDialog({
  open,
  ...props
}: ReportScheduleDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          props.onCancel();
        }
      }}
    >
      {open ? <AddContent {...props} sendingRef={sending} /> : null}
    </Dialog>
  );
}

type Failure =
  | { kind: 'exists'; cadence: ScheduleCadence }
  | { kind: 'other'; error: unknown };

function AddContent({
  today,
  authors,
  onAdd,
  onCancel,
  sendingRef,
}: Omit<ReportScheduleDialogProps, 'open'> & {
  sendingRef: { current: boolean };
}) {
  const { t, i18n } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const language = toLanguage(i18n.resolvedLanguage);
  const message = useReportMessage();
  const id = useId();
  const failureRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const {
    register,
    handleSubmit,
    control,
    setValue,
    setError,
    formState: { errors },
  } = useForm<FormFields>({
    resolver: zodResolver(scheduleFormSchema),
    defaultValues: {
      author: '',
      cadence: 'weekly',
      subject: '',
      firstPeriod: firstPeriodOptions('weekly', today)[0],
    },
    shouldFocusError: true,
  });
  const author = useWatch({ control, name: 'author' });
  const cadence = useWatch({ control, name: 'cadence' });
  const subjects = authors.find((a) => a.user_id === author)?.subjects ?? [];
  const periods = firstPeriodOptions(cadence, today);

  useEffect(() => {
    if (!failure) {
      return;
    }
    const timer = window.setTimeout(() => failureRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [failure]);

  async function send(values: FormFields) {
    if (sendingRef.current) {
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    setFailure(null);
    try {
      await onAdd(scheduleBody(values));
    } catch (caught) {
      const refusal = classifyScheduleError(caught);
      if (refusal.kind === 'fields') {
        let first = true;
        for (const [field, text] of Object.entries(refusal.errors)) {
          setError(
            FORM_FIELD[field as ScheduleField],
            { type: 'server', message: text },
            { shouldFocus: first },
          );
          first = false;
        }
      } else if (refusal.kind === 'exists') {
        setFailure({ kind: 'exists', cadence: values.cadence });
      } else {
        setFailure({ kind: 'other', error: caught });
      }
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }

  const fieldMessage = (name: keyof FormFields) =>
    message(errors[name]?.message);
  const describedBy = (name: keyof FormFields) =>
    fieldMessage(name) ? `${id}-${name}-error` : undefined;
  const authorField = register('author', {
    onChange: () => setValue('subject', ''),
  });
  const cadenceField = register('cadence', {
    onChange: (event: { target: { value: string } }) =>
      setValue(
        'firstPeriod',
        firstPeriodOptions(event.target.value as ScheduleCadence, today)[0]!,
      ),
  });

  return (
    <DialogContent
      className="sm:max-w-lg"
      onCloseAutoFocus={(event) => event.preventDefault()}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('progressReports.schedule.addTitle')}</DialogTitle>
        <DialogDescription>
          {t('progressReports.schedule.addDescription')}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={(event) => void handleSubmit(send)(event)}
        className="flex min-w-0 flex-col gap-4"
      >
        {failure ? (
          <div ref={failureRef} tabIndex={-1} className="outline-none">
            {failure.kind === 'exists' ? (
              <p
                role="alert"
                className="rounded-md border border-destructive p-3 text-sm font-medium text-destructive"
              >
                {t('progressReports.schedule.exists', {
                  cadence: t(
                    `progressReports.schedule.cadenceLower.${failure.cadence}`,
                  ),
                })}
              </p>
            ) : (
              <ErrorAlert error={failure.error} />
            )}
          </div>
        ) : null}
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-author`} className="text-sm font-medium">
            {t('progressReports.schedule.tutor')}
          </label>
          <select
            id={`${id}-author`}
            className={SELECT_CLASS}
            disabled={busy}
            aria-invalid={fieldMessage('author') ? true : undefined}
            aria-describedby={describedBy('author')}
            {...authorField}
          >
            <option value="">
              {t('progressReports.schedule.chooseTutor')}
            </option>
            {authors.map((option) => (
              <option key={option.user_id} value={option.user_id}>
                {option.display_name}
              </option>
            ))}
          </select>
          <FieldError
            id={`${id}-author-error`}
            message={fieldMessage('author')}
          />
        </div>
        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">
            {t('progressReports.schedule.cadence')}
          </legend>
          <div className="flex flex-wrap gap-4">
            {CADENCES.map((value) => (
              <label
                key={value}
                className="inline-flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  type="radio"
                  value={value}
                  disabled={busy}
                  className="size-4"
                  {...cadenceField}
                />
                {t(`progressReports.kinds.${value}`)}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-subject`} className="text-sm font-medium">
            {t('progressReports.schedule.subject')}
          </label>
          <select
            id={`${id}-subject`}
            className={SELECT_CLASS}
            disabled={busy}
            aria-invalid={fieldMessage('subject') ? true : undefined}
            aria-describedby={describedBy('subject')}
            {...register('subject')}
          >
            <option value="">
              {t('progressReports.schedule.allSubjects')}
            </option>
            {subjects.map((subject) => (
              <option key={subject.id} value={subject.id}>
                {subject.name}
              </option>
            ))}
          </select>
          <FieldError
            id={`${id}-subject-error`}
            message={fieldMessage('subject')}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-firstPeriod`} className="text-sm font-medium">
            {t('progressReports.schedule.firstPeriod')}
          </label>
          <select
            id={`${id}-firstPeriod`}
            className={SELECT_CLASS}
            disabled={busy}
            aria-invalid={fieldMessage('firstPeriod') ? true : undefined}
            aria-describedby={describedBy('firstPeriod')}
            {...register('firstPeriod')}
          >
            {periods.map((start) => (
              <option key={start} value={start}>
                {cadence === 'weekly'
                  ? t('progressReports.schedule.weekOf', {
                      date: formatLocalDate(start),
                    })
                  : monthLabel(start, language)}
              </option>
            ))}
          </select>
          <FieldError
            id={`${id}-firstPeriod-error`}
            message={fieldMessage('firstPeriod')}
          />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onCancel}
          >
            {t('progressReports.cancel')}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner label={t('progressReports.schedule.adding')} showLabel />
            ) : (
              t('progressReports.schedule.add')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
