import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi } from '@/test/api-stub';
import { renderApp, settle, type RenderedApp } from '@/test/render-app';
import type { ReportFacts } from './api';
import {
  EMPTY_FACTS,
  FACTS,
  NULL_AVERAGE_FACTS,
  SCRIPT,
  SCRIPT_FACTS,
  TRUNCATED_FACTS,
  TUTOR,
} from './fixtures';
import { ProgressReportFacts } from './ProgressReportFacts';

// #315 D, H1 (facts): the four sections with data, zero data, null
// averages (never "0 %"), both truncated notes, decimal strings through
// lib/decimal, an unsupported schema version, markup as text, and the
// decorative progress bar with its value in text. No request.

let app: RenderedApp | undefined;
let requests = 0;
const root = () => app!.container;
const text = () => root().textContent ?? '';
const section = (title: string) =>
  [...root().querySelectorAll('section')].find(
    (element) => element.querySelector('h3')?.textContent === title,
  );

async function show(facts: ReportFacts, language: 'en' | 'es' = 'en') {
  applyLanguage(language);
  const api = stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
  app = await renderApp('/', [
    {
      path: '/',
      element: createElement(ProgressReportFacts, {
        facts,
        heading: 'Facts as approved',
      }),
    },
  ]);
  await settle();
  requests = api.requests.filter((r) => r.path !== '/api/v1/auth/me').length;
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('the facts view (D1, D2)', () => {
  it('renders the four sections with data, as text', async () => {
    await show(FACTS);
    expect(root().querySelector('h2')?.textContent).toBe('Facts as approved');
    expect(text()).toContain(
      'Period Oct 1, 2026 – Oct 24, 2026 · All subjects',
    );
    expect(section('Attendance')?.textContent).toContain(
      '6 classes: 4 present · 1 late · 1 absent · 0 not recorded',
    );
    const evaluations = section('Evaluations')!.textContent ?? '';
    expect(evaluations).toContain('2 evaluations · average 80.25 %');
    // Per subject: a decimal through lib/decimal ("87.5" → "87.50"), a null
    // average as "No average".
    expect(evaluations).toContain('Física: 1 evaluation · average 87.50 %');
    expect(evaluations).toContain('Matemáticas: 1 evaluation · No average');
    expect(evaluations).toContain(
      'Oct 20, 2026 · Física · Diagnostic · Diagnóstico inicial · 87.50 %',
    );
    expect(evaluations).toContain(
      'Oct 6, 2026 · Matemáticas · Ongoing · Seguimiento · 73.00 %',
    );
    expect(section('Homework')?.textContent).toContain(
      '4 assignments: 1 pending · 1 missing · 1 handed in · 1 graded · 1 late · average 90.00 %',
    );
    const plans = section('Learning plans')!.textContent ?? '';
    expect(plans).toContain('Lectura comprensiva');
    expect(plans).toContain('Física · Active');
    expect(plans).toContain('67 % complete');
    expect(plans).toContain('1 of 2 objectives · 2 of 3 milestones');
    expect(requests).toBe(0);
  });

  it('the plan bar is decorative; the value is in the text', async () => {
    await show(FACTS);
    const bar = section('Learning plans')!.querySelector(
      '[aria-hidden="true"]',
    );
    expect(bar).not.toBeNull();
    expect((bar!.firstElementChild as HTMLElement).style.width).toBe('67%');
    expect(root().querySelector('progress')).toBeNull();
  });

  it('zero data', async () => {
    await show(EMPTY_FACTS);
    expect(text()).toContain('No classes in this period.');
    expect(text()).toContain('No evaluations in this period.');
    expect(text()).toContain('No homework due in this period.');
    expect(text()).toContain('No learning plans in this period.');
    expect(text()).not.toContain('0 %');
  });

  it('null averages are never shown as 0 %', async () => {
    await show(NULL_AVERAGE_FACTS);
    expect(section('Evaluations')?.textContent).toContain(
      '2 evaluations · No average',
    );
    expect(section('Homework')?.textContent).toContain('No average');
    expect(text()).not.toMatch(/average 0(\.00)? %/);
  });

  it('both truncated notes', async () => {
    await show(TRUNCATED_FACTS);
    expect(text()).toContain('Showing the 50 most recent evaluations.');
    expect(text()).toContain('Showing 20 plans.');
  });

  it('another schema version: only "can\'t be shown"', async () => {
    await show({ ...FACTS, schema_version: 2 } as unknown as ReportFacts);
    expect(text()).toContain(
      "These facts can't be shown in this version of the app.",
    );
    expect(text()).not.toContain('Attendance');
  });

  it('ignores unknown extra keys', async () => {
    await show({ ...FACTS, extra: { x: 1 } } as unknown as ReportFacts);
    expect(text()).toContain('6 classes');
  });

  it('markup in a template or plan title shows literally', async () => {
    await show(SCRIPT_FACTS);
    expect(text().split(SCRIPT).length - 1).toBe(2);
    expect(root().querySelector('script')).toBeNull();
  });

  it('Spanish plural forms', async () => {
    await show(FACTS, 'es');
    expect(text()).toContain(
      '6 clases: con asistencia 4 · con retraso 1 · con falta 1 · sin registrar 0',
    );
    expect(text()).toContain('2 evaluaciones · promedio 80.25 %');
  });
});
