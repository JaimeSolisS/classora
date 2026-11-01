import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { StateBadge } from '@/features/users/admin/StateBadge';
import { useFormatters } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import type {
  ClassKind,
  CompensationRate,
  CompensationStatus,
  PayTerms,
  TutorCompensation,
} from './api';
import { usePayLabels } from './labels';
import {
  CLASS_KINDS,
  hasCurrentSalary,
  orderVersions,
  paySummary,
} from './rules';

// One tutor's pay, read-only by default (#425 C1-C4): the header, the summary
// of the model, the terms and the rates by class kind, each version with its
// date, values and a status badge. The current version comes first and the
// past ones stay visible. `actions` adds the add and delete controls; My pay
// passes none, so nothing here can act on its behalf (F1). Nothing computes
// an amount: every value is the API's string, formatted.

export interface CompensationActions {
  onAddTerms: () => void;
  onAddRate: () => void;
  onDeleteTerms: (terms: PayTerms) => void;
  onDeleteRate: (rate: CompensationRate) => void;
}

export function PayStatusBadge({ status }: { status: CompensationStatus }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="pay-status-badge"
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground',
        status === 'current'
          ? 'border-foreground bg-background'
          : status === 'upcoming'
            ? 'border-dashed border-border bg-background'
            : 'border-border bg-muted',
      )}
    >
      {t(`payroll.status.${status}`)}
    </span>
  );
}

function VersionRow({
  id,
  status,
  date,
  value,
  deleteLabel,
  onDelete,
}: {
  id: string;
  status: CompensationStatus;
  /** "From {date}", left out when the value already names the date. */
  date: string | null;
  value: string;
  deleteLabel: string;
  onDelete?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <li
      data-version-id={id}
      data-version-status={status}
      className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-border p-3"
    >
      <div className="flex min-w-0 flex-1 basis-48 flex-col gap-1">
        <span className="font-medium break-words">{value}</span>
        {date ? (
          <span className="text-sm text-muted-foreground">{date}</span>
        ) : null}
      </div>
      <PayStatusBadge status={status} />
      {onDelete ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="min-h-11 md:min-h-8"
          aria-label={deleteLabel}
          onClick={onDelete}
        >
          {t('payroll.compensation.delete')}
        </Button>
      ) : null}
    </li>
  );
}

function SectionHeading({
  id,
  title,
  action,
}: {
  id: string;
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {action}
    </div>
  );
}

function RatesOfKind({
  kind,
  rates,
  salaried,
  actions,
}: {
  kind: ClassKind;
  rates: readonly CompensationRate[];
  salaried: boolean;
  actions?: CompensationActions;
}) {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const heading = useId();
  return (
    <section
      aria-labelledby={heading}
      data-class-kind={kind}
      className="flex flex-col gap-2"
    >
      <h3 id={heading} className="font-medium">
        {labels.kind(kind)}
      </h3>
      {rates.length === 0 ? (
        <p className="text-sm" data-no-rate>
          {salaried
            ? t('payroll.compensation.coveredBySalary')
            : t('payroll.compensation.noRate')}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {orderVersions(rates).map((rate) => {
            const date = labels.date(rate);
            return (
              <VersionRow
                key={rate.id}
                id={rate.id}
                status={rate.status}
                date={
                  rate.rate === null
                    ? null
                    : t('payroll.compensation.since', { date })
                }
                value={labels.rate(rate)}
                deleteLabel={t('payroll.compensation.deleteRate', {
                  kind: labels.kind(kind),
                  date,
                })}
                onDelete={
                  actions ? () => actions.onDeleteRate(rate) : undefined
                }
              />
            );
          })}
        </ul>
      )}
    </section>
  );
}

export interface CompensationViewProps {
  compensation: TutorCompensation;
  /** The page's title; the tutor's name when left out. */
  title?: string;
  /** A note under the header (a read-only reason, C9). */
  note?: string;
  /** Replaces both sections when the tutor has no version (F2). */
  emptyMessage?: string;
  actions?: CompensationActions;
}

export function CompensationView({
  compensation,
  title,
  note,
  emptyMessage,
  actions,
}: CompensationViewProps) {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const ids = { terms: `${id}-terms`, rates: `${id}-rates` };
  const { tutor, today, terms, rates } = compensation;
  const summary = paySummary(compensation);
  const salaried = hasCurrentSalary(compensation);
  const empty =
    emptyMessage !== undefined && terms.length === 0 && rates.length === 0;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold break-words">
          {title ?? tutor.display_name}
        </h1>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          {title ? (
            <span className="break-words">{tutor.display_name}</span>
          ) : null}
          <StateBadge state={tutor.state} />
          <span className="text-muted-foreground">
            {t('payroll.compensation.today', {
              date: formatLocalDate(today),
            })}
          </span>
        </div>
        {summary.length > 0 ? (
          <p className="font-medium" data-pay-summary>
            {summary
              .map((key) => t(`payroll.compensation.summary.${key}`))
              .join(' · ')}
          </p>
        ) : null}
        {note ? (
          <p className="text-sm" data-pay-note>
            {note}
          </p>
        ) : null}
      </div>
      {empty ? (
        <p data-pay-empty>{emptyMessage}</p>
      ) : (
        <>
          <section aria-labelledby={ids.terms} className="flex flex-col gap-4">
            <SectionHeading
              id={ids.terms}
              title={t('payroll.compensation.termsTitle')}
              action={
                actions ? (
                  <Button
                    type="button"
                    className="min-h-11 md:min-h-9"
                    onClick={actions.onAddTerms}
                  >
                    {t('payroll.termsDialog.title')}
                  </Button>
                ) : undefined
              }
            />
            {terms.length === 0 ? (
              <p className="text-sm">{t('payroll.compensation.noTerms')}</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {orderVersions(terms).map((item) => (
                  <VersionRow
                    key={item.id}
                    id={item.id}
                    status={item.status}
                    date={
                      item.pay_frequency === null
                        ? null
                        : t('payroll.compensation.since', {
                            date: labels.date(item),
                          })
                    }
                    value={labels.terms(item)}
                    deleteLabel={t('payroll.compensation.deleteTerms', {
                      date: labels.date(item),
                    })}
                    onDelete={
                      actions ? () => actions.onDeleteTerms(item) : undefined
                    }
                  />
                ))}
              </ul>
            )}
          </section>
          <section aria-labelledby={ids.rates} className="flex flex-col gap-4">
            <SectionHeading
              id={ids.rates}
              title={t('payroll.compensation.ratesTitle')}
              action={
                actions ? (
                  <Button
                    type="button"
                    className="min-h-11 md:min-h-9"
                    onClick={actions.onAddRate}
                  >
                    {t('payroll.rateDialog.title')}
                  </Button>
                ) : undefined
              }
            />
            {CLASS_KINDS.map((kind) => (
              <RatesOfKind
                key={kind}
                kind={kind}
                rates={rates.filter((rate) => rate.class_kind === kind)}
                salaried={salaried}
                actions={actions}
              />
            ))}
          </section>
        </>
      )}
    </div>
  );
}
