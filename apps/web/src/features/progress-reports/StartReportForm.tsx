import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import {
  REPORT_KINDS,
  reportPath,
  type ReportCreate,
  type ReportKind,
} from './api';
import {
  classifyStartError,
  lastWeek,
  monthOptions,
  START_FIELD_ORDER,
  startBody,
  startErrors,
  startValues,
  thisWeek,
  weekEnd,
  weekStart,
  type StartErrors,
  type StartField,
  type StartValues,
} from './rules';
import { monthLabel, useReportMessage } from './text';

// The start step of a report (#315 B; E3), phone-first: Report type
// (Weekly / Monthly / On demand, default On demand), the period (weekly:
// any day of the week, snapped to its Monday, with "This week" / "Last
// week"; monthly: this month and the 12 before it; on demand: From and To,
// by default the last 7 days), all in the center's calendar, and the
// optional subject (the subjects the caller teaches the student, AM2).
// "Start report" sends one POST with no narrative; the page navigates.
// The choices are kept after any error. Our own checks send nothing; each
// error lands on its field (focus on the first); an existing weekly or
// monthly report shows "Open it"; anything else the failure region.

export type StartFailure =
  | { kind: 'exists'; reportId: string | null; error: unknown }
  | { kind: 'other'; error: unknown };

export interface StartReportFormProps {
  studentId: string;
  today: string;
  /** The subjects offered, labelled (with their level when known). */
  subjects: readonly { id: string; label: string }[];
  onStart: (body: ReportCreate) => Promise<void>;
  /** Stories: the first values and errors. */
  initialValues?: Partial<StartValues>;
  initialErrors?: StartErrors;
  initialFailure?: StartFailure | null;
}

const KIND_LABELS = {
  weekly: 'progressReports.kinds.weekly',
  monthly: 'progressReports.kinds.monthly',
  on_demand: 'progressReports.kinds.on_demand',
} as const;

export function StartReportForm({
  studentId,
  today,
  subjects,
  onStart,
  initialValues,
  initialErrors = {},
  initialFailure = null,
}: StartReportFormProps) {
  const { t, i18n } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const language = toLanguage(i18n.resolvedLanguage);
  const message = useReportMessage();
  const id = useId();
  const [values, setValues] = useState<StartValues>(() => ({
    ...startValues(today),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<StartErrors>(initialErrors);
  const [failure, setFailure] = useState<StartFailure | null>(initialFailure);
  const [busy, setBusy] = useState(false);
  const failureRef = useRef<HTMLDivElement>(null);
  const [focus, setFocus] = useState<{
    n: number;
    to: StartField | 'failure';
  } | null>(null);

  useEffect(() => {
    if (!focus) {
      return;
    }
    const timer = window.setTimeout(() => {
      const target =
        focus.to === 'failure'
          ? failureRef.current
          : document.getElementById(`${id}-${focus.to}`);
      target?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focus, id]);

  function focusNext(to: StartField | 'failure') {
    setFocus((current) => ({ n: (current?.n ?? 0) + 1, to }));
  }

  function set<K extends keyof StartValues>(key: K, value: StartValues[K]) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function showErrors(found: StartErrors) {
    setErrors(found);
    const first = START_FIELD_ORDER.find((field) => found[field]);
    if (first) {
      focusNext(first);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    setFailure(null);
    const found = startErrors(values, today);
    if (Object.keys(found).length > 0) {
      showErrors(found);
      return;
    }
    setErrors({});
    setBusy(true);
    try {
      await onStart(startBody(values, studentId));
    } catch (caught) {
      const refusal = classifyStartError(caught, values.kind);
      if (refusal.kind === 'fields') {
        showErrors(refusal.errors);
      } else if (refusal.kind === 'exists') {
        setFailure({
          kind: 'exists',
          reportId: refusal.reportId,
          error: caught,
        });
        focusNext('failure');
      } else if (refusal.kind !== 'missing') {
        setFailure({ kind: 'other', error: caught });
        focusNext('failure');
      }
    } finally {
      setBusy(false);
    }
  }

  const fieldProps = (field: StartField) => {
    const text = message(errors[field]);
    return {
      id: `${id}-${field}`,
      'aria-invalid': text ? true : undefined,
      'aria-describedby': text ? `${id}-${field}-error` : undefined,
    } as const;
  };
  const fieldError = (field: StartField) => (
    <FieldError id={`${id}-${field}-error`} message={message(errors[field])} />
  );

  let weekText: string | null = null;
  if (values.kind === 'weekly' && /^\d{4}-\d{2}-\d{2}$/.test(values.weekDate)) {
    try {
      const monday = weekStart(values.weekDate);
      weekText = t('progressReports.period.week', {
        start: formatLocalDate(monday),
        end: formatLocalDate(weekEnd(monday)),
      });
    } catch {
      weekText = null;
    }
  }

  return (
    <form
      method="post"
      noValidate
      onSubmit={(event) => void submit(event)}
      className="flex w-full max-w-xl min-w-0 flex-col gap-6"
    >
      <fieldset className="flex min-w-0 flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">
          {t('progressReports.start.kind')}
        </legend>
        {REPORT_KINDS.map((kind: ReportKind) => (
          <label key={kind} className="flex min-h-6 items-center gap-2 text-sm">
            <input
              type="radio"
              name={`${id}-kind`}
              value={kind}
              checked={values.kind === kind}
              onChange={() => {
                set('kind', kind);
                setErrors({});
              }}
              className="size-4"
            />
            {t(KIND_LABELS[kind])}
          </label>
        ))}
      </fieldset>

      {values.kind === 'weekly' ? (
        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={`${id}-week`} className="text-sm font-medium">
            {t('progressReports.start.weekDay')}
          </label>
          <Input
            type="date"
            className="w-full max-w-xs"
            value={values.weekDate}
            onChange={(event) => set('weekDate', event.target.value)}
            {...fieldProps('week')}
          />
          {fieldError('week')}
          {weekText ? <p className="text-sm">{weekText}</p> : null}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => set('weekDate', thisWeek(today))}
            >
              {t('progressReports.start.thisWeek')}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => set('weekDate', lastWeek(today))}
            >
              {t('progressReports.start.lastWeek')}
            </Button>
          </div>
        </div>
      ) : values.kind === 'monthly' ? (
        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={`${id}-month`} className="text-sm font-medium">
            {t('progressReports.start.month')}
          </label>
          <select
            className={`${SELECT_CLASS} w-full max-w-xs`}
            value={values.month}
            onChange={(event) => set('month', event.target.value)}
            {...fieldProps('month')}
          >
            {monthOptions(today).map((month) => (
              <option key={month} value={month}>
                {monthLabel(month, language)}
              </option>
            ))}
          </select>
          {fieldError('month')}
        </div>
      ) : (
        <div className="flex min-w-0 flex-wrap gap-4">
          <div className="flex min-w-0 flex-col gap-1.5">
            <label htmlFor={`${id}-from`} className="text-sm font-medium">
              {t('progressReports.start.from')}
            </label>
            <Input
              type="date"
              max={today}
              className="w-full max-w-xs"
              value={values.from}
              onChange={(event) => set('from', event.target.value)}
              {...fieldProps('from')}
            />
            {fieldError('from')}
          </div>
          <div className="flex min-w-0 flex-col gap-1.5">
            <label htmlFor={`${id}-to`} className="text-sm font-medium">
              {t('progressReports.start.to')}
            </label>
            <Input
              type="date"
              min={values.from || undefined}
              className="w-full max-w-xs"
              value={values.to}
              onChange={(event) => set('to', event.target.value)}
              {...fieldProps('to')}
            />
            {fieldError('to')}
          </div>
        </div>
      )}
      <p className="text-sm text-muted-foreground">
        {t('progressReports.start.immutable')}
      </p>

      <div className="flex min-w-0 flex-col gap-1.5">
        <label htmlFor={`${id}-subject`} className="text-sm font-medium">
          {t('progressReports.start.subject')}
        </label>
        <select
          className={`${SELECT_CLASS} w-full max-w-xs`}
          value={values.subjectId}
          onChange={(event) => set('subjectId', event.target.value)}
          {...fieldProps('subject')}
        >
          <option value="">{t('progressReports.allSubjects')}</option>
          {subjects.map((subject) => (
            <option key={subject.id} value={subject.id}>
              {subject.label}
            </option>
          ))}
        </select>
        {fieldError('subject')}
      </div>

      {failure ? (
        <div ref={failureRef} tabIndex={-1} className="min-w-0 outline-none">
          {failure.kind === 'exists' ? (
            <div className="flex min-w-0 flex-col gap-2">
              <ErrorAlert
                error={failure.error}
                message={t('progressReports.start.exists')}
              />
              {failure.reportId ? (
                <Link
                  to={reportPath(failure.reportId)}
                  className={`${SMALL_LINK_CLASS} self-start`}
                >
                  {t('progressReports.start.openExisting')}
                </Link>
              ) : null}
            </div>
          ) : (
            <ErrorAlert error={failure.error} />
          )}
        </div>
      ) : null}

      <Button type="submit" className="self-start" disabled={busy}>
        {busy ? (
          <Spinner label={t('progressReports.busy.starting')} showLabel />
        ) : (
          t('progressReports.start.submit')
        )}
      </Button>
    </form>
  );
}
