// Shared chart styling (#322 C3): semantic tokens only. Axis text and grid
// lines use muted-foreground / border; series use --chart-1…4; tooltips
// look like popovers and never animate. No Recharts import here.

/** Every chart's fixed height; the width follows the container. */
export const CHART_HEIGHT = 240;

export const AXIS_TICK = { fill: 'var(--muted-foreground)', fontSize: 12 };
export const GRID_STROKE = 'var(--border)';

/** The four series colours, in fixed order (never cycled). */
export const SERIES_COLORS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
] as const;

export const TOOLTIP_PROPS = {
  isAnimationActive: false,
  contentStyle: {
    background: 'var(--popover)',
    border: '1px solid var(--border)',
    borderRadius: 6,
    color: 'var(--popover-foreground)',
  },
  labelStyle: { color: 'var(--popover-foreground)', fontWeight: 600 },
  itemStyle: { color: 'var(--popover-foreground)' },
  cursor: { fill: 'var(--muted)', stroke: 'var(--border)' },
} as const;

/** A useId() value usable in an SVG `url(#…)` reference. */
export function svgId(id: string): string {
  return `chart-${id.replace(/[^a-zA-Z0-9_-]/g, '')}`;
}
