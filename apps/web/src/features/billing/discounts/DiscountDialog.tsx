import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
import { subjectsByLevel } from '@/features/scheduling/create/rules';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { Option, SubjectOption } from '../pricing/api';
import type { DiscountDetail, DiscountKind } from './api';
import {
  CHARGE_KINDS,
  changeDiscountForm,
  CRITERIA_CLASS_TYPES,
  DISCOUNT_FIELD_ORDER,
  DISCOUNT_KINDS,
  discountFormOf,
  discountServerErrors,
  emptyDiscountForm,
  forcedAudience,
  MIN_FAMILY_MAX,
  MIN_FAMILY_MIN,
  NAME_MAX_LENGTH,
  validateDiscountForm,
  type DiscountErrors,
  type DiscountField,
  type DiscountFormValues,
} from './rules';

// "Add discount" and "Edit discount" (#367 C), without any API call: the
// page passes the subjects and levels and `onSubmit` (which resolves once
// the discount is stored). The kind and who gets it are chosen on create
// only; a scholarship is always for assigned students or families and a
// sibling discount for everyone who qualifies (#95 H2), shown disabled
// with a hint. Every criterion is optional ("Any"). A 409
// discount_name_exists and the 422 field errors go on their fields; a 409
// version_conflict shows the reload message. Nothing typed is lost.

export interface DiscountDialogProps {
  open: boolean;
  /** The discount being edited; absent to create one. */
  discount?: DiscountDetail;
  /** The active subjects; null while they load. */
  subjects: readonly SubjectOption[] | null;
  /** The active levels; null while they load. */
  levels: readonly Option[] | null;
  optionsError?: unknown;
  onRetryOptions?: () => void;
  /**
   * Stores the discount; rejects with the error to show. `initial` is the
   * form as it opened (the edit sends only what changed from it).
   */
  onSubmit: (
    values: DiscountFormValues,
    initial: DiscountFormValues,
  ) => Promise<void>;
  /** Reloads the discount after a version_conflict. */
  onReload?: () => void;
  onClose: () => void;
  /** Stories: the values and errors to start from. */
  initialValues?: DiscountFormValues;
  initialErrors?: DiscountErrors;
}

export function DiscountDialog(props: DiscountDialogProps) {
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

interface Choice {
  value: string;
  label: string;
}

function DialogBody({
  discount,
  subjects,
  levels,
  optionsError,
  onRetryOptions,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: DiscountDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const editing = discount !== undefined;
  const [initial] = useState<DiscountFormValues>(() =>
    discount ? discountFormOf(discount) : emptyDiscountForm(),
  );
  const [values, setValues] = useState<DiscountFormValues>(
    () => initialValues ?? initial,
  );
  const [errors, setErrors] = useState<DiscountErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: DiscountField) => `${id}-${field}`;
  const errorId = (field: DiscountField) => `${id}-${field}-error`;
  const helpId = (field: DiscountField) => `${id}-${field}-help`;

  function change<K extends DiscountField>(
    field: K,
    value: DiscountFormValues[K],
  ) {
    setValues((current) => changeDiscountForm(current, field, value));
  }

  function focusFirst(found: DiscountErrors) {
    const first = DISCOUNT_FIELD_ORDER.find((field) => found[field]);
    if (!first) return;
    const element = document.getElementById(fieldId(first));
    if (element instanceof HTMLInputElement) {
      element.focus();
    } else {
      element?.querySelector<HTMLInputElement>('input:not(:disabled)')?.focus();
    }
  }

  async function submit() {
    setFailure(null);
    const found = validateDiscountForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values, initial);
    } catch (caught) {
      const fromServer = discountServerErrors(caught);
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
    if (busyRef.current) {
      return;
    }
    void submit();
  }

  const describedBy = (field: DiscountField, help = false) =>
    [
      help ? helpId(field) : undefined,
      errors[field] ? errorId(field) : undefined,
    ]
      .filter(Boolean)
      .join(' ') || undefined;
  const invalid = (field: DiscountField) => (errors[field] ? true : undefined);

  function textField(
    name: DiscountField,
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

  function radios(
    name: DiscountField,
    legend: string,
    choices: readonly (Choice & { help?: string; disabled?: boolean })[],
    value: string,
    onChange: (value: string) => void,
    help?: string,
  ) {
    return (
      <fieldset
        id={fieldId(name)}
        className="flex min-w-0 flex-col gap-2"
        aria-describedby={describedBy(name, Boolean(help))}
      >
        <legend className="mb-2 text-sm font-medium">{legend}</legend>
        {choices.map((choice) => (
          <label
            key={choice.value}
            className="flex min-h-6 items-start gap-2 text-sm"
          >
            <input
              type="radio"
              name={fieldId(name)}
              value={choice.value}
              checked={value === choice.value}
              disabled={choice.disabled}
              className="mt-0.5 size-4 shrink-0 accent-primary"
              onChange={() => onChange(choice.value)}
            />
            <span className="flex flex-col">
              <span>{choice.label}</span>
              {choice.help ? (
                <span className="text-muted-foreground">{choice.help}</span>
              ) : null}
            </span>
          </label>
        ))}
        {help ? (
          <p id={helpId(name)} className="text-sm text-muted-foreground">
            {help}
          </p>
        ) : null}
        <FieldError id={errorId(name)} message={message(errors[name])} />
      </fieldset>
    );
  }

  function checkboxes(
    name: 'chargeKinds' | 'classTypes' | 'subjectIds' | 'levelIds',
    legend: string,
    groups: readonly [string | null, readonly Choice[]][],
    selected: readonly string[],
    onChange: (next: string[]) => void,
  ) {
    const toggle = (value: string, checked: boolean) =>
      onChange(
        checked
          ? [...selected, value]
          : selected.filter((item) => item !== value),
      );
    const many = groups.reduce((sum, [, list]) => sum + list.length, 0) > 8;
    return (
      <fieldset
        id={fieldId(name)}
        className="flex min-w-0 flex-col gap-2"
        aria-describedby={describedBy(name, true)}
      >
        <legend className="mb-1 text-sm font-medium">{legend}</legend>
        <p id={helpId(name)} className="text-sm text-muted-foreground">
          {selected.length === 0
            ? t('billing.discounts.hints.any')
            : t('billing.discounts.hints.chosen', { count: selected.length })}
        </p>
        <div
          className={
            many
              ? 'flex max-h-48 flex-col gap-3 overflow-y-auto rounded-md border border-border p-3'
              : 'flex flex-col gap-3'
          }
        >
          {groups.map(([group, list]) => (
            <div key={group ?? ''} className="flex flex-col gap-1">
              {group ? (
                <p className="text-sm font-medium text-muted-foreground">
                  {group}
                </p>
              ) : null}
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                {list.map((choice) => (
                  <label
                    key={choice.value}
                    className="flex min-h-6 items-center gap-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      value={choice.value}
                      checked={selected.includes(choice.value)}
                      className="size-4 shrink-0 accent-primary"
                      onChange={(event) =>
                        toggle(choice.value, event.target.checked)
                      }
                    />
                    {choice.label}
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
        <FieldError id={errorId(name)} message={message(errors[name])} />
      </fieldset>
    );
  }

  const kindText = (kind: DiscountKind) => t(`billing.discounts.kinds.${kind}`);
  const forced = forcedAudience(values.kind);

  let content;
  if (optionsError) {
    content = <ErrorAlert error={optionsError} onRetry={onRetryOptions} />;
  } else if (!subjects || !levels) {
    content = <LoadingState rows={6} />;
  } else {
    // Criteria already chosen that the active lists no longer have (an
    // inactive subject or level) stay listed so they can be removed.
    const known = new Set(subjects.map((subject) => subject.id));
    const otherSubjects = (discount?.eligibility.subjects ?? []).filter(
      (subject) => !known.has(subject.id),
    );
    const knownLevels = new Set(levels.map((level) => level.id));
    const otherLevels = (discount?.eligibility.education_levels ?? []).filter(
      (level) => !knownLevels.has(level.id),
    );
    const subjectGroups: [string | null, Choice[]][] = [
      ...subjectsByLevel(subjects).map(
        ([levelName, group]): [string, Choice[]] => [
          levelName,
          group.map((subject) => ({ value: subject.id, label: subject.name })),
        ],
      ),
      ...(otherSubjects.length > 0
        ? [
            [
              t('billing.discounts.fields.inactive'),
              otherSubjects.map((subject) => ({
                value: subject.id,
                label: subject.name,
              })),
            ] as [string, Choice[]],
          ]
        : []),
    ];
    const levelChoices: Choice[] = [...levels, ...otherLevels].map((level) => ({
      value: level.id,
      label: level.name,
    }));
    const percent = values.valueType === 'percent';

    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-5"
      >
        {editing ? (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="font-medium">
              {t('billing.discounts.fields.kind')}
            </dt>
            <dd>{kindText(values.kind)}</dd>
            <dt className="font-medium">
              {t('billing.discounts.fields.audience')}
            </dt>
            <dd>{t(`billing.discounts.audience.${values.audience}`)}</dd>
          </dl>
        ) : (
          radios(
            'kind',
            t('billing.discounts.fields.kind'),
            DISCOUNT_KINDS.map((kind) => ({
              value: kind,
              label: kindText(kind),
              help: t(`billing.discounts.kindHelp.${kind}`),
            })),
            values.kind,
            (value) =>
              change(
                'kind',
                DISCOUNT_KINDS.find((kind) => kind === value) ?? 'discount',
              ),
          )
        )}
        {textField(
          'name',
          t('billing.discounts.fields.name'),
          <Input
            id={fieldId('name')}
            type="text"
            autoComplete="off"
            maxLength={NAME_MAX_LENGTH}
            value={values.name}
            aria-invalid={invalid('name')}
            aria-describedby={describedBy('name')}
            onChange={(event) => change('name', event.target.value)}
          />,
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-2 text-sm font-medium">
              {t('billing.discounts.fields.valueType')}
            </legend>
            {(['percent', 'amount'] as const).map((type) => (
              <label
                key={type}
                className="flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  type="radio"
                  name={`${id}-valueType`}
                  value={type}
                  checked={values.valueType === type}
                  className="size-4 shrink-0 accent-primary"
                  onChange={() => change('valueType', type)}
                />
                {t(`billing.discounts.valueTypes.${type}`)}
              </label>
            ))}
          </fieldset>
          {textField(
            'value',
            percent
              ? t('billing.discounts.fields.percent')
              : t('billing.discounts.fields.amount'),
            <Input
              id={fieldId('value')}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={values.value}
              aria-invalid={invalid('value')}
              aria-describedby={describedBy('value', true)}
              onChange={(event) => change('value', event.target.value)}
            />,
            percent
              ? t('billing.discounts.hints.percent')
              : t('billing.discounts.hints.amount'),
          )}
        </div>
        {editing
          ? null
          : radios(
              'audience',
              t('billing.discounts.fields.audience'),
              (['all', 'assigned'] as const).map((audience) => ({
                value: audience,
                label: t(`billing.discounts.audience.${audience}`),
                disabled: forced !== null && forced !== audience,
              })),
              values.audience,
              (value) =>
                change('audience', value === 'assigned' ? 'assigned' : 'all'),
              forced === 'assigned'
                ? t('billing.discounts.hints.scholarshipAssigned')
                : forced === 'all'
                  ? t('billing.discounts.hints.siblingAll')
                  : t('billing.discounts.hints.audience'),
            )}
        <section
          aria-labelledby={`${id}-criteria`}
          className="flex flex-col gap-4 rounded-md border border-border p-4"
        >
          <div className="flex flex-col gap-1">
            <h3 id={`${id}-criteria`} className="text-base font-semibold">
              {t('billing.discounts.fields.criteria')}
            </h3>
            <p className="text-sm text-muted-foreground">
              {t('billing.discounts.hints.criteria')}
            </p>
          </div>
          {checkboxes(
            'chargeKinds',
            t('billing.discounts.fields.chargeKinds'),
            [
              [
                null,
                CHARGE_KINDS.map((kind) => ({
                  value: kind,
                  label: t(`billing.discounts.chargeKinds.${kind}`),
                })),
              ],
            ],
            values.chargeKinds,
            (next) =>
              change(
                'chargeKinds',
                CHARGE_KINDS.filter((kind) => next.includes(kind)),
              ),
          )}
          {checkboxes(
            'classTypes',
            t('billing.discounts.fields.classTypes'),
            [
              [
                null,
                CRITERIA_CLASS_TYPES.map((type) => ({
                  value: type,
                  label: t(`billing.prices.classTypes.${type}`),
                })),
              ],
            ],
            values.classTypes,
            (next) =>
              change(
                'classTypes',
                CRITERIA_CLASS_TYPES.filter((type) => next.includes(type)),
              ),
          )}
          {checkboxes(
            'subjectIds',
            t('billing.discounts.fields.subjects'),
            subjectGroups,
            values.subjectIds,
            (next) => change('subjectIds', next),
          )}
          {checkboxes(
            'levelIds',
            t('billing.discounts.fields.levels'),
            [[null, levelChoices]],
            values.levelIds,
            (next) => change('levelIds', next),
          )}
          {values.kind === 'sibling'
            ? textField(
                'minFamilyStudents',
                t('billing.discounts.fields.minFamilyStudents'),
                <Input
                  id={fieldId('minFamilyStudents')}
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  value={values.minFamilyStudents}
                  aria-invalid={invalid('minFamilyStudents')}
                  aria-describedby={describedBy('minFamilyStudents', true)}
                  onChange={(event) =>
                    change('minFamilyStudents', event.target.value)
                  }
                />,
                t('billing.discounts.hints.minFamily', {
                  min: MIN_FAMILY_MIN,
                  max: MIN_FAMILY_MAX,
                }),
              )
            : null}
        </section>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {textField(
            'startsOn',
            t('billing.discounts.fields.startsOn'),
            <Input
              id={fieldId('startsOn')}
              type="date"
              value={values.startsOn}
              aria-invalid={invalid('startsOn')}
              aria-describedby={describedBy('startsOn', true)}
              onChange={(event) => change('startsOn', event.target.value)}
            />,
            values.kind === 'promotion'
              ? t('billing.discounts.hints.promotionDates')
              : t('billing.discounts.hints.optionalDate'),
          )}
          {textField(
            'expiresOn',
            t('billing.discounts.fields.expiresOn'),
            <Input
              id={fieldId('expiresOn')}
              type="date"
              value={values.expiresOn}
              min={values.startsOn || undefined}
              aria-invalid={invalid('expiresOn')}
              aria-describedby={describedBy('expiresOn', true)}
              onChange={(event) => change('expiresOn', event.target.value)}
            />,
            values.kind === 'promotion'
              ? t('billing.discounts.hints.promotionDates')
              : t('billing.discounts.hints.optionalDate'),
          )}
        </div>
        {editing ? (
          <div className="flex flex-col gap-1">
            <label className="flex min-h-6 items-center gap-2 text-sm font-medium">
              <input
                id={fieldId('isActive')}
                type="checkbox"
                checked={values.isActive}
                className="size-4 shrink-0 accent-primary"
                aria-describedby={describedBy('isActive', true)}
                onChange={(event) => change('isActive', event.target.checked)}
              />
              {t('billing.discounts.fields.isActive')}
            </label>
            <p
              id={helpId('isActive')}
              className="text-sm text-muted-foreground"
            >
              {t('billing.discounts.hints.isActive')}
            </p>
            <FieldError
              id={errorId('isActive')}
              message={message(errors.isActive)}
            />
          </div>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.discounts.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.discounts.saving')} showLabel />
            ) : (
              t('billing.discounts.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  const conflict =
    failure instanceof ApiError && failure.code === 'version_conflict';

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-2xl"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>
          {editing
            ? t('billing.discounts.editTitle')
            : t('billing.discounts.add')}
        </DialogTitle>
        <DialogDescription>
          {editing
            ? t('billing.discounts.editNote')
            : t('billing.discounts.createNote')}
        </DialogDescription>
      </DialogHeader>
      {failure ? (
        <ErrorAlert
          error={failure}
          onRetry={
            conflict && onReload
              ? () => {
                  setFailure(null);
                  onReload();
                }
              : undefined
          }
          retryLabel={conflict ? t('billing.prices.reload') : undefined}
        />
      ) : null}
      {content}
    </DialogContent>
  );
}
