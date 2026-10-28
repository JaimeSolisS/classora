import { useQuery } from '@tanstack/react-query';
import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
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
import { subjectsByLevel } from '@/features/scheduling/create/rules';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { SELECT_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import type { LocalDate } from '@/lib/time';
import {
  scopeOptionsKey,
  type ClassType,
  type Option,
  type PricingModel,
  type ScopeKind,
  type SubjectOption,
} from './api';
import { usePriceLabels } from './labels';
import {
  changePriceForm,
  CLASS_TYPES,
  DURATION_MAX,
  DURATION_MIN,
  emptyPriceForm,
  existingPriceId,
  MODELS_BY_CLASS,
  PRICE_FIELD_ORDER,
  priceServerErrors,
  scopeKindOf,
  validatePriceForm,
  type PriceErrors,
  type PriceField,
  type PriceFormValues,
} from './rules';

// The "Add price" dialog (#363 C), without any API call: the page passes
// the subjects and levels, the scope reader and `onSubmit` (which resolves
// once the item is stored; the page then navigates to it). The pricing
// models follow the class type (pre-selected when only one applies); the
// scope (a cohort for a course, a group for a group, of the chosen subject)
// clears and disables the level and the duration (#89 E4); only a
// per-session price has a duration. A 409 service_price_exists links to
// the existing price; a 422 puts each field error on its field; anything
// else is an alert. Nothing typed is lost on any error.

export interface ServicePriceDialogProps {
  open: boolean;
  /** Today's date in the center's zone (the default "Effective from"). */
  today: LocalDate;
  /** The active subjects; null while they load. */
  subjects: readonly SubjectOption[] | null;
  /** The active levels; null while they load. */
  levels: readonly Option[] | null;
  /** A failed load of the subjects or the levels. */
  optionsError?: unknown;
  onRetryOptions?: () => void;
  /** The cohorts or groups of a subject. */
  fetchScopes: (
    kind: ScopeKind,
    subjectId: string,
    signal: AbortSignal,
  ) => Promise<Option[]>;
  /** Stores the price; rejects with the error to show. */
  onSubmit: (values: PriceFormValues) => Promise<void>;
  onClose: () => void;
  /** Stories: the values and errors to start from. */
  initialValues?: PriceFormValues;
  initialErrors?: PriceErrors;
}

export function ServicePriceDialog(props: ServicePriceDialogProps) {
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

function DialogBody({
  today,
  subjects,
  levels,
  optionsError,
  onRetryOptions,
  fetchScopes,
  onSubmit,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: ServicePriceDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const message = useFieldMessage();
  const labels = usePriceLabels();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<PriceFormValues>(
    () => initialValues ?? emptyPriceForm(today),
  );
  const [errors, setErrors] = useState<PriceErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [existingId, setExistingId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const scopeKind = scopeKindOf(values.classType);
  const scopes = useQuery({
    queryKey: scopeOptionsKey(scopeKind ?? 'group', values.subjectId),
    queryFn: ({ signal }) =>
      fetchScopes(scopeKind ?? 'group', values.subjectId, signal),
    enabled: scopeKind !== null && values.subjectId !== '',
  });

  const fieldId = (field: PriceField) => `${id}-${field}`;
  const errorId = (field: PriceField) => `${id}-${field}-error`;
  const helpId = (field: PriceField) => `${id}-${field}-help`;

  function change<K extends PriceField>(field: K, value: PriceFormValues[K]) {
    setValues((current) => changePriceForm(current, field, value));
    setExistingId(null);
  }

  function focusFirst(found: PriceErrors) {
    const first = PRICE_FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  async function submit() {
    setFailure(null);
    setExistingId(null);
    const found = validatePriceForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (caught) {
      const existing = existingPriceId(caught);
      const fromServer = priceServerErrors(caught);
      if (existing) {
        setExistingId(existing);
      } else if (fromServer) {
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
    if (busyRef.current) {
      return;
    }
    void submit();
  }

  const describedBy = (field: PriceField, help = false) =>
    [
      help ? helpId(field) : undefined,
      errors[field] ? errorId(field) : undefined,
    ]
      .filter(Boolean)
      .join(' ') || undefined;
  const invalid = (field: PriceField) => (errors[field] ? true : undefined);

  function field(
    name: PriceField,
    label: string,
    control: ReactNode,
    help?: string,
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

  function select(
    name: PriceField,
    value: string,
    onChange: (value: string) => void,
    options: ReactNode,
    { disabled = false, help = false } = {},
  ) {
    return (
      <select
        id={fieldId(name)}
        className={SELECT_CLASS}
        value={value}
        disabled={disabled}
        aria-invalid={invalid(name)}
        aria-describedby={describedBy(name, help)}
        onChange={(event) => onChange(event.target.value)}
      >
        {options}
      </select>
    );
  }

  const models = values.classType ? MODELS_BY_CLASS[values.classType] : [];
  const perSession = values.pricingModel === 'per_session';
  const scoped = values.scopeId !== '';
  const scopeHelp =
    scopeKind === 'cohort'
      ? t('billing.prices.hints.scopeCohort')
      : scopeKind === 'group'
        ? t('billing.prices.hints.scopeGroup')
        : t('billing.prices.hints.scopeNone');
  const durationHelp = scoped
    ? t('billing.prices.hints.scoped')
    : perSession
      ? t('billing.prices.hints.duration', {
          min: DURATION_MIN,
          max: DURATION_MAX,
        })
      : t('billing.prices.hints.durationPerSession');

  let content;
  if (optionsError) {
    content = <ErrorAlert error={optionsError} onRetry={onRetryOptions} />;
  } else if (!subjects || !levels) {
    content = <LoadingState rows={6} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {field(
            'classType',
            t('billing.prices.fields.classType'),
            select(
              'classType',
              values.classType,
              (value) =>
                change(
                  'classType',
                  CLASS_TYPES.find((type) => type === value) ?? '',
                ),
              <>
                <option value="">{t('billing.prices.choose')}</option>
                {CLASS_TYPES.map((type: ClassType) => (
                  <option key={type} value={type}>
                    {labels.classType(type)}
                  </option>
                ))}
              </>,
            ),
          )}
          {field(
            'pricingModel',
            t('billing.prices.fields.pricingModel'),
            select(
              'pricingModel',
              values.pricingModel,
              (value) =>
                change(
                  'pricingModel',
                  models.find((model) => model === value) ?? '',
                ),
              <>
                {models.length !== 1 ? (
                  <option value="">{t('billing.prices.choose')}</option>
                ) : null}
                {values.classType
                  ? models.map((model: PricingModel) => (
                      <option key={model} value={model}>
                        {t(
                          `billing.prices.modelOptions.${
                            model === 'full_course' &&
                            values.classType === 'group'
                              ? 'flat_fee'
                              : model === 'full_course'
                                ? 'course_fee'
                                : 'per_session'
                          }`,
                        )}
                      </option>
                    ))
                  : null}
              </>,
              { disabled: !values.classType },
            ),
          )}
        </div>
        {field(
          'subjectId',
          t('billing.prices.fields.subject'),
          select(
            'subjectId',
            values.subjectId,
            (value) => change('subjectId', value),
            <>
              <option value="">{t('billing.prices.choose')}</option>
              {subjectsByLevel(subjects).map(([levelName, group]) => (
                <optgroup key={levelName} label={levelName}>
                  {group.map((subject) => (
                    <option key={subject.id} value={subject.id}>
                      {subject.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </>,
          ),
        )}
        {field(
          'scopeId',
          t('billing.prices.fields.scope'),
          select(
            'scopeId',
            values.scopeId,
            (value) => change('scopeId', value),
            <>
              <option value="">{t('billing.prices.noScope')}</option>
              {(scopes.data ?? []).map((scope) => (
                <option key={scope.id} value={scope.id}>
                  {scope.name}
                </option>
              ))}
            </>,
            {
              disabled:
                scopeKind === null ||
                values.subjectId === '' ||
                !scopes.isSuccess,
              help: true,
            },
          ),
          scopes.isError ? t('billing.prices.hints.scopeFailed') : scopeHelp,
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {field(
            'levelId',
            t('billing.prices.fields.level'),
            select(
              'levelId',
              values.levelId,
              (value) => change('levelId', value),
              <>
                <option value="">{t('billing.prices.any')}</option>
                {levels.map((level) => (
                  <option key={level.id} value={level.id}>
                    {level.name}
                  </option>
                ))}
              </>,
              { disabled: scoped, help: true },
            ),
            scoped
              ? t('billing.prices.hints.scoped')
              : t('billing.prices.hints.level'),
          )}
          {field(
            'duration',
            t('billing.prices.fields.duration'),
            <Input
              id={fieldId('duration')}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={values.duration}
              disabled={!perSession || scoped}
              aria-invalid={invalid('duration')}
              aria-describedby={describedBy('duration', true)}
              onChange={(event) => change('duration', event.target.value)}
            />,
            durationHelp,
          )}
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {field(
            'effectiveFrom',
            t('billing.prices.fields.effectiveFrom'),
            <Input
              id={fieldId('effectiveFrom')}
              type="date"
              value={values.effectiveFrom}
              aria-invalid={invalid('effectiveFrom')}
              aria-describedby={describedBy('effectiveFrom')}
              onChange={(event) => change('effectiveFrom', event.target.value)}
            />,
          )}
          {field(
            'amount',
            t('billing.prices.fields.amount'),
            <Input
              id={fieldId('amount')}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={values.amount}
              aria-invalid={invalid('amount')}
              aria-describedby={describedBy('amount', true)}
              onChange={(event) => change('amount', event.target.value)}
            />,
            t('billing.prices.hints.amount'),
          )}
        </div>
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
              t('billing.prices.save')
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
        <DialogTitle>{t('billing.prices.add')}</DialogTitle>
      </DialogHeader>
      {existingId ? (
        <div
          role="alert"
          className="flex flex-col items-start gap-2 rounded-md border border-destructive bg-background p-4"
        >
          <p className="font-medium text-destructive">
            {tErrors('service_price_exists')}
          </p>
          <Link
            to={`/billing/prices/${existingId}`}
            className={TEXT_LINK_CLASS}
          >
            {t('billing.prices.viewExisting')}
          </Link>
        </div>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      {content}
    </DialogContent>
  );
}
