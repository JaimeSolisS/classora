import { useQuery } from '@tanstack/react-query';
import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { SELECT_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import type { ServicePrice } from '../pricing/api';
import {
  eligibleEnrollmentsKey,
  fetchEligibleEnrollments,
  searchStudents,
  TUITION_OPTIONS_KEY,
  type EligibleEnrollment,
  type StudentOption,
} from './api';
import { useTuitionLabels } from './labels';
import {
  changePlanForm,
  DUE_OFFSET_MAX,
  DUE_OFFSET_MIN,
  emptyPlanForm,
  enrollmentBlock,
  PERIODS_MAX,
  PERIODS_MIN,
  PLAN_FIELD_ORDER,
  planEndsOn,
  planServerErrors,
  pricesForPlan,
  validatePlanForm,
  type PlanErrors,
  type PlanField,
  type PlanFormValues,
  type PlanKind,
} from './rules';

// The "New plan" dialog (#374 D), without any plan call: the page passes
// the per-period prices and `onSubmit` (which resolves once the plan is
// stored; the page then opens it). The student comes from the #34 search
// picker (active students); a group plan takes one of the student's group
// enrollments (one with a plan or a charged flat fee is listed disabled
// with the reason, #93 K6); the prices follow the type and the enrollment.
// "After N periods" becomes the plan's last day through the K3 grid
// (lib/tuition.ts), shown under the field. A 422 puts each field error on
// its field; a 409 is an alert (student_has_no_family links to the
// families list for guardian.view holders). Nothing typed is lost.

export interface TuitionPlanDialogProps {
  open: boolean;
  /** Today's date in the center's zone (the default start). */
  today: LocalDate;
  /** Every per-period catalog price; null while they load. */
  prices: readonly ServicePrice[] | null;
  pricesError?: unknown;
  onRetryPrices?: () => void;
  /** guardian.view: the student_has_no_family alert links to families. */
  canOpenFamilies: boolean;
  /** Stores the plan; rejects with the error to show. */
  onSubmit: (values: PlanFormValues, price: ServicePrice) => Promise<void>;
  onClose: () => void;
  /** Stories: the searches instead of the API. */
  search?: (q: string, signal: AbortSignal) => Promise<StudentOption[]>;
  fetchEnrollments?: (
    studentId: string,
    signal: AbortSignal,
  ) => Promise<EligibleEnrollment[]>;
  /** Stories: the values and errors to start from. */
  initialValues?: PlanFormValues;
  initialErrors?: PlanErrors;
}

export function TuitionPlanDialog(props: TuitionPlanDialogProps) {
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
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

const KINDS: readonly PlanKind[] = ['individual', 'group'];

function DialogBody({
  today,
  prices,
  pricesError,
  onRetryPrices,
  canOpenFamilies,
  onSubmit,
  onClose,
  search = searchStudents,
  fetchEnrollments = fetchEligibleEnrollments,
  initialValues,
  initialErrors = {},
  busyRef,
}: TuitionPlanDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const message = useFieldMessage();
  const labels = useTuitionLabels();
  const { formatLocalDate } = useFormatters();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<PlanFormValues>(
    () => initialValues ?? emptyPlanForm(today),
  );
  const [errors, setErrors] = useState<PlanErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const studentId = values.student?.id ?? '';
  const enrollments = useQuery({
    queryKey: eligibleEnrollmentsKey(studentId),
    queryFn: ({ signal }) => fetchEnrollments(studentId, signal),
    enabled: values.kind === 'group' && studentId !== '',
  });
  const enrollment =
    enrollments.data?.find(
      (item) => item.group_enrollment_id === values.enrollmentId,
    ) ?? null;
  const choices = prices ? pricesForPlan(prices, values.kind, enrollment) : [];
  const price = choices.find((item) => item.id === values.priceId) ?? null;
  const endsOn = planEndsOn(values, price?.period_frequency);

  const fieldId = (field: PlanField) => `${id}-${field}`;
  const errorId = (field: PlanField) => `${id}-${field}-error`;
  const helpId = (field: PlanField) => `${id}-${field}-help`;

  function change<K extends keyof PlanFormValues>(
    field: K,
    value: PlanFormValues[K],
  ) {
    setValues((current) => changePlanForm(current, field, value));
  }

  function focusFirst(found: PlanErrors) {
    const first = PLAN_FIELD_ORDER.find((field) => found[field]);
    if (!first) return;
    const target = document.getElementById(fieldId(first));
    const focusable =
      target instanceof HTMLInputElement || target instanceof HTMLSelectElement
        ? target
        : target?.querySelector<HTMLElement>('input, select');
    focusable?.focus();
  }

  async function submit() {
    setFailure(null);
    const found = validatePlanForm(values);
    if (!found.priceId && !price) {
      found.priceId = 'billing.tuition.validation.priceRequired';
    }
    setErrors(found);
    if (Object.keys(found).length > 0 || !price) {
      focusFirst(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values, price);
    } catch (caught) {
      const fromServer = planServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setFailure(caught);
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

  const describedBy = (field: PlanField, help = false) =>
    [
      help ? helpId(field) : undefined,
      errors[field] ? errorId(field) : undefined,
    ]
      .filter(Boolean)
      .join(' ') || undefined;
  const invalid = (field: PlanField) => (errors[field] ? true : undefined);

  function field(
    name: PlanField,
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
        {help ? (
          <p id={helpId(name)} className="text-sm text-muted-foreground">
            {help}
          </p>
        ) : null}
        <FieldError id={errorId(name)} message={message(errors[name])} />
      </div>
    );
  }

  const noFamily =
    failure instanceof ApiError && failure.code === 'student_has_no_family';

  let enrollmentHelp: string;
  if (!values.student) {
    enrollmentHelp = t('billing.tuition.hints.enrollmentStudentFirst');
  } else if (enrollments.isError) {
    enrollmentHelp = t('billing.tuition.hints.enrollmentFailed');
  } else if (enrollments.isSuccess && enrollments.data.length === 0) {
    enrollmentHelp = t('billing.tuition.hints.enrollmentNone');
  } else {
    enrollmentHelp = t('billing.tuition.hints.enrollment');
  }

  let priceHelp: string;
  if (values.kind === 'group' && !enrollment) {
    priceHelp = t('billing.tuition.hints.priceEnrollmentFirst');
  } else if (prices && choices.length === 0) {
    priceHelp = t(`billing.tuition.hints.priceNone.${values.kind}`);
  } else {
    priceHelp = t('billing.tuition.hints.price');
  }

  let content;
  if (pricesError) {
    content = <ErrorAlert error={pricesError} onRetry={onRetryPrices} />;
  } else if (!prices) {
    content = <LoadingState rows={6} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div id={fieldId('student')}>
          <SearchPicker<StudentOption>
            label={t('billing.tuition.fields.student')}
            help={
              values.student
                ? t('billing.tuition.chosenStudent', {
                    name: values.student.name,
                  })
                : t('billing.tuition.hints.student')
            }
            queryKey={[...TUITION_OPTIONS_KEY, 'dialog-students']}
            search={search}
            optionId={(option) => option.id}
            optionLabel={(option) => option.name}
            selectedId={values.student?.id ?? null}
            onSelect={(student) => change('student', student)}
            errorMessage={message(errors.student)}
          />
        </div>
        <fieldset id={fieldId('kind')} className="flex flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">
            {t('billing.tuition.fields.kind')}
          </legend>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {KINDS.map((kind) => (
              <label
                key={kind}
                className="flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  type="radio"
                  name={`${id}-kind`}
                  value={kind}
                  checked={values.kind === kind}
                  className="size-4 accent-primary"
                  onChange={() => change('kind', kind)}
                />
                {t(`billing.tuition.kinds.${kind}`)}
              </label>
            ))}
          </div>
          {values.kind === 'individual' ? (
            <p className="text-sm text-muted-foreground">
              {t('billing.tuition.hints.individual')}
            </p>
          ) : null}
        </fieldset>
        {values.kind === 'group'
          ? field(
              'enrollmentId',
              t('billing.tuition.fields.enrollment'),
              <select
                id={fieldId('enrollmentId')}
                className={SELECT_CLASS}
                value={values.enrollmentId}
                disabled={!enrollments.isSuccess}
                aria-invalid={invalid('enrollmentId')}
                aria-describedby={describedBy('enrollmentId', true)}
                onChange={(event) => change('enrollmentId', event.target.value)}
              >
                <option value="">{t('billing.prices.choose')}</option>
                {(enrollments.data ?? []).map((item) => {
                  const block = enrollmentBlock(item);
                  const name = t('billing.tuition.enrollmentOption', {
                    group: item.group.name,
                    subject: item.subject.name,
                    date: formatLocalDate(item.enrolled_on),
                  });
                  return (
                    <option
                      key={item.group_enrollment_id}
                      value={item.group_enrollment_id}
                      disabled={block !== null}
                    >
                      {block
                        ? t('billing.tuition.enrollmentBlocked', {
                            name,
                            reason: t(
                              `billing.tuition.enrollmentBlock.${block}`,
                            ),
                          })
                        : name}
                    </option>
                  );
                })}
              </select>,
              enrollmentHelp,
            )
          : null}
        {field(
          'priceId',
          t('billing.tuition.fields.price'),
          <select
            id={fieldId('priceId')}
            className={SELECT_CLASS}
            value={values.priceId}
            disabled={choices.length === 0}
            aria-invalid={invalid('priceId')}
            aria-describedby={describedBy('priceId', true)}
            onChange={(event) => change('priceId', event.target.value)}
          >
            <option value="">{t('billing.prices.choose')}</option>
            {choices.map((item) => (
              <option key={item.id} value={item.id}>
                {labels.priceOption(item)}
              </option>
            ))}
          </select>,
          priceHelp,
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {field(
            'startsOn',
            t('billing.tuition.fields.startsOn'),
            <Input
              id={fieldId('startsOn')}
              type="date"
              value={values.startsOn}
              aria-invalid={invalid('startsOn')}
              aria-describedby={describedBy('startsOn', true)}
              onChange={(event) => change('startsOn', event.target.value)}
            />,
            t('billing.tuition.hints.startsOn'),
          )}
          {field(
            'dueOffset',
            t('billing.tuition.fields.dueOffset'),
            <Input
              id={fieldId('dueOffset')}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={values.dueOffset}
              aria-invalid={invalid('dueOffset')}
              aria-describedby={describedBy('dueOffset', true)}
              onChange={(event) => change('dueOffset', event.target.value)}
            />,
            t('billing.tuition.hints.dueOffset', {
              min: DUE_OFFSET_MIN,
              max: DUE_OFFSET_MAX,
            }),
          )}
        </div>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">
            {t('billing.tuition.fields.end')}
          </legend>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {(['open', 'periods'] as const).map((end) => (
              <label
                key={end}
                className="flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  type="radio"
                  name={`${id}-end`}
                  value={end}
                  checked={values.end === end}
                  className="size-4 accent-primary"
                  onChange={() => change('end', end)}
                />
                {t(`billing.tuition.ends.${end}`)}
              </label>
            ))}
          </div>
          {values.end === 'periods'
            ? field(
                'periods',
                t('billing.tuition.fields.periods'),
                <Input
                  id={fieldId('periods')}
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  className="sm:max-w-40"
                  value={values.periods}
                  aria-invalid={invalid('periods')}
                  aria-describedby={describedBy('periods', true)}
                  onChange={(event) => change('periods', event.target.value)}
                />,
                endsOn
                  ? t('billing.tuition.hints.lastDay', {
                      date: formatLocalDate(endsOn),
                    })
                  : t('billing.tuition.hints.periods', {
                      min: PERIODS_MIN,
                      max: PERIODS_MAX,
                    }),
              )
            : null}
        </fieldset>
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
              t('billing.tuition.create')
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <DialogContent
      aria-describedby={undefined}
      className="max-h-[90vh] overflow-y-auto sm:max-w-2xl"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.tuition.add')}</DialogTitle>
      </DialogHeader>
      {noFamily ? (
        <div
          role="alert"
          className="flex flex-col items-start gap-2 rounded-md border border-destructive bg-background p-4"
        >
          <p className="font-medium text-destructive">
            {tErrors('student_has_no_family')}
          </p>
          {canOpenFamilies ? (
            <Link to="/families" className={TEXT_LINK_CLASS}>
              {t('billing.tuition.openFamilies')}
            </Link>
          ) : null}
        </div>
      ) : failure ? (
        <ErrorAlert error={failure} />
      ) : null}
      {content}
    </DialogContent>
  );
}
