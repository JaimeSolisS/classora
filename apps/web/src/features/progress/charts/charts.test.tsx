import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi, TEST_USER } from '@/test/api-stub';
import { click } from '@/test/dom';
import {
  buttonNamed,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { StudentProgress } from '../api';
import { PROGRESS, SINGLE_POINT } from '../fixtures';
import { AttendanceChart } from './AttendanceChart';
import { GradeTrendChart } from './GradeTrendChart';

// #322 C3 (the chart rules, as props): every series has its animation off
// and the accessibility layer is on; attendance bars are filled by four
// distinct SVG patterns (never colour alone) from the --chart tokens;
// grade lines leave gaps (connectNulls off) and differ in colour, dash and
// marker; axes and grids use the muted/border tokens; a single data point
// renders. Recharts is wrapped so its props can be read; it still renders
// at a fixed width (jsdom has no layout).

const captured = vi.hoisted(() => ({
  charts: [] as Record<string, unknown>[],
  bars: [] as Record<string, unknown>[],
  lines: [] as Record<string, unknown>[],
  axes: [] as Record<string, unknown>[],
}));

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>();
  const { createElement: h } = await import('react');
  const wrap = (
    Component: React.ComponentType<Record<string, unknown>>,
    into: Record<string, unknown>[],
  ) =>
    function Wrapped(props: Record<string, unknown>) {
      into.push(props);
      return h(Component, props);
    };
  return {
    ...actual,
    BarChart: wrap(actual.BarChart as never, captured.charts),
    LineChart: wrap(actual.LineChart as never, captured.charts),
    Bar: wrap(actual.Bar as never, captured.bars),
    Line: wrap(actual.Line as never, captured.lines),
    XAxis: wrap(actual.XAxis as never, captured.axes),
    YAxis: wrap(actual.YAxis as never, captured.axes),
  };
});

let app: RenderedApp | undefined;
const root = () => app!.container;

async function show(
  component: typeof AttendanceChart | typeof GradeTrendChart,
  progress: StudentProgress = PROGRESS,
) {
  stubApi({ 'GET /api/v1/auth/me': meAs(TEST_USER) });
  app = await renderApp('/', [
    { path: '/', element: createElement(component, { progress, width: 480 }) },
  ]);
  await settle();
}

const last = <T,>(items: T[], count: number) => items.slice(-count);

beforeEach(() => {
  applyLanguage('en');
  for (const list of Object.values(captured)) {
    list.length = 0;
  }
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('the attendance chart (C2.1, C3)', () => {
  it('stacked bars, no animation, the accessibility layer, four patterns', async () => {
    await show(AttendanceChart);
    expect(captured.charts.at(-1)!.accessibilityLayer).toBe(true);
    const bars = last(captured.bars, 4);
    expect(bars.map((bar) => bar.dataKey)).toEqual([
      'present',
      'late',
      'absent',
      'not_recorded',
    ]);
    for (const bar of bars) {
      expect(bar.isAnimationActive).toBe(false);
      expect(bar.stackId).toBe('attendance');
    }
    const fills = bars.map((bar) => String(bar.fill));
    expect(new Set(fills).size).toBe(4);
    for (const fill of fills) {
      const id = /^url\(#(.+)\)$/.exec(fill)![1]!;
      const pattern = root().querySelector(`pattern[id="${id}"]`);
      expect(pattern, id).not.toBeNull();
    }
    // Each pattern draws with a different chart token.
    const tokens = [...root().querySelectorAll('pattern')].map(
      (pattern) =>
        [...pattern.querySelectorAll('rect, circle, path')]
          .map((shape) => shape.getAttribute('style') ?? '')
          .find((style) => style.includes('--chart-')) ?? '',
    );
    expect(tokens.map((style) => /--chart-\d/.exec(style)?.[0])).toEqual([
      '--chart-1',
      '--chart-2',
      '--chart-3',
      '--chart-4',
    ]);
    for (const axis of last(captured.axes, 2)) {
      expect(axis.stroke).toBe('var(--border)');
      expect(axis.tick).toEqual({
        fill: 'var(--muted-foreground)',
        fontSize: 12,
      });
    }
  });

  it('one month renders one bar group', async () => {
    await show(AttendanceChart, SINGLE_POINT);
    expect(captured.charts.at(-1)!.data).toHaveLength(1);
    expect(root().querySelector('.recharts-wrapper')).not.toBeNull();
  });
});

describe('the grade trend chart (C2.2, C3)', () => {
  it('a line per subject: no animation, gaps, distinct colour, dash and marker', async () => {
    await show(GradeTrendChart);
    expect(captured.charts.at(-1)!.accessibilityLayer).toBe(true);
    const lines = last(captured.lines, 2);
    expect(lines.map((line) => line.name)).toEqual([
      'Física: evaluations',
      'Matemáticas: evaluations',
    ]);
    for (const line of lines) {
      expect(line.isAnimationActive).toBe(false);
      expect(line.connectNulls).toBe(false);
    }
    expect(lines.map((line) => line.stroke)).toEqual([
      'var(--chart-1)',
      'var(--chart-2)',
    ]);
    expect(new Set(lines.map((line) => line.strokeDasharray)).size).toBe(2);
    // The y values are geometry; a month without a value is a gap (null).
    const data = captured.charts.at(-1)!.data as Record<string, unknown>[];
    expect(data.map((row) => row.label)).toEqual([
      'Aug 2026',
      'Sep 2026',
      'Oct 2026',
    ]);
    expect(data.map((row) => row.e0)).toEqual([87.5, null, 95]);
    expect(data.map((row) => row.e1)).toEqual([null, 70, null]);
    // Its text rides along for the tooltip, as the API sent it.
    expect(data.map((row) => row.e0text)).toEqual(['87.50', null, '95.00']);
    const yAxis = captured.axes.at(-1)!;
    expect(yAxis.domain).toEqual([0, 100]);
  });

  it('the homework series: hollow markers and their own dashes', async () => {
    await show(GradeTrendChart);
    await click(buttonNamed(root(), 'Show homework averages'));
    const lines = last(captured.lines, 4);
    expect(lines.map((line) => line.dataKey)).toEqual(['e0', 'h0', 'e1', 'h1']);
    for (const line of lines) {
      expect(line.isAnimationActive).toBe(false);
      expect(line.connectNulls).toBe(false);
    }
    expect(new Set(lines.map((line) => line.strokeDasharray)).size).toBe(4);
  });

  it('a single grade renders its point (a marker)', async () => {
    await show(GradeTrendChart, SINGLE_POINT);
    const data = captured.charts.at(-1)!.data as Record<string, unknown>[];
    expect(data).toHaveLength(1);
    expect(data[0]!.e0).toBe(90);
    expect(root().querySelector('.recharts-wrapper')).not.toBeNull();
  });
});
