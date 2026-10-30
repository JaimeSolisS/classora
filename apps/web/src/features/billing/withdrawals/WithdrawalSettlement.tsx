import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { isCancelledReauth } from '@/features/billing/accounts/corrections';
import { SELECT_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import { useIdempotentSubmission } from '@/lib/idempotency';
import { formatPackageUnits } from '@/lib/packages';
import type { LocalDate } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { PaymentMethod } from '../accounts/api';
import { CorrectionField } from '../accounts/CorrectionField';
import { REASON_MAX } from '../accounts/corrections';
import { useAccountLabels } from '../accounts/labels';
import { REFERENCE_MAX } from '../accounts/rules';
import type {
  WithdrawalCreditConfirm,
  WithdrawalLine,
  WithdrawalPreview,
  WithdrawalRefundConfirm,
} from './api';
import {
  emptySettlement,
  estimateTotals,
  hasErrors,
  isBlocked,
  isReadOnly,
  mergeStale,
  minutesAsHours,
  outcomeAvailability,
  refetchesPreview,
  SETTLEMENT_FIELD_ORDER,
  settlementBody,
  settlementServerErrors,
  stalePreview,
  validateSettlement,
  type SettlementErrors,
  type SettlementField,
  type SettlementValues,
  type TotalKey,
} from './rules';
import { useWithdrawalLabels } from './labels';

// The withdrawal settlement panel (#387 A; #101 C1, C2), without any query:
// the header (student, family, subject, the withdrawal instant), the
// consumed and unused sessions with hours (or the package's units), the
// lines (kind, date or period, net, paid, "U of D", the policy, the
// credit, a "posted now" tag), the automatic session credits (collapsed),
// the totals, the warnings, and either the blocking reasons (no confirm)
// or the form: the outcome (Account credit with billing.manage, Refund
// with finance.refund.approve, disabled when nothing paid is freed),
// "Adjust lines" (collapsed; 0.00…net per line, the totals become an
// estimate), the refund's date, method and reference, and the required
// reason. Confirm computes the Idempotency-Key from the body BEFORE the
// call (#88), so apiCall's retry after the password dialog resends it;
// nothing changes before the server answers. A `409
// withdrawal_preview_stale` hands the fresh preview to `onStale` (the
// caller puts it in the query, so `preview` is always the live data, #386
// D2), keeps the reason, the outcome and the refund fields, drops the
// overrides of changed lines, highlights the changed totals and says
// "Something changed; review and confirm again"; the next confirm sends
// the new fingerprint, so a new key. Any other 409 is an alert and reads
// the preview again (`onRefetch`).

export interface WithdrawalSettlementProps {
  /** The live preview (the query's data). */
  preview: WithdrawalPreview;
  /** The group, cohort or package name (the preview has only its id). */
  subjectName: string;
  /** billing.manage: "Account credit". */
  canCredit: boolean;
  /** finance.refund.approve: "Refund". */
  canRefund: boolean;
  /** Every payment method; null while they load (Refund only). */
  methods: readonly PaymentMethod[] | null;
  today: LocalDate;
  /** The reason typed in the first step (a withdrawal from a roster). */
  initialReason?: string;
  onConfirm: (
    body: WithdrawalCreditConfirm | WithdrawalRefundConfirm,
    idempotencyKey: string,
  ) => Promise<void>;
  /** A stale answer's fresh preview (put it in the query). */
  onStale: (fresh: WithdrawalPreview) => void;
  /** Read the preview again (after another 409). */
  onRefetch: () => void;
  onCancel: () => void;
  /** The two-step dialog's "Back" (to the withdrawal's own step). */
  onBack?: () => void;
  /** Keeps the dialog open while a confirm runs. */
  busyRef: { current: boolean };
  /** Stories. */
  initialValues?: Partial<SettlementValues>;
  initialAdjusting?: boolean;
  initialChanged?: TotalKey[];
  initialStale?: boolean;
}

const CHANGED_CLASS = 'rounded-sm bg-accent px-1 font-semibold';

export function WithdrawalSettlement({
  preview,
  subjectName,
  canCredit,
  canRefund,
  methods,
  today,
  initialReason = '',
  onConfirm,
  onStale,
  onRefetch,
  onCancel,
  onBack,
  busyRef,
  initialValues,
  initialAdjusting = false,
  initialChanged = [],
  initialStale = false,
}: WithdrawalSettlementProps) {
  const { t } = useTranslation();
  const money = useAccountLabels();
  const labels = useWithdrawalLabels();
  const id = useId();
  const submission = useIdempotentSubmission();
  const availability = outcomeAvailability({ canCredit, canRefund }, preview);
  const readOnly = isReadOnly(availability);
  const blocked = isBlocked(preview);
  const [values, setValues] = useState<SettlementValues>(() => ({
    ...emptySettlement(availability, today, initialReason),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<SettlementErrors>({});
  const [adjusting, setAdjusting] = useState(initialAdjusting);
  const [changed, setChanged] = useState<TotalKey[]>(initialChanged);
  const [stale, setStale] = useState(initialStale);
  // Bumped on each stale answer: the notice takes the focus.
  const [staleCount, setStaleCount] = useState(0);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const staleRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (staleCount > 0) staleRef.current?.focus();
  }, [staleCount]);
  const fieldId = (field: SettlementField) => `${id}-${field}`;
  const overrideId = (chargeId: string) => `${id}-override-${chargeId}`;
  const estimate = estimateTotals(preview, values.overrides);
  const activeMethods = (methods ?? []).filter((method) => method.is_active);

  function change<F extends keyof SettlementValues>(
    field: F,
    value: SettlementValues[F],
  ) {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  }

  function changeOverride(chargeId: string, value: string) {
    setValues((current) => ({
      ...current,
      overrides: { ...current.overrides, [chargeId]: value },
    }));
    setErrors((current) => {
      const rest = { ...current.overrides };
      delete rest[chargeId];
      return { ...current, overrides: rest };
    });
  }

  function focusFirst(found: SettlementErrors) {
    const overridden = Object.keys(found.overrides ?? {})[0];
    if (overridden) {
      document.getElementById(overrideId(overridden))?.focus();
      return;
    }
    const field = SETTLEMENT_FIELD_ORDER.find((name) => found[name]);
    if (field === 'outcome') {
      document
        .querySelector<HTMLInputElement>(`input[name="${id}-outcome"]`)
        ?.focus();
    } else if (field) {
      document.getElementById(fieldId(field))?.focus();
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) return;
    const found = validateSettlement(values, preview, today);
    setErrors(found);
    if (hasErrors(found)) {
      if (found.overrides) setAdjusting(true);
      focusFirst(found);
      return;
    }
    setFailure(null);
    const body = settlementBody(values, preview);
    // Outside the call (#88): apiCall's retry resends this key.
    const key = submission.keyFor(body);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onConfirm(body, key);
      submission.succeeded();
    } catch (caught) {
      const fresh = stalePreview(caught);
      if (fresh) {
        const merged = mergeStale(preview, fresh, values);
        setValues(merged.values);
        setChanged(merged.changed);
        setStale(true);
        setStaleCount((count) => count + 1);
        submission.reset();
        onStale(fresh);
      } else if (isCancelledReauth(caught)) {
        // The password dialog was cancelled: keep the form, no alert.
      } else {
        const fromServer = settlementServerErrors(caught);
        if (fromServer) {
          setErrors(fromServer);
          focusFirst(fromServer);
        } else {
          setFailure(caught);
          if (refetchesPreview(caught)) onRefetch();
        }
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  const totalClass = (key: TotalKey) =>
    changed.includes(key) ? CHANGED_CLASS : undefined;
  const changedMark = (key: TotalKey) =>
    changed.includes(key) ? (
      <span className="ml-1 text-xs font-medium">
        ({t('billing.withdrawals.totals.changed')})
      </span>
    ) : null;

  const totalRows: { key: TotalKey; label: string; value: string }[] = [
    {
      key: 'credit',
      label: t('billing.withdrawals.totals.credit'),
      value: money.cents(estimate.credit),
    },
    {
      key: 'reduces_debt',
      label: t('billing.withdrawals.totals.reducesDebt'),
      value: money.cents(estimate.reducesDebt),
    },
    {
      key: 'refundable_cash',
      label: t('billing.withdrawals.totals.refundableCash'),
      value: money.cents(estimate.refundableCash),
    },
    {
      key: 'account_credit_after_credit',
      label: t('billing.withdrawals.totals.accountCreditAfter'),
      value: money.cents(estimate.accountCreditAfter),
    },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-6" data-withdrawal-preview>
      <SettlementSummary preview={preview} subjectName={subjectName} />

      <section aria-labelledby={`${id}-lines`} className="flex flex-col gap-3">
        <h3 id={`${id}-lines`} className="font-semibold">
          {t('billing.withdrawals.lines.title')}
        </h3>
        {preview.lines.length === 0 ? (
          <p className="text-sm">{t('billing.withdrawals.lines.none')}</p>
        ) : (
          <LinesTable
            preview={preview}
            adjusting={adjusting && !blocked && !readOnly}
            values={values}
            errors={errors}
            overrideId={overrideId}
            onOverride={changeOverride}
          />
        )}
        {preview.lines.some((line) => line.charge_id !== null) &&
        !blocked &&
        !readOnly ? (
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-expanded={adjusting}
              onClick={() => setAdjusting((open) => !open)}
            >
              {adjusting
                ? t('billing.withdrawals.adjust.hide')
                : t('billing.withdrawals.adjust.toggle')}
            </Button>
          </div>
        ) : null}
      </section>

      <AutomaticCredits preview={preview} />

      <section aria-labelledby={`${id}-totals`} className="flex flex-col gap-2">
        <h3 id={`${id}-totals`} className="font-semibold">
          {t('billing.withdrawals.totals.title')}
        </h3>
        {estimate.estimated ? (
          <p className="text-sm text-muted-foreground" data-estimate>
            {t('billing.withdrawals.totals.estimate')}
          </p>
        ) : null}
        <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {totalRows.map((row) => (
            <div
              key={row.key}
              className="flex flex-wrap justify-between gap-2"
              data-total={row.key}
              data-changed={changed.includes(row.key) ? 'true' : undefined}
            >
              <dt>{row.label}</dt>
              <dd className={cn('font-medium', totalClass(row.key))}>
                {row.value}
                {changedMark(row.key)}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <Warnings preview={preview} />

      {stale ? (
        <p
          ref={staleRef}
          tabIndex={-1}
          role="alert"
          className="text-sm font-medium outline-none"
        >
          {t('billing.withdrawals.stale')}
        </p>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}

      {blocked ? (
        <>
          <Blocking preview={preview} />
          <DialogFooter>
            {onBack ? (
              <Button type="button" variant="outline" onClick={onBack}>
                {t('billing.withdrawals.back')}
              </Button>
            ) : null}
            <Button type="button" variant="outline" onClick={onCancel}>
              {t('billing.withdrawals.close')}
            </Button>
          </DialogFooter>
        </>
      ) : readOnly ? (
        <>
          <p className="text-sm">{t('billing.withdrawals.outcome.readOnly')}</p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onCancel}>
              {t('billing.withdrawals.close')}
            </Button>
          </DialogFooter>
        </>
      ) : (
        <form
          method="post"
          noValidate
          onSubmit={(event) => void submit(event)}
          className="flex min-w-0 flex-col gap-4"
        >
          <fieldset
            className="flex min-w-0 flex-col gap-2"
            aria-describedby={
              errors.outcome ? `${fieldId('outcome')}-error` : undefined
            }
          >
            <legend className="mb-2 text-sm font-medium">
              {t('billing.withdrawals.outcome.legend')}
            </legend>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              {availability.credit ? (
                <label className="flex min-h-6 items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name={`${id}-outcome`}
                    value="account_credit"
                    className="size-4 accent-primary"
                    checked={values.outcome === 'account_credit'}
                    onChange={() => change('outcome', 'account_credit')}
                  />
                  {t('billing.withdrawals.outcomes.account_credit')}
                </label>
              ) : null}
              {availability.refund ? (
                <label className="flex min-h-6 items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name={`${id}-outcome`}
                    value="refund"
                    className="size-4 accent-primary"
                    disabled={availability.refundDisabled}
                    aria-describedby={
                      availability.refundDisabled
                        ? `${id}-refund-hint`
                        : undefined
                    }
                    checked={values.outcome === 'refund'}
                    onChange={() => change('outcome', 'refund')}
                  />
                  {t('billing.withdrawals.outcomes.refund')}
                </label>
              ) : null}
            </div>
            {availability.refund && availability.refundDisabled ? (
              <p
                id={`${id}-refund-hint`}
                className="text-sm text-muted-foreground"
              >
                {t('billing.withdrawals.outcome.refundHint')}
              </p>
            ) : null}
            {errors.outcome ? (
              <p
                id={`${fieldId('outcome')}-error`}
                className="text-sm text-destructive"
              >
                {labels.message(errors.outcome)}
              </p>
            ) : null}
          </fieldset>
          {values.outcome === 'refund' ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <CorrectionField
                id={fieldId('refundedOn')}
                label={t('billing.withdrawals.fields.refundedOn')}
                error={errors.refundedOn}
              >
                {(wiring) => (
                  <Input
                    {...wiring}
                    type="date"
                    max={today}
                    value={values.refundedOn}
                    onChange={(event) =>
                      change('refundedOn', event.target.value)
                    }
                  />
                )}
              </CorrectionField>
              <CorrectionField
                id={fieldId('method')}
                label={t('billing.withdrawals.fields.method')}
                error={errors.method}
              >
                {(wiring) => (
                  <select
                    {...wiring}
                    className={SELECT_CLASS}
                    value={values.method}
                    disabled={methods === null}
                    onChange={(event) => change('method', event.target.value)}
                  >
                    <option value="">{t('billing.prices.choose')}</option>
                    {activeMethods.map((method) => (
                      <option key={method.code} value={method.code}>
                        {money.method(method.code)}
                      </option>
                    ))}
                  </select>
                )}
              </CorrectionField>
              <CorrectionField
                id={fieldId('reference')}
                label={t('billing.withdrawals.fields.reference')}
                error={errors.reference}
              >
                {(wiring) => (
                  <Input
                    {...wiring}
                    type="text"
                    autoComplete="off"
                    maxLength={REFERENCE_MAX}
                    value={values.reference}
                    onChange={(event) =>
                      change('reference', event.target.value)
                    }
                  />
                )}
              </CorrectionField>
            </div>
          ) : null}
          <CorrectionField
            id={fieldId('reason')}
            label={t('billing.withdrawals.fields.reason')}
            error={errors.reason}
          >
            {(wiring) => (
              <textarea
                {...wiring}
                rows={2}
                className={TEXTAREA_CLASS}
                maxLength={REASON_MAX}
                value={values.reason}
                onChange={(event) => change('reason', event.target.value)}
              />
            )}
          </CorrectionField>
          <p className="text-sm text-muted-foreground">
            {t('billing.withdrawals.passwordNote')}
          </p>
          <DialogFooter>
            {onBack ? (
              <Button
                type="button"
                variant="outline"
                disabled={submitting}
                onClick={onBack}
              >
                {t('billing.withdrawals.back')}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={onCancel}
            >
              {t('billing.withdrawals.cancel')}
            </Button>
            <Button type="submit" variant="destructive" disabled={submitting}>
              {submitting ? (
                <Spinner
                  label={t('billing.withdrawals.confirming')}
                  showLabel
                />
              ) : (
                t('billing.withdrawals.confirm')
              )}
            </Button>
          </DialogFooter>
        </form>
      )}
    </div>
  );
}

/** The header and the counts (A1). */
function SettlementSummary({
  preview,
  subjectName,
}: {
  preview: WithdrawalPreview;
  subjectName: string;
}) {
  const { t } = useTranslation();
  const money = useAccountLabels();
  const labels = useWithdrawalLabels();
  const rows: { label: string; value: string }[] = [
    {
      label: t('billing.withdrawals.header.student'),
      value: preview.student.name,
    },
    {
      label: t('billing.withdrawals.header.family'),
      value: preview.family?.name ?? t('billing.withdrawals.header.noFamily'),
    },
    subjectName
      ? { label: labels.subjectKind(preview.subject.kind), value: subjectName }
      : {
          label: t('billing.withdrawals.header.subject'),
          value: labels.subjectKind(preview.subject.kind),
        },
    {
      label: t('billing.withdrawals.header.at'),
      value: money.instantDate(preview.at),
    },
  ];
  const { sessions, units } = preview;
  if (sessions) {
    rows.push(
      {
        label: t('billing.withdrawals.counts.consumed'),
        value: t('billing.withdrawals.counts.sessions', {
          count: sessions.consumed,
          hours: minutesAsHours(sessions.consumed_minutes),
        }),
      },
      {
        label: t('billing.withdrawals.counts.unused'),
        value: t('billing.withdrawals.counts.sessions', {
          count: sessions.unused,
          hours: minutesAsHours(sessions.unused_minutes),
        }),
      },
    );
  }
  if (units) {
    const show = (value: number) => formatPackageUnits(units.unit, value, t);
    rows.push(
      {
        label: t('billing.withdrawals.counts.granted'),
        value: show(units.granted),
      },
      {
        label: t('billing.withdrawals.counts.consumedUnits'),
        value: show(units.consumed),
      },
      {
        label: t('billing.withdrawals.counts.remaining'),
        value: show(units.remaining),
      },
    );
  }
  return (
    <dl
      className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2"
      data-withdrawal-summary
    >
      {rows.map((row) => (
        <div key={row.label} className="flex flex-wrap gap-x-2">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="font-medium break-words">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function LinesTable({
  preview,
  adjusting,
  values,
  errors,
  overrideId,
  onOverride,
}: {
  preview: WithdrawalPreview;
  adjusting: boolean;
  values: SettlementValues;
  errors: SettlementErrors;
  overrideId: (chargeId: string) => string;
  onOverride: (chargeId: string, value: string) => void;
}) {
  const { t } = useTranslation();
  const money = useAccountLabels();
  const labels = useWithdrawalLabels();
  const unit = preview.units?.unit ?? null;

  function basis(line: WithdrawalLine) {
    const { unused, denominator } = line.basis;
    if (line.kind === 'package_purchase' && unit === 'minutes') {
      return t('billing.withdrawals.lines.basisUnits', {
        unused: formatPackageUnits('minutes', unused, t),
        total: formatPackageUnits('minutes', denominator, t),
      });
    }
    return t('billing.withdrawals.lines.basis', {
      unused,
      count: denominator,
    });
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">
              {t('billing.withdrawals.lines.columns.kind')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.withdrawals.lines.columns.period')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.withdrawals.lines.columns.net')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.withdrawals.lines.columns.paid')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.withdrawals.lines.columns.basis')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.withdrawals.lines.columns.policy')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.withdrawals.lines.columns.credit')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {preview.lines.map((line, index) => {
            const kind = money.chargeKind(line.kind);
            const period = line.service_period_end
              ? t('billing.withdrawals.lines.period', {
                  from: money.date(line.service_date),
                  to: money.date(line.service_period_end),
                })
              : money.date(line.service_date);
            const chargeId = line.charge_id;
            const error = chargeId ? errors.overrides?.[chargeId] : undefined;
            return (
              <TableRow
                key={chargeId ?? `now-${index}`}
                data-withdrawal-line={line.kind}
                data-line-credit={line.computed_credit}
              >
                <TableCell className="whitespace-normal">
                  <span className="flex flex-wrap items-center gap-2">
                    {kind}
                    {line.posting === 'now' ? (
                      <span className="rounded-md border border-border px-1.5 text-xs font-medium">
                        {t('billing.withdrawals.lines.postedNow')}
                      </span>
                    ) : null}
                  </span>
                </TableCell>
                <TableCell>{period}</TableCell>
                <TableCell>{money.money(line.net)}</TableCell>
                <TableCell>{money.money(line.allocated)}</TableCell>
                <TableCell>{basis(line)}</TableCell>
                <TableCell className="whitespace-normal">
                  {labels.policy(line.policy)}
                </TableCell>
                <TableCell>
                  {adjusting && chargeId ? (
                    <div className="flex min-w-32 flex-col gap-1">
                      <label htmlFor={overrideId(chargeId)} className="sr-only">
                        {t('billing.withdrawals.adjust.fieldFor', {
                          line: `${kind} ${period}`,
                        })}
                      </label>
                      <Input
                        id={overrideId(chargeId)}
                        type="text"
                        inputMode="decimal"
                        autoComplete="off"
                        aria-invalid={error ? true : undefined}
                        aria-describedby={`${overrideId(chargeId)}-help`}
                        value={
                          values.overrides[chargeId] ?? line.computed_credit
                        }
                        onChange={(event) =>
                          onOverride(chargeId, event.target.value)
                        }
                      />
                      <span
                        id={`${overrideId(chargeId)}-help`}
                        className={
                          error
                            ? 'text-xs text-destructive'
                            : 'text-xs text-muted-foreground'
                        }
                      >
                        {error
                          ? labels.message(error, {
                              net: money.money(line.net),
                            })
                          : t('billing.withdrawals.adjust.help', {
                              net: money.money(line.net),
                            })}
                      </span>
                    </div>
                  ) : (
                    money.money(line.computed_credit)
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

/** The #92 automatic session credits, collapsed (A1). */
function AutomaticCredits({ preview }: { preview: WithdrawalPreview }) {
  const { t } = useTranslation();
  const money = useAccountLabels();
  if (preview.automatic_credits.length === 0) return null;
  return (
    <details className="text-sm" data-automatic-credits>
      <summary className="min-h-6 cursor-pointer rounded-md font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring">
        {t('billing.withdrawals.automatic.summary', {
          count: preview.automatic_credits.length,
          total: money.money(preview.totals.automatic_credits),
        })}
      </summary>
      <p className="mt-2 text-muted-foreground">
        {t('billing.withdrawals.automatic.note')}
      </p>
      <ul className="mt-2 flex flex-col gap-1">
        {preview.automatic_credits.map((credit) => (
          <li key={credit.charge_id} className="flex flex-wrap gap-2">
            <Link
              to={`/schedule/sessions/${encodeURIComponent(credit.class_session_id)}`}
              className={TEXT_LINK_CLASS}
            >
              {t('billing.withdrawals.automatic.session')}
            </Link>
            <span>{money.money(credit.amount)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function SessionLinks({
  ids,
  label,
}: {
  ids: readonly string[];
  label: string;
}) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1">
      {ids.map((sessionId, index) => (
        <li key={sessionId}>
          <Link
            to={`/schedule/sessions/${encodeURIComponent(sessionId)}`}
            className={TEXT_LINK_CLASS}
          >
            {`${label} ${index + 1}`}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Warnings({ preview }: { preview: WithdrawalPreview }) {
  const { t } = useTranslation();
  const labels = useWithdrawalLabels();
  if (preview.warnings.length === 0) return null;
  return (
    <section className="flex flex-col gap-2 text-sm" data-withdrawal-warnings>
      <h3 className="font-semibold">
        {t('billing.withdrawals.warnings.title')}
      </h3>
      <ul className="flex list-disc flex-col gap-2 pl-5">
        {preview.warnings.map((warning) => (
          <li key={warning.code} data-warning={warning.code}>
            <p>{labels.warning(warning.code, warning.count)}</p>
            {warning.class_session_ids &&
            warning.class_session_ids.length > 0 ? (
              <SessionLinks
                ids={warning.class_session_ids}
                label={t('billing.withdrawals.warnings.session')}
              />
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function detailText(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function detailList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

/** A2: why the withdrawal can't be confirmed, with where to go. */
function Blocking({ preview }: { preview: WithdrawalPreview }) {
  const { t } = useTranslation();
  const labels = useWithdrawalLabels();
  return (
    <section
      className="flex flex-col gap-2 rounded-md border border-border p-3 text-sm"
      data-withdrawal-blocking
    >
      <h3 className="font-semibold">
        {t('billing.withdrawals.blocking.title')}
      </h3>
      <ul className="flex flex-col gap-3">
        {preview.blocking.map((item) => {
          const details = item.details ?? {};
          let extra = null;
          if (item.code === 'withdrawal_exists') {
            const withdrawalId = detailText(details.withdrawal_id);
            extra =
              withdrawalId && preview.family ? (
                <Link
                  to={`/billing/accounts/${encodeURIComponent(preview.family.id)}?withdrawal=${encodeURIComponent(withdrawalId)}`}
                  className={TEXT_LINK_CLASS}
                >
                  {t('billing.withdrawals.blocking.viewWithdrawal')}
                </Link>
              ) : null;
          } else if (item.code === 'withdrawal_closeout_pending') {
            extra = (
              <SessionLinks
                ids={detailList(details.class_session_ids)}
                label={t('billing.withdrawals.blocking.closeoutSession')}
              />
            );
          } else if (item.code === 'student_has_no_family') {
            extra = (
              <Link
                to={`/students/${encodeURIComponent(preview.student.id)}`}
                className={TEXT_LINK_CLASS}
              >
                {t('billing.withdrawals.blocking.openStudent')}
              </Link>
            );
          }
          return (
            <li
              key={item.code}
              data-blocking={item.code}
              className="flex flex-col gap-1"
            >
              <p>{labels.blocking(item.code, detailText(details.reason))}</p>
              {extra}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * A stored preview, read-only (#387 B4: the withdrawals drawer): the
 * header, the counts, the lines with their computed credits, the automatic
 * credits and the totals as they were previewed.
 */
export function PreviewSnapshot({ preview }: { preview: WithdrawalPreview }) {
  const { t } = useTranslation();
  const money = useAccountLabels();
  const rows: { key: TotalKey; label: string }[] = [
    { key: 'credit', label: t('billing.withdrawals.totals.credit') },
    { key: 'reduces_debt', label: t('billing.withdrawals.totals.reducesDebt') },
    {
      key: 'refundable_cash',
      label: t('billing.withdrawals.totals.refundableCash'),
    },
    {
      key: 'account_credit_after_credit',
      label: t('billing.withdrawals.totals.accountCreditAfter'),
    },
  ];
  return (
    <div className="flex min-w-0 flex-col gap-4" data-withdrawal-snapshot>
      <SettlementSummary preview={preview} subjectName="" />
      {preview.lines.length === 0 ? (
        <p className="text-sm">{t('billing.withdrawals.lines.none')}</p>
      ) : (
        <LinesTable
          preview={preview}
          adjusting={false}
          values={{
            outcome: '',
            reason: '',
            overrides: {},
            refundedOn: '',
            method: '',
            reference: '',
          }}
          errors={{}}
          overrideId={(chargeId) => chargeId}
          onOverride={() => {}}
        />
      )}
      <AutomaticCredits preview={preview} />
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {rows.map((row) => (
          <div key={row.key} className="flex flex-wrap justify-between gap-2">
            <dt>{row.label}</dt>
            <dd className="font-medium">
              {money.money(preview.totals[row.key])}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
