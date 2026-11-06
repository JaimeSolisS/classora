import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

// The KPI charts' shared pieces (M8-09b E3, the #322 C2 pattern): a text
// legend with each series' swatch, and the chart's data as a visually
// hidden captioned table (the text alternative; the wrapper is what's
// hidden, since a table ignores the 1 px box). No Recharts import here.

export interface LegendItem {
  key: string;
  label: string;
  /** The swatch's SVG content in a 16 × 16 box. */
  swatch: ReactNode;
}

export function ChartLegend({ items }: { items: readonly LegendItem[] }) {
  const { t } = useTranslation();
  return (
    <ul
      aria-label={t('progress.chart.legend')}
      className="flex flex-wrap gap-x-4 gap-y-1 text-sm"
    >
      {items.map((item) => (
        <li key={item.key} className="flex items-center gap-1.5">
          <svg
            aria-hidden="true"
            focusable="false"
            width="16"
            height="16"
            className="shrink-0"
          >
            {item.swatch}
          </svg>
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export interface ChartTableRow {
  key: string;
  /** The first cell is the row header. */
  cells: readonly string[];
}

export function ChartDataTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: readonly string[];
  rows: readonly ChartTableRow[];
}) {
  const id = useId();
  return (
    <div className="sr-only">
      <table aria-labelledby={id} data-kpi-chart-table>
        <caption id={id}>{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              {row.cells.map((cell, index) =>
                index === 0 ? (
                  <th key={index} scope="row">
                    {cell}
                  </th>
                ) : (
                  <td key={index}>{cell}</td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
