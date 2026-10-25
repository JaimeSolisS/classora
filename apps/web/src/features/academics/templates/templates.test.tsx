import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi, type ApiStub, type Routes } from '@/test/api-stub';
import { alerts, click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { COORDINATOR, FINANCE, problemWith } from '../testing';
import {
  INACTIVE,
  PHYSICS_ID,
  TEMPLATE,
  TEMPLATE_ID,
  TEMPLATES,
  TWENTY,
} from './fixtures';
import { parseTemplateFilters } from './rules';

// #308 B, A1, G1 (templates): the route guard (Finance: access-denied, no
// request), the list (columns, text badges, the scroll region), its
// filters in the URL (invalid values ignored), the empty states, the
// editor's validation (name, kind, 1-20 criteria, duplicate titles, the
// maximum score through lib/decimal), the row controls and their
// accessible names, the POST and PUT bodies, every B2 error mapping and
// Reload after a version conflict.

const LIST = 'GET /api/v1/evaluation-templates';
const DETAIL = `GET /api/v1/evaluation-templates/${TEMPLATE_ID}`;
const SUBJECTS = 'GET /api/v1/subjects';

let app: RenderedApp | undefined;
let api: ApiStub;
let listUrls: URL[] = [];
const main = () => app!.container.querySelector('main')!;

function stub(routes: Routes = {}, as = COORDINATOR): ApiStub {
  listUrls = [];
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, { items: TEMPLATES });
    },
    [DETAIL]: () => jsonResponse(200, TEMPLATE),
    [SUBJECTS]: () =>
      jsonResponse(200, {
        items: [
          {
            id: PHYSICS_ID,
            name: 'Física',
            education_level_name: 'Secundaria',
            is_active: true,
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    ...routes,
  });
  return api;
}

async function open(path: string, ready: () => unknown): Promise<void> {
  app = await renderApp(path);
  await settleUntil(ready);
}

const h1 = () => main().querySelector('h1')?.textContent;
const field = <
  T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
>(
  text: string,
) => labelled<T>(main(), text);

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('access (A1)', () => {
  it.each([
    '/academics/evaluation-templates',
    '/academics/evaluation-templates/new',
    `/academics/evaluation-templates/${TEMPLATE_ID}`,
  ])('Finance gets access-denied on %s and no request', async (path) => {
    stub({}, FINANCE);
    await open(path, () => h1());
    expect(main().textContent).toMatch(/permission/i);
    expect(
      api.requests.filter((r) => r.path.includes('evaluation')),
    ).toHaveLength(0);
  });
});

describe('the list (B1)', () => {
  it('shows the columns, the text badges and the scroll region', async () => {
    stub();
    await open('/academics/evaluation-templates', () =>
      main().querySelector('table'),
    );
    expect(h1()).toBe('Evaluation templates');
    expect(document.title).toContain('Evaluation templates');
    const region = main().querySelector<HTMLElement>('[role="region"]')!;
    expect(region.getAttribute('tabindex')).toBe('0');
    expect(region.getAttribute('aria-labelledby')).toBe(
      main().querySelector('h1')!.id,
    );
    const headers = [...main().querySelectorAll('th')].map(
      (th) => th.textContent,
    );
    expect(headers).toEqual(['Name', 'Kind', 'Subject', 'Criteria', 'Status']);
    const rows = [...main().querySelectorAll('tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => td.textContent),
    );
    expect(rows).toEqual([
      ['Diagnóstico de física', 'Diagnostic', 'Física', '2', 'Active'],
      ['Seguimiento general', 'Ongoing', 'Any subject', '1', 'Active'],
      ['Diagnóstico anterior', 'Diagnostic', 'Física', '2', 'Inactive'],
    ]);
    expect(
      main().querySelector(
        `a[href="/academics/evaluation-templates/${TEMPLATE_ID}"]`,
      )?.textContent,
    ).toBe('Diagnóstico de física');
    expect(
      main().querySelector('a[href="/academics/evaluation-templates/new"]')
        ?.textContent,
    ).toBe('New template');
  });

  it('reads the filters from the URL', async () => {
    stub();
    await open(
      `/academics/evaluation-templates?status=all&kind=ongoing&subject=${PHYSICS_ID}&q=lab`,
      () => listUrls.length > 0,
    );
    const params = listUrls.at(-1)!.searchParams;
    expect(params.get('status')).toBe('all');
    expect(params.get('kind')).toBe('ongoing');
    expect(params.get('subject_id')).toBe(PHYSICS_ID);
    expect(params.get('q')).toBe('lab');
    expect(field<HTMLSelectElement>('Status').value).toBe('all');
    expect(field<HTMLInputElement>('Search by name').value).toBe('lab');
  });

  it('ignores invalid filter values', async () => {
    stub();
    await open(
      '/academics/evaluation-templates?status=bogus&kind=final&subject=nope',
      () => listUrls.length > 0,
    );
    const params = listUrls.at(-1)!.searchParams;
    expect(params.get('status')).toBe('active');
    expect(params.has('kind')).toBe(false);
    expect(params.has('subject_id')).toBe(false);
    expect(
      parseTemplateFilters(
        new URLSearchParams('status=inactive&kind=diagnostic'),
      ),
    ).toEqual({ status: 'inactive', kind: 'diagnostic', subject: null, q: '' });
  });

  it('puts a filter change in the URL', async () => {
    stub();
    await open('/academics/evaluation-templates', () =>
      main().querySelector('table'),
    );
    setFieldValue(field<HTMLSelectElement>('Kind'), 'diagnostic');
    await settleUntil(() => app!.location().includes('kind=diagnostic'));
    await settleUntil(() => listUrls.at(-1)!.searchParams.get('kind'));
    expect(listUrls.at(-1)!.searchParams.get('kind')).toBe('diagnostic');
  });

  it('says there are none yet, with "New template"', async () => {
    stub({ [LIST]: () => jsonResponse(200, { items: [] }) });
    await open('/academics/evaluation-templates', () =>
      main().textContent?.includes('No evaluation templates yet.'),
    );
    expect(
      [...main().querySelectorAll('a')].filter(
        (a) => a.textContent === 'New template',
      ).length,
    ).toBeGreaterThan(0);
  });

  it('says nothing matches, and "Clear filters" clears them', async () => {
    stub({ [LIST]: () => jsonResponse(200, { items: [] }) });
    await open('/academics/evaluation-templates?kind=ongoing', () =>
      main().textContent?.includes('No templates match these filters.'),
    );
    await click(buttonNamed(main(), 'Clear filters'));
    expect(app!.location()).toBe('/academics/evaluation-templates');
  });
});

describe('the editor (B2)', () => {
  const save = () => buttonNamed(main(), 'Save template');

  async function openNew(routes: Routes = {}) {
    stub(routes);
    await open('/academics/evaluation-templates/new', () =>
      main().querySelector('form'),
    );
  }

  function fillValid() {
    setFieldValue(field<HTMLInputElement>('Name'), '  Rúbrica  ');
    setFieldValue(field<HTMLSelectElement>('Kind'), 'ongoing');
    setFieldValue(field<HTMLInputElement>('Criterion 1 title'), 'Orden');
    setFieldValue(field<HTMLInputElement>('Criterion 1 maximum score'), '7.5');
  }

  it('checks the name, the kind and the criterion, and focuses the first', async () => {
    await openNew();
    await click(save());
    await settleUntil(
      () =>
        alerts().length >= 0 && main().textContent?.includes('Write a name.'),
    );
    expect(main().textContent).toContain('Choose a kind.');
    expect(main().textContent).toContain('Write a title.');
    expect(field('Name').getAttribute('aria-invalid')).toBe('true');
    expect(field('Name').getAttribute('aria-describedby')).toBeTruthy();
    expect(document.activeElement).toBe(field('Name'));
    expect(api.calls('POST /api/v1/evaluation-templates')).toHaveLength(0);
  });

  it.each([
    ['10.255', 'Use at most two decimal places.'],
    ['5,5', 'Use a period for decimals, for example 7.5.'],
    ['0.4', 'The maximum score must be from 0.5 to 1000.'],
    ['1000.01', 'The maximum score must be from 0.5 to 1000.'],
    ['abc', 'Write a number such as 10 or 7.5.'],
  ])('refuses the maximum score %s', async (value, message) => {
    await openNew();
    fillValid();
    setFieldValue(field<HTMLInputElement>('Criterion 1 maximum score'), value);
    await click(save());
    await settleUntil(() => main().textContent?.includes(message));
    expect(api.calls('POST /api/v1/evaluation-templates')).toHaveLength(0);
  });

  it('flags a duplicate title (ignoring case) on the later row', async () => {
    await openNew();
    fillValid();
    await click(buttonNamed(main(), 'Add criterion'));
    setFieldValue(field<HTMLInputElement>('Criterion 2 title'), 'ORDEN');
    setFieldValue(field<HTMLInputElement>('Criterion 2 maximum score'), '5');
    await click(save());
    await settleUntil(() =>
      main().textContent?.includes('Another criterion has this title.'),
    );
    expect(field('Criterion 2 title').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(field('Criterion 1 title').getAttribute('aria-invalid')).toBeNull();
  });

  it('disables Remove on the last row and Add at 20, with the reason', async () => {
    await openNew();
    expect(buttonNamed(main(), 'Remove criterion 1').disabled).toBe(true);
    for (let n = 1; n < 20; n += 1) {
      await click(buttonNamed(main(), 'Add criterion'));
    }
    const add = buttonNamed(main(), 'Add criterion');
    expect(add.disabled).toBe(true);
    const reason = document.getElementById(
      add.getAttribute('aria-describedby')!,
    );
    expect(reason?.textContent).toBe('A template has at most 20 criteria.');
    expect(main().querySelectorAll('ol > li')).toHaveLength(20);
  });

  it('moves and removes rows, named by position and title, keeping focus', async () => {
    await openNew();
    setFieldValue(field<HTMLInputElement>('Criterion 1 title'), 'Orden');
    await click(buttonNamed(main(), 'Add criterion'));
    // "Add" focuses the new row's title.
    expect(document.activeElement).toBe(field('Criterion 2 title'));
    setFieldValue(field<HTMLInputElement>('Criterion 2 title'), 'Limpieza');
    const up = buttonNamed(main(), 'Move up criterion 2: Limpieza');
    expect(buttonNamed(main(), 'Move up criterion 1: Orden').disabled).toBe(
      true,
    );
    up.focus();
    await click(up);
    expect(field<HTMLInputElement>('Criterion 1 title').value).toBe('Limpieza');
    expect(field<HTMLInputElement>('Criterion 2 title').value).toBe('Orden');
    expect(main().textContent).toContain('Criterion moved to position 1.');
    // The moved row's Move up is now disabled: focus goes to its Move down.
    expect(document.activeElement).toBe(
      buttonNamed(main(), 'Move down criterion 1: Limpieza'),
    );
    await click(buttonNamed(main(), 'Remove criterion 2: Orden'));
    expect(main().querySelectorAll('ol > li')).toHaveLength(1);
    expect(document.activeElement).toBe(buttonNamed(main(), 'Add criterion'));
  });

  it('sends one POST and goes to the new template', async () => {
    const created = { ...TEMPLATE, id: '00000000-0000-4000-8000-000000308b09' };
    await openNew({
      'POST /api/v1/evaluation-templates': () => jsonResponse(201, created),
      [`GET /api/v1/evaluation-templates/${created.id}`]: () =>
        jsonResponse(200, created),
    });
    fillValid();
    setFieldValue(field<HTMLSelectElement>('Subject'), PHYSICS_ID);
    setFieldValue(field<HTMLTextAreaElement>('Description'), ' Notas ');
    await click(save());
    await settleUntil(() =>
      app!
        .location()
        .startsWith(`/academics/evaluation-templates/${created.id}`),
    );
    const [post] = api.calls('POST /api/v1/evaluation-templates');
    expect(post!.body).toEqual({
      name: 'Rúbrica',
      kind: 'ongoing',
      subject_id: PHYSICS_ID,
      description: 'Notas',
      criteria: [{ title: 'Orden', description: null, max_score: '7.5' }],
    });
    expect(api.calls('POST /api/v1/evaluation-templates')).toHaveLength(1);
    await settleUntil(() => main().textContent?.includes('Template saved.'));
    // #308 QA D1: focus lands on the status message, not <body>.
    await settleUntil(
      () =>
        document.activeElement ===
        main().querySelector('[role="status"][tabindex="-1"]'),
    );
  });

  it('shows the kind read-only on edit and sends the PUT with version and is_active', async () => {
    stub({
      [`PUT /api/v1/evaluation-templates/${TEMPLATE_ID}`]: (_request, body) =>
        jsonResponse(200, { ...TEMPLATE, version: 4, ...(body as object) }),
    });
    await open(`/academics/evaluation-templates/${TEMPLATE_ID}`, () =>
      main().querySelector('form'),
    );
    expect(main().textContent).toContain(
      "The kind can't change after creation.",
    );
    expect(
      [...main().querySelectorAll('label')].some(
        (l) => l.textContent === 'Kind',
      ),
    ).toBe(false);
    setFieldValue(field<HTMLInputElement>('Name'), 'Diagnóstico de física II');
    await click(save());
    await settleUntil(
      () =>
        api.calls(`PUT /api/v1/evaluation-templates/${TEMPLATE_ID}`).length ===
        1,
    );
    expect(
      api.calls(`PUT /api/v1/evaluation-templates/${TEMPLATE_ID}`)[0]!.body,
    ).toEqual({
      name: 'Diagnóstico de física II',
      kind: 'diagnostic',
      subject_id: PHYSICS_ID,
      description: 'Primer diagnóstico del ciclo.',
      criteria: [
        {
          title: 'Comprensión',
          description: 'Explica los conceptos.',
          max_score: '10.00',
        },
        { title: 'Resolución', description: null, max_score: '10.00' },
      ],
      version: 3,
      is_active: true,
    });
    await settleUntil(() => main().textContent?.includes('Template saved.'));
    const status = main().querySelector<HTMLElement>(
      '[role="status"][tabindex="-1"]',
    );
    await settleUntil(() => document.activeElement === status);
  });

  it('deactivates with the PUT', async () => {
    stub({
      [`PUT /api/v1/evaluation-templates/${TEMPLATE_ID}`]: () =>
        jsonResponse(200, { ...TEMPLATE, version: 4, is_active: false }),
    });
    await open(`/academics/evaluation-templates/${TEMPLATE_ID}`, () =>
      main().querySelector('form'),
    );
    expect(main().textContent).toContain(
      'Deactivating hides the template from tutors',
    );
    await click(buttonNamed(main(), 'Deactivate'));
    await settleUntil(
      () =>
        api.calls(`PUT /api/v1/evaluation-templates/${TEMPLATE_ID}`).length ===
        1,
    );
    expect(
      (
        api.calls(`PUT /api/v1/evaluation-templates/${TEMPLATE_ID}`)[0]!
          .body as {
          is_active: boolean;
        }
      ).is_active,
    ).toBe(false);
    await settleUntil(() => buttonNamed(main(), 'Activate'));
  });

  it('activates an inactive template', async () => {
    stub({
      [`GET /api/v1/evaluation-templates/${INACTIVE.id}`]: () =>
        jsonResponse(200, INACTIVE),
      [`PUT /api/v1/evaluation-templates/${INACTIVE.id}`]: () =>
        jsonResponse(200, { ...INACTIVE, is_active: true }),
    });
    await open(`/academics/evaluation-templates/${INACTIVE.id}`, () =>
      main().querySelector('form'),
    );
    await click(buttonNamed(main(), 'Activate'));
    await settleUntil(
      () =>
        api.calls(`PUT /api/v1/evaluation-templates/${INACTIVE.id}`).length ===
        1,
    );
    expect(
      (
        api.calls(`PUT /api/v1/evaluation-templates/${INACTIVE.id}`)[0]!
          .body as {
          is_active: boolean;
        }
      ).is_active,
    ).toBe(true);
  });

  describe('server errors', () => {
    const PUT = `PUT /api/v1/evaluation-templates/${TEMPLATE_ID}`;

    async function refused(response: () => Response) {
      stub({ [PUT]: response });
      await open(`/academics/evaluation-templates/${TEMPLATE_ID}`, () =>
        main().querySelector('form'),
      );
      await click(save());
      await settleUntil(() => api.calls(PUT).length === 1);
    }

    it('409 duplicate_name lands on Name', async () => {
      await refused(() => problemWith(409, 'duplicate_name'));
      await settleUntil(() =>
        main().textContent?.includes(
          'Another evaluation template already has this name.',
        ),
      );
      expect(field('Name').getAttribute('aria-invalid')).toBe('true');
      await settleUntil(() => document.activeElement === field('Name'));
    });

    it.each([
      [
        ['body', 'criteria'],
        'duplicate_title',
        'Another criterion has this title.',
      ],
      [
        ['body', 'criteria'],
        'criteria_required',
        'Add at least one criterion.',
      ],
      [
        ['body', 'criteria'],
        'criteria_too_many',
        'A template has at most 20 criteria.',
      ],
    ])('%j %s shows on the criteria', async (path, code, message) => {
      await refused(() =>
        problemWith(422, 'validation_error', { errors: [{ path, code }] }),
      );
      await settleUntil(() => alerts(main()).some((text) => text === message));
    });

    it.each([
      ['max_score_out_of_range', 'The maximum score must be from 0.5 to 1000.'],
      ['score_precision', 'Use at most two decimal places.'],
    ])('%s lands on the maximum score', async (code, message) => {
      await refused(() =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'criteria', 1, 'max_score'], code }],
        }),
      );
      await settleUntil(() => main().textContent?.includes(message));
      expect(
        field('Criterion 2 maximum score').getAttribute('aria-invalid'),
      ).toBe('true');
      await settleUntil(
        () => document.activeElement === field('Criterion 2 maximum score'),
      );
    });

    it('subject_unknown lands on Subject', async () => {
      await refused(() =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'subject_id'], code: 'subject_unknown' }],
        }),
      );
      await settleUntil(() =>
        main().textContent?.includes('Choose an active subject.'),
      );
      expect(field('Subject').getAttribute('aria-invalid')).toBe('true');
    });

    it('kind_immutable shows under the kind', async () => {
      await refused(() =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'kind'], code: 'kind_immutable' }],
        }),
      );
      await settleUntil(() =>
        main().textContent?.includes("A template's kind can't change."),
      );
    });

    it('409 version_conflict: "This template changed elsewhere." and Reload', async () => {
      await refused(() => problemWith(409, 'version_conflict'));
      await settleUntil(() =>
        main().textContent?.includes('This template changed elsewhere.'),
      );
      const before = api.calls(DETAIL).length;
      api.set({
        [DETAIL]: () =>
          jsonResponse(200, { ...TEMPLATE, version: 5, name: 'Renombrada' }),
      });
      await click(buttonNamed(main(), 'Reload'));
      await settleUntil(() => api.calls(DETAIL).length > before);
      await settleUntil(
        () => field<HTMLInputElement>('Name').value === 'Renombrada',
      );
      // #308 QA D3: the alert and its Reload are gone; focus goes to Name.
      await settleUntil(() => document.activeElement === field('Name'));
    });

    it('a 404 on the template shows the not-found page', async () => {
      stub({ [DETAIL]: () => problemWith(404, 'not_found') });
      await open(
        `/academics/evaluation-templates/${TEMPLATE_ID}`,
        () => h1() === 'Page not found',
      );
    });
  });

  it('asks before leaving with unsaved changes', async () => {
    await openNew();
    setFieldValue(field<HTMLInputElement>('Name'), 'Algo');
    const back = [...main().querySelectorAll<HTMLAnchorElement>('a')].find(
      (a) => a.textContent === 'Back to evaluation templates',
    )!;
    back.focus();
    await click(back);
    await settleUntil(() => document.querySelector('[role="dialog"]'));
    await click(
      buttonNamed(document.querySelector('[role="dialog"]')!, 'Stay'),
    );
    await settleUntil(() => document.querySelector('[role="dialog"]') === null);
    expect(app!.location()).toBe('/academics/evaluation-templates/new');
    await settleUntil(() => document.activeElement === back);
  });

  it('opens a template with 20 criteria', async () => {
    stub({ [DETAIL]: () => jsonResponse(200, { ...TWENTY, id: TEMPLATE_ID }) });
    await open(`/academics/evaluation-templates/${TEMPLATE_ID}`, () =>
      main().querySelector('form'),
    );
    expect(main().querySelectorAll('ol > li')).toHaveLength(20);
    expect(buttonNamed(main(), 'Add criterion').disabled).toBe(true);
    act(() => undefined);
  });
});
