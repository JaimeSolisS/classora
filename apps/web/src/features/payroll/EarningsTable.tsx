import type { ReactNode } from 'react';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';

// One group of a period's detail (M7-10b C3, C4): a table from 768 px and
// cards below it, from the same columns, so nothing scrolls sideways at
// 320 px. The amount column is right-aligned in the table.

export interface EarningsColumn {
  key: string;
  label: string;
  /** Right-aligned in the table (amounts). */
  numeric?: boolean;
}

export interface EarningsRow {
  key: string;
  cells: Record<string, ReactNode>;
  /** Data attributes for tests and stories. */
  data?: Record<`data-${string}`, string>;
}

export function EarningsTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: readonly EarningsColumn[];
  rows: readonly EarningsRow[];
}) {
  return (
    <>
      <div className="hidden md:block" data-earnings-group-table>
        <Table>
          <TableCaption className="sr-only">{caption}</TableCaption>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead
                  key={column.key}
                  scope="col"
                  className={column.numeric ? 'text-right' : undefined}
                >
                  {column.label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.key} className="align-top" {...row.data}>
                {columns.map((column) => (
                  <TableCell
                    key={column.key}
                    className={cn(
                      'whitespace-normal',
                      column.numeric &&
                        'text-right font-medium whitespace-nowrap',
                    )}
                  >
                    {row.cells[column.key]}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul
        className="flex flex-col gap-3 md:hidden"
        aria-label={caption}
        data-earnings-group-cards
      >
        {rows.map((row) => (
          <li
            key={row.key}
            className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
            {...row.data}
          >
            <dl className="grid grid-cols-1 gap-1 text-sm">
              {columns.map((column) => (
                <div key={column.key} className="min-w-0">
                  <dt className="font-medium">{column.label}</dt>
                  <dd className="break-words">{row.cells[column.key]}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}
