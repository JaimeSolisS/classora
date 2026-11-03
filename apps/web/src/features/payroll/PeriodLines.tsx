import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
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
  isCoveredBySalary,
  isNegativeAmount,
  isSessionLine,
  lineKindKey,
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

export interface PeriodLinesProps {
  lines: readonly PayrollLine[];
  /** schedule.view: a session line links to its session (C4). */
  canOpenSession: boolean;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium break-words underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

interface LineText {
  date: string;
  label: string;
  details: string[];
  amount: string;
}

function useLineText() {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate, formatMoney } = useFormatters();
  const L = 'payroll.period.lines';

  return (line: PayrollLine): LineText => {
    const kind = lineKindKey(line.kind);
    const date = line.service_date ?? line.salary_starts_on;
    const details: string[] = [];
    let label = t(`${L}.kinds.${kind}`);

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
    }

    return {
      date: date ? formatLocalDate(date) : t(`${L}.noDate`),
      label,
      details,
      amount: formatMoney(line.amount),
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

function Details({
  line,
  text,
  canOpenSession,
}: {
  line: PayrollLine;
  text: LineText;
  canOpenSession: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col items-start gap-1">
      {text.details.map((detail, index) => (
        <span key={index} className="break-words">
          {detail}
        </span>
      ))}
      {canOpenSession && isSessionLine(line) ? (
        <SessionLink line={line} date={text.date} />
      ) : null}
    </div>
  );
}

export function PeriodLines({ lines, canOpenSession }: PeriodLinesProps) {
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
                  />
                </TableCell>
                <TableCell
                  className="text-right font-medium whitespace-nowrap"
                  data-amount={line.amount}
                >
                  {row.amount}
                </TableCell>
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
              <Details line={line} text={row} canOpenSession={canOpenSession} />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
