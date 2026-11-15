import { useId, type ReactNode } from 'react';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';

// A KPI table (M8-09b C8): a captioned table from `sm` up, and the same
// rows as cards below it, so nothing scrolls sideways at 320 px. The first
// column is the row header; the total row is bold, in the table footer and
// the last card.

export interface KpiRow {
  key: string;
  header: ReactNode;
  cells: readonly ReactNode[];
}

export function KpiTable({
  caption,
  columns,
  rows,
  total,
  data,
}: {
  caption: string;
  /** The header column's label, then one per value cell. */
  columns: readonly string[];
  rows: readonly KpiRow[];
  total?: KpiRow;
  /** `data-kpi-table` / `data-kpi-cards` value (tests, E2E). */
  data: string;
}) {
  const captionId = useId();
  const [headerLabel, ...valueLabels] = columns;
  const card = (row: KpiRow, strong: boolean) => (
    <li
      key={row.key}
      data-kpi-card={row.key}
      className={cn(
        'flex min-w-0 flex-col gap-1 rounded-lg border p-4',
        strong ? 'border-foreground' : 'border-border',
      )}
    >
      <p
        className={cn(
          'min-w-0 [overflow-wrap:anywhere]',
          strong ? 'font-semibold' : 'font-medium',
        )}
      >
        {row.header}
      </p>
      <dl className="flex flex-col text-sm">
        {row.cells.map((cell, index) => (
          <div
            key={valueLabels[index]}
            className={cn(
              'flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-1',
              strong && 'font-semibold',
            )}
          >
            <dt className="min-w-0 [overflow-wrap:anywhere]">
              {valueLabels[index]}
            </dt>
            <dd className="tabular-nums">{cell}</dd>
          </div>
        ))}
      </dl>
    </li>
  );
  return (
    <>
      <div className="hidden sm:block" data-kpi-table={data}>
        <Table aria-labelledby={captionId}>
          <TableCaption id={captionId} className="sr-only">
            {caption}
          </TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{headerLabel}</TableHead>
              {valueLabels.map((label) => (
                <TableHead key={label} scope="col" className="text-right">
                  {label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.key} data-kpi-row={row.key}>
                <TableHead
                  scope="row"
                  className="font-normal whitespace-normal [overflow-wrap:anywhere]"
                >
                  {row.header}
                </TableHead>
                {row.cells.map((cell, index) => (
                  <TableCell
                    key={valueLabels[index]}
                    className="text-right tabular-nums"
                  >
                    {cell}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
          {total ? (
            <TableFooter>
              <TableRow className="font-semibold" data-kpi-row={total.key}>
                <TableHead scope="row">{total.header}</TableHead>
                {total.cells.map((cell, index) => (
                  <TableCell
                    key={valueLabels[index]}
                    className="text-right tabular-nums"
                  >
                    {cell}
                  </TableCell>
                ))}
              </TableRow>
            </TableFooter>
          ) : null}
        </Table>
      </div>
      <ul className="flex flex-col gap-3 sm:hidden" data-kpi-cards={data}>
        {rows.map((row) => card(row, false))}
        {total ? card(total, true) : null}
      </ul>
    </>
  );
}
