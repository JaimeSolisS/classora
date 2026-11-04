import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
import { Pagination } from '@/features/catalog/Pagination';
import { useFormatters } from '@/lib/formatters';
import type { ExpensePage, ExpenseTemplate } from '../api';
import { ExpenseStatusBadge, TemplateStatusBadge } from '../Badges';
import { ExpensesTabs } from '../ExpensesTabs';
import { expensePath } from '../expenses/rules';
import {
  templateActions,
  TEMPLATES_PATH,
  type TemplateCapabilities,
} from './rules';
import { CategoryCell, VendorCell } from './TemplatesView';
import { useNextPeriod } from './useNextPeriod';

// One recurring template (M7-13b C), without any query: the header fields,
// the actions (A3, D3, D4) and the expenses it generated, newest period
// first, each linking to its expense (C2). A failed refetch keeps what is
// shown, with the out-of-date alert, and holds every action until a reload
// lands (C3, E2).

export type GeneratedState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: ExpensePage };

export interface TemplateDetailViewProps {
  template: ExpenseTemplate;
  can: TemplateCapabilities;
  generated: GeneratedState;
  onPage: (page: number) => void;
  /** A refetch hasn't landed (or failed), or a dialog is open. */
  blocked?: boolean;
  staleError?: unknown;
  onReload?: () => void;
  onEdit: () => void;
  onPause: () => void;
  onResume: () => void;
  onEnd: () => void;
  announcement?: string;
}

const L = 'finance.templates';
const DT = `${L}.detail`;
const WRAP = '[overflow-wrap:anywhere]';
const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

export function BackLink() {
  const { t } = useTranslation();
  return (
    <Link to={TEMPLATES_PATH} className={LINK_CLASS}>
      {t(`${DT}.back`)}
    </Link>
  );
}

export function TemplateDetailView({
  template,
  can,
  generated,
  onPage,
  blocked = false,
  staleError = null,
  onReload,
  onEdit,
  onPause,
  onResume,
  onEnd,
  announcement = '',
}: TemplateDetailViewProps) {
  const { t } = useTranslation();
  const { formatLocalDate, formatMoney } = useFormatters();
  const nextPeriod = useNextPeriod();
  const actions = templateActions(can, template);
  const none = t(`${L}.none`);
  const field = (key: string, value: ReactNode) => (
    <div className="flex min-w-0 flex-col gap-1" data-template-field={key}>
      <dt className="text-sm text-muted-foreground">
        {t(`${DT}.fields.${key as 'category'}`)}
      </dt>
      <dd className={WRAP}>{value}</dd>
    </div>
  );
  const anyAction =
    actions.edit || actions.pause || actions.resume || actions.end;
  const items = generated.status === 'ready' ? generated.page.items : [];
  const generatedTitle = `${useId()}-generated`;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <ExpensesTabs />
      <div className="flex min-w-0 flex-col gap-4">
        <BackLink />
        <div className="flex flex-wrap items-center gap-3">
          <h1 className={`text-2xl font-semibold ${WRAP}`}>{template.name}</h1>
          <TemplateStatusBadge status={template.status} />
        </div>
        <p
          role="status"
          aria-live="polite"
          className="text-sm font-medium"
          data-template-announcement
        >
          {announcement}
        </p>
        {staleError ? (
          <ErrorAlert
            error={staleError}
            message={t(`${DT}.stale`)}
            onRetry={onReload}
            retryLabel={t(`${DT}.reload`)}
          />
        ) : null}
        <dl
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
          data-template-head
        >
          {field('category', <CategoryCell category={template.category} />)}
          {field('vendor', <VendorCell vendor={template.vendor} />)}
          {field('amount', formatMoney(template.amount))}
          {field('frequency', t(`${L}.frequencies.${template.frequency}`))}
          {field('startsOn', formatLocalDate(template.starts_on))}
          {field(
            'endsOn',
            template.ends_on
              ? formatLocalDate(template.ends_on)
              : t(`${DT}.noEnd`),
          )}
          {field(
            'dueOffset',
            t(`${DT}.dueOffset`, { count: template.due_offset_days }),
          )}
          {field('next', nextPeriod(template))}
          {field(
            'last',
            template.last_period_start
              ? formatLocalDate(template.last_period_start)
              : none,
          )}
          {field('status', <TemplateStatusBadge status={template.status} />)}
        </dl>
        {anyAction ? (
          <div
            role="group"
            aria-label={t(`${DT}.actions.label`)}
            className="flex flex-wrap gap-2"
          >
            {actions.edit ? (
              <Button
                type="button"
                className="min-h-11 md:min-h-9"
                disabled={blocked}
                onClick={onEdit}
              >
                {t(`${DT}.actions.edit`)}
              </Button>
            ) : null}
            {actions.pause ? (
              <Button
                type="button"
                variant="outline"
                className="min-h-11 md:min-h-9"
                disabled={blocked}
                onClick={onPause}
              >
                {t(`${DT}.actions.pause`)}
              </Button>
            ) : null}
            {actions.resume ? (
              <Button
                type="button"
                variant="outline"
                className="min-h-11 md:min-h-9"
                disabled={blocked}
                onClick={onResume}
              >
                {t(`${DT}.actions.resume`)}
              </Button>
            ) : null}
            {actions.end ? (
              <Button
                type="button"
                variant="outline"
                className="min-h-11 md:min-h-9"
                disabled={blocked}
                onClick={onEnd}
              >
                {t(`${DT}.actions.end`)}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      <section
        aria-labelledby={generatedTitle}
        className="flex min-w-0 flex-col gap-4"
        data-template-generated
      >
        <h2 id={generatedTitle} className="text-xl font-semibold">
          {t(`${DT}.generated.title`)}
        </h2>
        {generated.status === 'loading' ? (
          <LoadingState layout="table" rows={3} />
        ) : null}
        {generated.status === 'error' ? (
          <ErrorAlert error={generated.error} onRetry={generated.onRetry} />
        ) : null}
        {generated.status === 'ready' && items.length === 0 ? (
          <p data-generated-empty>
            {template.status === 'ended'
              ? t(`${DT}.generated.emptyEnded`)
              : t(`${DT}.generated.empty`, {
                  date: formatLocalDate(template.next_period_start),
                })}
          </p>
        ) : null}
        {generated.status === 'ready' && items.length > 0 ? (
          <>
            <div className="hidden sm:block" data-generated-table>
              <Table>
                <TableCaption className="sr-only">
                  {t(`${DT}.generated.caption`)}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    {(['period', 'net', 'open', 'status'] as const).map(
                      (column) => (
                        <TableHead
                          key={column}
                          scope="col"
                          className={
                            column === 'net' || column === 'open'
                              ? 'text-right'
                              : undefined
                          }
                        >
                          {t(`${DT}.generated.columns.${column}`)}
                        </TableHead>
                      ),
                    )}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((expense) => (
                    <TableRow key={expense.id} data-generated-id={expense.id}>
                      <TableCell>
                        <Link
                          to={expensePath(expense.id)}
                          className={LINK_CLASS}
                        >
                          {formatLocalDate(expense.incurred_on)}
                        </Link>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(expense.adjusted_amount)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(expense.open_amount)}
                      </TableCell>
                      <TableCell>
                        <ExpenseStatusBadge status={expense.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <ul className="flex flex-col gap-3 sm:hidden" data-generated-cards>
              {items.map((expense) => (
                <li
                  key={expense.id}
                  className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <Link to={expensePath(expense.id)} className={LINK_CLASS}>
                      {formatLocalDate(expense.incurred_on)}
                    </Link>
                    <ExpenseStatusBadge status={expense.status} />
                  </div>
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                    <dt className="text-muted-foreground">
                      {t(`${DT}.generated.columns.net`)}
                    </dt>
                    <dd className="tabular-nums">
                      {formatMoney(expense.adjusted_amount)}
                    </dd>
                    <dt className="text-muted-foreground">
                      {t(`${DT}.generated.columns.open`)}
                    </dt>
                    <dd className="tabular-nums">
                      {formatMoney(expense.open_amount)}
                    </dd>
                  </dl>
                </li>
              ))}
            </ul>
            <Pagination
              offset={generated.page.offset}
              limit={generated.page.limit}
              shown={items.length}
              total={generated.page.total}
              onPage={onPage}
              buttonClassName="min-h-11 md:min-h-9"
            />
          </>
        ) : null}
      </section>
    </div>
  );
}
