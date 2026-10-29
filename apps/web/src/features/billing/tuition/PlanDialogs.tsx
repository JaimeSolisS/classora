import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { SELECT_CLASS } from '@/features/students/styles';
import type {
  TuitionPauseCreate,
  TuitionPlanDetail,
  TuitionPlanUpdate,
  TuitionResume,
} from './api';
import { useTuitionLabels } from './labels';
import {
  DUE_OFFSET_MAX,
  DUE_OFFSET_MIN,
  dialogServerErrors,
  endChoices,
  openPause,
  pauseAfter,
  pauseEnds,
  pauseStarts,
  resumeStarts,
  validDueOffset,
  type PlanMessage,
} from './rules';

// The plan page's action dialogs (#374 E4), without any API call: Pause
// (from an uncharged upcoming period start, until a later period end or
// until resumed), Resume (from an upcoming period start), End (an upcoming
// period end, or "Remove end date") and the due offset. Each sends the
// plan's `version` as last read. A 409 version_conflict shows the shared
// message and reloads the plan (the dialog stays open and the next submit
// sends the new version); a 422 goes on its field; any other refusal is an
// alert above the form.

const V = 'billing.tuition.validation';

interface ShellProps {
  open: boolean;
  title: string;
  description: string;
  submitLabel: string;
  /** The client checks: false keeps the dialog open without a request. */
  validate: () => boolean;
  /** Runs the change; rejects with the error to show. */
  onSubmit: () => Promise<void>;
  /** A 422 on the dialog's fields: true when it was placed on one. */
  onFieldErrors: (error: unknown) => boolean;
  /** Called once the dialog finds the plan stale (409 version_conflict). */
  onReload: () => void;
  onClose: () => void;
  children: ReactNode;
}

function ActionDialog(props: ShellProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose();
        }
      }}
    >
      {props.open ? <ShellBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

function ShellBody({
  title,
  description,
  submitLabel,
  validate,
  onSubmit,
  onFieldErrors,
  onReload,
  onClose,
  children,
  busyRef,
}: ShellProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    setFailure(null);
    if (!validate()) return;
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit();
    } catch (caught) {
      if (!onFieldErrors(caught)) {
        setFailure(caught);
        if (caught instanceof ApiError && caught.code === 'version_conflict') {
          onReload();
        }
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void submit();
    }
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-lg"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        {children}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.prices.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.prices.saving')} showLabel />
            ) : (
              submitLabel
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

interface FieldProps {
  id: string;
  label: string;
  help?: string;
  error?: PlanMessage;
  children: (describedBy: string | undefined, invalid?: true) => ReactNode;
}

function Field({ id, label, help, error, children }: FieldProps) {
  const message = useFieldMessage();
  const describedBy =
    [help ? `${id}-help` : undefined, error ? `${id}-error` : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children(describedBy, error ? true : undefined)}
      {help ? (
        <p id={`${id}-help`} className="text-sm text-muted-foreground">
          {help}
        </p>
      ) : null}
      <FieldError id={`${id}-error`} message={message(error)} />
    </div>
  );
}

export interface PlanDialogProps<B> {
  plan: TuitionPlanDetail;
  open: boolean;
  /** Sends the body (with the plan's version); rejects with the error. */
  onSubmit: (body: B) => Promise<void>;
  onReload: () => void;
  onClose: () => void;
}

// ------------------------------------------------------------------ pause

export function PauseDialog(props: PlanDialogProps<TuitionPauseCreate>) {
  return props.open ? <PauseForm {...props} /> : null;
}

function PauseForm({
  plan,
  open,
  onSubmit,
  onReload,
  onClose,
}: PlanDialogProps<TuitionPauseCreate>) {
  const { t } = useTranslation();
  const labels = useTuitionLabels();
  const id = useId();
  const starts = pauseStarts(plan);
  // "Until resumed" would overlap a later pause (QA O2): then the pause
  // must end before it, and the first end is chosen.
  const defaultEnd = (from: string) =>
    from && pauseAfter(plan, from)
      ? (pauseEnds(plan, from)[0]?.period_end ?? '')
      : '';
  const [start, setStart] = useState(starts[0]?.period_start ?? '');
  const [end, setEnd] = useState(() => defaultEnd(start));
  const [errors, setErrors] = useState<{ start?: string; end?: string }>({});
  const ends = start ? pauseEnds(plan, start) : [];
  const openAllowed = !start || !pauseAfter(plan, start);

  return (
    <ActionDialog
      open={open}
      title={t('billing.tuition.pause.title')}
      description={t('billing.tuition.pause.description')}
      submitLabel={t('billing.tuition.pause.submit')}
      validate={() => {
        const found = !start
          ? { start: `${V}.pauseStartRequired` }
          : !end && !openAllowed
            ? { end: `${V}.endRequired` }
            : {};
        setErrors(found);
        return Object.keys(found).length === 0;
      }}
      onSubmit={async () => {
        await onSubmit({
          version: plan.version,
          starts_on: start,
          ...(end ? { ends_on: end } : {}),
        });
      }}
      onFieldErrors={(error) => {
        const found = dialogServerErrors(
          error,
          { starts_on: 'start', ends_on: 'end' } as const,
          { tuition_pause_invalid: 'start' } as const,
        );
        if (found) setErrors(found);
        return found !== null;
      }}
      onReload={onReload}
      onClose={onClose}
    >
      <Field
        id={`${id}-start`}
        label={t('billing.tuition.pause.from')}
        help={t('billing.tuition.pause.fromHelp')}
        error={errors.start}
      >
        {(describedBy, invalid) => (
          <select
            id={`${id}-start`}
            className={SELECT_CLASS}
            value={start}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => {
              setStart(event.target.value);
              setEnd(defaultEnd(event.target.value));
            }}
          >
            {starts.map((period) => (
              <option key={period.period_start} value={period.period_start}>
                {labels.date(period.period_start)}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field
        id={`${id}-end`}
        label={t('billing.tuition.pause.until')}
        error={errors.end}
      >
        {(describedBy, invalid) => (
          <select
            id={`${id}-end`}
            className={SELECT_CLASS}
            value={end}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => setEnd(event.target.value)}
          >
            {openAllowed ? (
              <option value="">{t('billing.tuition.untilResumed')}</option>
            ) : null}
            {ends.map((period) => (
              <option key={period.period_end} value={period.period_end}>
                {labels.date(period.period_end)}
              </option>
            ))}
          </select>
        )}
      </Field>
    </ActionDialog>
  );
}

// ----------------------------------------------------------------- resume

export function ResumeDialog(props: PlanDialogProps<TuitionResume>) {
  return props.open ? <ResumeForm {...props} /> : null;
}

function ResumeForm({
  plan,
  open,
  onSubmit,
  onReload,
  onClose,
}: PlanDialogProps<TuitionResume>) {
  const { t } = useTranslation();
  const labels = useTuitionLabels();
  const id = useId();
  const starts = resumeStarts(plan);
  const pause = openPause(plan);
  // The first period after the paused ones today, else the first offered.
  const [resumeOn, setResumeOn] = useState(
    starts.find((period) => period.state !== 'paused')?.period_start ??
      starts[0]?.period_start ??
      '',
  );
  const [error, setError] = useState<string | undefined>();

  return (
    <ActionDialog
      open={open}
      title={t('billing.tuition.resume.title')}
      description={
        pause
          ? t('billing.tuition.resume.description', {
              date: labels.date(pause.starts_on),
            })
          : ''
      }
      submitLabel={t('billing.tuition.resume.submit')}
      validate={() => {
        setError(resumeOn ? undefined : `${V}.resumeRequired`);
        return resumeOn !== '';
      }}
      onSubmit={async () => {
        await onSubmit({ version: plan.version, resume_on: resumeOn });
      }}
      onFieldErrors={(caught) => {
        const found = dialogServerErrors(
          caught,
          { resume_on: 'resumeOn' } as const,
          { tuition_resume_invalid: 'resumeOn' } as const,
        );
        if (found) setError(found.resumeOn);
        return found !== null;
      }}
      onReload={onReload}
      onClose={onClose}
    >
      <Field
        id={`${id}-resume`}
        label={t('billing.tuition.resume.from')}
        help={t('billing.tuition.resume.fromHelp')}
        error={error}
      >
        {(describedBy, invalid) => (
          <select
            id={`${id}-resume`}
            className={SELECT_CLASS}
            value={resumeOn}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => setResumeOn(event.target.value)}
          >
            {starts.map((period) => (
              <option key={period.period_start} value={period.period_start}>
                {labels.date(period.period_start)}
              </option>
            ))}
          </select>
        )}
      </Field>
    </ActionDialog>
  );
}

// -------------------------------------------------------------------- end

/** The End dialog's "Remove end date" choice. */
const NO_END = 'none';

export function EndDialog(props: PlanDialogProps<TuitionPlanUpdate>) {
  return props.open ? <EndForm {...props} /> : null;
}

function EndForm({
  plan,
  open,
  onSubmit,
  onReload,
  onClose,
}: PlanDialogProps<TuitionPlanUpdate>) {
  const { t } = useTranslation();
  const labels = useTuitionLabels();
  const id = useId();
  const ends = endChoices(plan).filter(
    (period) => period.period_end !== plan.ends_on,
  );
  const [value, setValue] = useState(
    ends[0]?.period_end ?? (plan.ends_on !== null ? NO_END : ''),
  );
  const [error, setError] = useState<string | undefined>();

  return (
    <ActionDialog
      open={open}
      title={t('billing.tuition.end.title')}
      description={
        plan.ends_on === null
          ? t('billing.tuition.end.descriptionOpen')
          : t('billing.tuition.end.description', {
              date: labels.date(plan.ends_on),
            })
      }
      submitLabel={t('billing.tuition.end.submit')}
      validate={() => {
        setError(value ? undefined : `${V}.endRequired`);
        return value !== '';
      }}
      onSubmit={async () => {
        await onSubmit({
          version: plan.version,
          ends_on: value === NO_END ? null : value,
        });
      }}
      onFieldErrors={(caught) => {
        const found = dialogServerErrors(
          caught,
          { ends_on: 'endsOn' } as const,
          { tuition_plan_ends_on_invalid: 'endsOn' } as const,
        );
        if (found) setError(found.endsOn);
        return found !== null;
      }}
      onReload={onReload}
      onClose={onClose}
    >
      <Field
        id={`${id}-end`}
        label={t('billing.tuition.end.lastDay')}
        help={t('billing.tuition.end.help')}
        error={error}
      >
        {(describedBy, invalid) => (
          <select
            id={`${id}-end`}
            className={SELECT_CLASS}
            value={value}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => setValue(event.target.value)}
          >
            {ends.map((period) => (
              <option key={period.period_end} value={period.period_end}>
                {labels.date(period.period_end)}
              </option>
            ))}
            {plan.ends_on !== null ? (
              <option value={NO_END}>{t('billing.tuition.end.remove')}</option>
            ) : null}
          </select>
        )}
      </Field>
    </ActionDialog>
  );
}

// ------------------------------------------------------------- due offset

export function DueOffsetDialog(props: PlanDialogProps<TuitionPlanUpdate>) {
  return props.open ? <DueOffsetForm {...props} /> : null;
}

function DueOffsetForm({
  plan,
  open,
  onSubmit,
  onReload,
  onClose,
}: PlanDialogProps<TuitionPlanUpdate>) {
  const { t } = useTranslation();
  const id = useId();
  const [value, setValue] = useState(String(plan.due_offset_days));
  const [error, setError] = useState<string | undefined>();

  return (
    <ActionDialog
      open={open}
      title={t('billing.tuition.dueOffset.title')}
      description={t('billing.tuition.dueOffset.description')}
      submitLabel={t('billing.prices.save')}
      validate={() => {
        const valid = validDueOffset(value);
        setError(valid ? undefined : `${V}.dueOffsetRange`);
        if (!valid) document.getElementById(`${id}-days`)?.focus();
        return valid;
      }}
      onSubmit={async () => {
        await onSubmit({
          version: plan.version,
          due_offset_days: Number(value.trim()),
        });
      }}
      onFieldErrors={(caught) => {
        const found = dialogServerErrors(caught, {
          due_offset_days: 'days',
        } as const);
        if (found) setError(found.days);
        return found !== null;
      }}
      onReload={onReload}
      onClose={onClose}
    >
      <Field
        id={`${id}-days`}
        label={t('billing.tuition.fields.dueOffset')}
        help={t('billing.tuition.hints.dueOffset', {
          min: DUE_OFFSET_MIN,
          max: DUE_OFFSET_MAX,
        })}
        error={error}
      >
        {(describedBy, invalid) => (
          <Input
            id={`${id}-days`}
            type="text"
            inputMode="numeric"
            autoComplete="off"
            value={value}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => setValue(event.target.value)}
          />
        )}
      </Field>
    </ActionDialog>
  );
}
