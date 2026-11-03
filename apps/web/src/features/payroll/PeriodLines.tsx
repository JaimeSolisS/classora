import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useFormatters } from '@/lib/formatters';
import { formatRate } from '@/lib/money';
import type { PayrollLine } from './api';
import { usePayLabels } from './labels';
import {
  adjustmentKindKey,
  isCoveredBySalary,
  isNegativeAmount,
  isSessionLine,
  lineKindKey,
  periodPath,
} from './periodRules';

// A period's lines (#110b C3-C6, C9), in the API's order, without any query:
// a table from 768 px and cards below it. Every figure is the API's string,
// formatted; nothing is added up here. A session line says its class kind,
// minutes, model and rate, and "k of N" for a per-course share; a line with
// no rate snapshot is covered by the salary. A salary piece says its dates,
// the fraction of the salary it pays and the basis: `0/n` is "paid for no
// days", not an error. A pay item says its type, description and direction,
// a deduction with its minus sign. A kind this page doesn't know shows its
// amount under a generic label.
//
// An adjustment (M7-08b A1) says its kind, the work it corrects (date and
// class kind), the period it comes from as a link, and a manual one's reason;
// a negative one reads as a deduction does. A carry-forward's line in the
// period it left names this period as its origin, so it links to the tutor's
// periods instead (D1). `onCorrect` adds "Correct line" to every line of a
// finalized period (E1); the page decides who sees it.

export interface PeriodLinesProps {
  lines: readonly PayrollLine[];
  /** schedule.view: a session line links to its session (C4). */
  canOpenSession: boolean;
  /** The period shown: an origin naming it isn't linked to itself. */
  periodId?: string;
  /** The period's tutor, for the carry-forward's link to their periods. */
  tutorId?: string;
  /** "Correct line" on each line (a finalized period, payroll.manage). */
  onCorrect?: (line: PayrollLine) => void;
  /** An action or a refresh runs, or the page may be out of date. */
  correctDisabled?: boolean;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium break-words underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

interface LineText {
  date: string;
  label: string;
  details: string[];
  amount: string;
  /** A manual adjustment's reason, as written. */
  reason: string | null;
}

function useLineText() {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate, formatMoney } = useFormatters();
  const L = 'payroll.period.lines';

  return (line: PayrollLine): LineText => {
    const kind = lineKindKey(line.kind);
    const date =
      line.service_date ??
      line.adjustment_service_date ??
      line.salary_starts_on;
    const details: string[] = [];
    let label = t(`${L}.kinds.${kind}`);
    let reason: string | null = null;

    if (isSessionLine(line)) {
      if (line.class_kind) details.push(labels.kind(line.class_kind));
      if (line.minutes !== null) {
        details.push(t(`${L}.minutes`, { count: line.minutes }));
      }
      if (isCoveredBySalary(line)) {
        details.push(t(`${L}.coveredBySalary`));
      } else if (line.rate_model && line.rate !== null) {
        details.push(
          t(`${L}.rate`, {
            model: labels.model(line.rate_model),
            rate: formatRate(line.rate),
          }),
        );
      }
      if (
        line.rate_model === 'per_course' &&
        line.cumulative_sessions !== null &&
        line.cohort_sessions !== null
      ) {
        details.push(
          t(`${L}.courseShare`, {
            k: line.cumulative_sessions,
            n: line.cohort_sessions,
          }),
        );
      }
    } else if (kind === 'salary') {
      if (line.salary_starts_on && line.salary_ends_on) {
        details.push(
          t(`${L}.salaryPiece`, {
            start: formatLocalDate(line.salary_starts_on),
            end: formatLocalDate(line.salary_ends_on),
          }),
        );
      }
      if (
        line.proration_numerator !== null &&
        line.proration_denominator !== null &&
        line.salary_amount !== null
      ) {
        details.push(
          t(`${L}.salaryOf`, {
            fraction: `${line.proration_numerator}/${line.proration_denominator}`,
            amount: formatMoney(line.salary_amount),
          }),
        );
        if (line.proration_numerator === 0) {
          details.push(t(`${L}.paidNoDays`));
        }
      }
      if (line.proration_basis) {
        details.push(
          t(`${L}.salaryBasis`, {
            basis: t(`${L}.bases.${line.proration_basis}`),
          }),
        );
      }
    } else if (kind === 'pay_item') {
      const direction = isNegativeAmount(line.amount) ? 'deduction' : 'bonus';
      label = line.pay_item_type_name
        ? `${line.pay_item_type_name} (${t(`${L}.directions.${direction}`)})`
        : t(`${L}.directions.${direction}`);
      if (line.pay_item_description) details.push(line.pay_item_description);
    } else if (kind === 'adjustment') {
      const adjustment = adjustmentKindKey(line.adjustment_kind);
      label = adjustment
        ? t(`${L}.adjustmentKinds.${adjustment}`)
        : t(`${L}.kinds.adjustment`);
      if (line.adjustment_service_date) {
        details.push(
          t(`${L}.adjustmentWork`, {
            date: formatLocalDate(line.adjustment_service_date),
          }),
        );
      }
      if (line.adjustment_class_kind) {
        details.push(labels.kind(line.adjustment_class_kind));
      }
      reason = line.adjustment_reason;
    }

    return {
      date: date ? formatLocalDate(date) : t(`${L}.noDate`),
      label,
      details,
      amount: formatMoney(line.amount),
      reason,
    };
  };
}

function SessionLink({ line, date }: { line: PayrollLine; date: string }) {
  const { t } = useTranslation();
  if (!line.class_session_id) return null;
  return (
    <Link
      to={`/schedule/sessions/${encodeURIComponent(line.class_session_id)}`}
      aria-label={t('payroll.period.lines.openSessionFor', { date })}
      className={LINK_CLASS}
    >
      {t('payroll.period.lines.openSession')}
    </Link>
  );
}

function OriginLink({
  line,
  periodId,
  tutorId,
}: {
  line: PayrollLine;
  periodId?: string;
  tutorId?: string;
}) {
  const { t } = useTranslation();
  const origin = line.adjustment_origin_period_id;
  if (line.kind !== 'adjustment' || !origin) return null;
  if (origin !== periodId) {
    return (
      <Link to={periodPath(origin)} className={LINK_CLASS} data-origin-link>
        {t('payroll.period.lines.originPeriod')}
      </Link>
    );
  }
  // A carry-forward's line where it left: it names this period (D1).
  if (line.adjustment_kind === 'carry_forward' && tutorId) {
    return (
      <Link
        to={`/payroll/periods?tutor=${encodeURIComponent(tutorId)}`}
        className={LINK_CLASS}
        data-carry-link
      >
        {t('payroll.period.lines.carriedTo')}
      </Link>
    );
  }
  return null;
}

function Details({
  line,
  text,
  canOpenSession,
  periodId,
  tutorId,
}: {
  line: PayrollLine;
  text: LineText;
  canOpenSession: boolean;
  periodId?: string;
  tutorId?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col items-start gap-1">
      {text.details.map((detail, index) => (
        <span key={index} className="break-words">
          {detail}
        </span>
      ))}
      {text.reason !== null ? (
        <span className="break-words whitespace-pre-line" data-line-reason>
          {t('payroll.period.lines.reason', { reason: text.reason })}
        </span>
      ) : null}
      {canOpenSession && isSessionLine(line) ? (
        <SessionLink line={line} date={text.date} />
      ) : null}
      <OriginLink line={line} periodId={periodId} tutorId={tutorId} />
    </div>
  );
}

function CorrectButton({
  line,
  text,
  onCorrect,
  disabled,
}: {
  line: PayrollLine;
  text: LineText;
  onCorrect: (line: PayrollLine) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="min-h-11 md:min-h-8"
      disabled={disabled}
      aria-label={t('payroll.period.lines.correctFor', {
        line: text.label,
        date: text.date,
      })}
      data-correct-line={line.id}
      onClick={() => onCorrect(line)}
    >
      {t('payroll.period.lines.correct')}
    </Button>
  );
}

export function PeriodLines({
  lines,
  canOpenSession,
  periodId,
  tutorId,
  onCorrect,
  correctDisabled = false,
}: PeriodLinesProps) {
  const { t } = useTranslation();
  const text = useLineText();
  const L = 'payroll.period.lines';

  if (lines.length === 0) {
    return (
      <p className="text-sm" data-period-no-lines>
        {t(`${L}.empty`)}
      </p>
    );
  }
  const rows = lines.map((line) => ({ line, text: text(line) }));
  return (
    <>
      <div className="hidden md:block" data-period-lines-table>
        <Table>
          <TableCaption className="sr-only">{t(`${L}.caption`)}</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t(`${L}.columns.date`)}</TableHead>
              <TableHead scope="col">{t(`${L}.columns.line`)}</TableHead>
              <TableHead scope="col">{t(`${L}.columns.details`)}</TableHead>
              <TableHead scope="col" className="text-right">
                {t(`${L}.columns.amount`)}
              </TableHead>
              {onCorrect ? (
                <TableHead scope="col">
                  <span className="sr-only">{t(`${L}.columns.actions`)}</span>
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ line, text: row }) => (
              <TableRow
                key={line.id}
                data-line-id={line.id}
                data-line-kind={line.kind}
                className="align-top"
              >
                <TableCell>{row.date}</TableCell>
                <TableCell className="whitespace-normal">{row.label}</TableCell>
                <TableCell className="whitespace-normal">
                  <Details
                    line={line}
                    text={row}
                    canOpenSession={canOpenSession}
                    periodId={periodId}
                    tutorId={tutorId}
                  />
                </TableCell>
                <TableCell
                  className="text-right font-medium whitespace-nowrap"
                  data-amount={line.amount}
                >
                  {row.amount}
                </TableCell>
                {onCorrect ? (
                  <TableCell>
                    <CorrectButton
                      line={line}
                      text={row}
                      onCorrect={onCorrect}
                      disabled={correctDisabled}
                    />
                  </TableCell>
                ) : null}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul className="flex flex-col gap-3 md:hidden" data-period-lines-cards>
        {rows.map(({ line, text: row }) => (
          <li
            key={line.id}
            data-line-card={line.id}
            className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-medium break-words">{row.label}</span>
              <span className="font-medium whitespace-nowrap">
                {row.amount}
              </span>
            </div>
            <p className="text-sm">{row.date}</p>
            <div className="text-sm">
              <Details
                line={line}
                text={row}
                canOpenSession={canOpenSession}
                periodId={periodId}
                tutorId={tutorId}
              />
            </div>
            {onCorrect ? (
              <div>
                <CorrectButton
                  line={line}
                  text={row}
                  onCorrect={onCorrect}
                  disabled={correctDisabled}
                />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  );
}
