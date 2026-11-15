import { useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

// A chart's frame (#322 C2, C3): a <figure> named by its h2 heading and
// described by a one-sentence text summary, a Chart / Table toggle (two
// aria-pressed buttons in a labelled group, the calendar's view-toggle
// pattern; kept per chart in component state, Chart by default), optional
// extra controls, and either the chart or its data table. With no data,
// a text empty state replaces both views (never an empty chart).

export type ChartView = 'chart' | 'table';

export function ChartFigure({
  heading,
  summary,
  empty,
  controls,
  chart,
  table,
  initialView = 'chart',
}: {
  heading: string;
  summary: string;
  /** The text empty state, or null when there's data. */
  empty: string | null;
  /** Extra controls shown in the chart view (a series toggle). */
  controls?: ReactNode;
  chart: () => ReactNode;
  table: () => ReactNode;
  /** The first view (stories). */
  initialView?: ChartView;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [view, setView] = useState<ChartView>(initialView);
  return (
    <figure
      aria-labelledby={`${id}-heading`}
      aria-describedby={`${id}-summary`}
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4 text-card-foreground"
    >
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <h2 id={`${id}-heading`} className="text-lg font-semibold">
          {heading}
        </h2>
        {empty === null ? (
          <div
            role="group"
            aria-label={t('progress.chart.viewOf', { name: heading })}
            className="flex flex-wrap gap-1"
          >
            {(['chart', 'table'] as const).map((option) => (
              <Button
                key={option}
                type="button"
                size="sm"
                variant={view === option ? 'default' : 'outline'}
                aria-pressed={view === option}
                onClick={() => setView(option)}
              >
                {t(`progress.chart.${option}`)}
              </Button>
            ))}
          </div>
        ) : null}
      </div>
      <p id={`${id}-summary`} className="text-sm break-words">
        {summary}
      </p>
      {empty !== null ? (
        <p className="text-sm">{empty}</p>
      ) : (
        <>
          {view === 'chart' ? controls : null}
          {view === 'chart' ? chart() : table()}
        </>
      )}
    </figure>
  );
}

export interface TableRow {
  key: string;
  /** The first cell is the row header (th scope="row"). */
  cells: readonly string[];
}

/**
 * A chart's data as a captioned table (#322 C2) in the #311 scroll region:
 * role="region" named by the caption, tabIndex 0 and a focus ring, so a
 * wide table scrolls inside its container, never the page.
 */
export function DataTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: readonly string[];
  rows: readonly TableRow[];
}) {
  const id = useId();
  return (
    <div
      role="region"
      aria-labelledby={`${id}-caption`}
      tabIndex={0}
      className="relative min-w-0 overflow-x-auto rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      <table className="w-full min-w-[28rem] border-collapse text-left text-sm">
        <caption id={`${id}-caption`} className="pb-2 text-left font-medium">
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-border">
            {columns.map((column) => (
              <th key={column} scope="col" className="py-2 pr-3 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-b border-border align-top">
              {row.cells.map((cell, index) =>
                index === 0 ? (
                  <th
                    key={index}
                    scope="row"
                    className="py-2 pr-3 font-normal break-words"
                  >
                    {cell}
                  </th>
                ) : (
                  <td key={index} className="py-2 pr-3 break-words">
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
