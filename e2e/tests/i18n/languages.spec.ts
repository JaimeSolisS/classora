import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { api } from '../support/api';
import { SIGNED_OUT_STATE } from '../support/auth';
import { common, commonEn } from '../support/catalog';
import { FIRST_RENDER_TIMEOUT_MS } from '../support/timeouts';

// #130 E1 (E6): every page in each UI language. Read-only: the shared Owner
// state (0 logins, 0 registrations, 0 redemptions). For English the context
// rewrites ONLY `preferences.language` in the real GET /api/v1/auth/me
// response (the account's stored language wins at load, so demo.owner is
// never changed), and a preferences PATCH fails the test. This is the only
// spec that rewrites a response; every other spec stays es-MX.

type Language = 'es' | 'en';
type Catalog = typeof common;

const CATALOGS: Record<Language, Catalog> = { es: common, en: commonEn };
const LANGUAGES: Language[] = ['es', 'en'];
const MISSING_KEY_LOG = '[i18n] missing key';

/** The header navigation's destinations (apps/web/src/app/navigation.ts),
 * read from the source so a new item gets its tests automatically. A
 * destination the Owner can't see is skipped (the DOM decides). */
const NAV_PATHS: string[] = [
  ...readFileSync(
    new URL('../../../apps/web/src/app/navigation.ts', import.meta.url),
    'utf8',
  ).matchAll(/\bto: '([^']+)'/g),
].map((match) => match[1]!);

/** A token that is a raw `common` key (`students.title`) or an
 * `errors:` key. */
const RAW_KEY = new RegExp(
  `^(?:(?:${Object.keys(common).join('|')})\\.[\\w.]+|errors:\\w+)$`,
);

function navLabels(catalog: Catalog): Set<string> {
  // The link labels only (#546: `nav.sections` holds the section labels).
  return new Set(
    Object.values(catalog.nav).filter(
      (value): value is string => typeof value === 'string',
    ),
  );
}

/** Watches the page: missing-key logs and preference writes. */
async function prepare(
  page: Page,
  language: Language,
): Promise<{ missing: string[]; preferenceWrites: string[] }> {
  const missing: string[] = [];
  const preferenceWrites: string[] = [];
  page.on('console', (message) => {
    if (message.text().startsWith(MISSING_KEY_LOG)) {
      missing.push(message.text());
    }
  });
  await page.route('**/api/v1/auth/me/preferences', async (route) => {
    if (route.request().method() !== 'GET') {
      preferenceWrites.push(route.request().method());
      await route.abort();
      return;
    }
    await route.continue();
  });
  if (language === 'en') {
    await page.route('**/api/v1/auth/me', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.continue();
        return;
      }
      const response = await route.fetch();
      const body = (await response.json()) as {
        preferences: { language: string | null };
      };
      body.preferences.language = 'en';
      await route.fulfill({ response, json: body });
    });
  }
  return { missing, preferenceWrites };
}

/** Waits for the page's own content: the heading, then no region loading. */
async function settle(page: Page): Promise<void> {
  const main = page.getByRole('main');
  await expect(main.locator('h1').first()).toBeVisible({
    timeout: FIRST_RENDER_TIMEOUT_MS,
  });
  await expect(main.locator('[data-slot="loading-state"]')).toHaveCount(0);
}

async function checkPage(
  page: Page,
  language: Language,
  watched: { missing: string[]; preferenceWrites: string[] },
): Promise<void> {
  const catalog = CATALOGS[language];
  await settle(page);
  await expect(page.locator('html')).toHaveAttribute('lang', language);
  await expect(page.getByRole('main').locator('h1').first()).not.toBeEmpty();

  const nav = page.getByRole('navigation', { name: catalog.nav.label });
  const labels = (await nav.getByRole('link').allInnerTexts()).map((text) =>
    text.trim(),
  );
  expect(labels.length).toBeGreaterThan(0);
  const known = navLabels(catalog);
  expect(labels.filter((label) => !known.has(label))).toEqual([]);

  const text = await page.locator('body').innerText();
  expect(text).not.toContain('{{');
  // Every text node, screen-reader-only ones too, but not codes a page shows
  // on purpose in <code> (the audit log's action and table codes, #120 E4).
  const words = await page.evaluate(() => {
    const walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
    );
    const found: string[] = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.parentElement?.closest('code, script, style')) {
        found.push(...(node.textContent ?? '').split(/\s+/));
      }
    }
    return found;
  });
  const rawKeys = words
    .map((token) => token.replace(/^[("'«]+|[)"'».,:;]+$/g, ''))
    .filter((token) => RAW_KEY.test(token));
  expect(rawKeys).toEqual([]);
  expect(watched.missing).toEqual([]);
  expect(watched.preferenceWrites).toEqual([]);
}

for (const language of LANGUAGES) {
  test.describe(`in ${language}`, () => {
    test.describe('signed out', () => {
      test.use({
        storageState: async ({ baseURL }, use) => {
          await use({
            ...SIGNED_OUT_STATE,
            origins: [
              {
                origin: new URL(baseURL!).origin,
                localStorage: [{ name: 'classora.language', value: language }],
              },
            ],
          });
        },
      });

      test('the login page', async ({ page }) => {
        const catalog = CATALOGS[language];
        const missing: string[] = [];
        page.on('console', (message) => {
          if (message.text().startsWith(MISSING_KEY_LOG)) {
            missing.push(message.text());
          }
        });
        await page.goto('/login');
        await expect(page.locator('html')).toHaveAttribute('lang', language);
        await expect(
          page.getByRole('heading', {
            level: 1,
            name: catalog.auth.login.title,
          }),
        ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
        await expect(
          page.getByLabel(catalog.auth.login.username, { exact: true }),
        ).toBeVisible();
        await expect(
          page.getByLabel(catalog.auth.login.password, { exact: true }),
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: catalog.auth.login.submit }),
        ).toBeVisible();
        expect(await page.locator('body').innerText()).not.toContain('{{');
        expect(missing).toEqual([]);
      });
    });

    test.describe('signed in as the Owner', () => {
      for (const path of NAV_PATHS) {
        test(`page ${path}`, async ({ page }) => {
          const watched = await prepare(page, language);
          await page.goto(path);
          await settle(page);
          const link = page
            .getByRole('navigation', { name: CATALOGS[language].nav.label })
            .locator(`a[href="${path}"]`);
          test.skip((await link.count()) === 0, 'not in the Owner navigation');
          await checkPage(page, language, watched);
        });
      }

      test('the Owner navigation has no destination this spec misses', async ({
        page,
      }) => {
        const watched = await prepare(page, language);
        await page.goto('/');
        await settle(page);
        const hrefs = await page
          .getByRole('navigation', { name: CATALOGS[language].nav.label })
          .getByRole('link')
          .evaluateAll((links) => links.map((a) => a.getAttribute('href')));
        expect(hrefs.length).toBeGreaterThan(10);
        expect(hrefs.filter((href) => !NAV_PATHS.includes(href!))).toEqual([]);
        expect(watched.preferenceWrites).toEqual([]);
      });

      for (const [kind, list] of [
        ['student', '/api/v1/students?limit=1'],
        ['family', '/api/v1/families?limit=1'],
      ] as const) {
        test(`a demo ${kind} detail page`, async ({ page }) => {
          const watched = await prepare(page, language);
          await page.goto('/');
          await settle(page);
          const result = await api(page, list);
          expect(result.status).toBe(200);
          const [first] = (result.body as { items: { id: string }[] }).items;
          expect(first).toBeDefined();
          await page.goto(
            `/${kind === 'student' ? 'students' : 'families'}/${first!.id}`,
          );
          await checkPage(page, language, watched);
        });
      }
    });
  });
}
