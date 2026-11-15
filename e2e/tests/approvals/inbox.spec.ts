import { expect, test, type APIRequest, type Locator } from '@playwright/test';
import { expectNoAxeViolations, switchColorScheme } from '../support/a11y';
import { api } from '../support/api';
import {
  cspViolations,
  recordCspViolations,
  SIGNED_OUT_STATE,
} from '../support/auth';
import { common, fill } from '../support/catalog';

// The approval inbox with the keyboard (M8-01b G3, E2): two registration
// requests sent through the public API; the demo Owner (the shared signed-in
// state) filters the inbox to registrations, sorts it newest first, opens
// the newest request's detail panel with Enter, assigns it to themself
// ("Asignada a mí" then lists it), approves it in the panel and rejects the
// other with a reason. An axe scan runs with the panel open, in light and
// dark. Native selects are set with `selectOption` on the focused control
// (no pointer); every button is reached by focus and pressed with Enter.
// The other approval types are decided in their own specs (qualifications,
// availability, offers, session and change requests, closeouts, progress
// reports, payroll periods) and in Vitest and the API tests (E2).
//
// Budgets (see support/auth.ts): 0 logins, 0 redemptions, 2 registration
// requests (`registration.ip`; REGISTRATIONS_PER_RUN). The usernames are
// unique per run (every `make e2e` starts from an empty database).

const TEST_TIMEOUT_MS = 90_000;

const OLDER = { username: 'e2e.inbox.older', displayName: 'E2E Inbox Older' };
const NEWER = { username: 'e2e.inbox.newer', displayName: 'E2E Inbox Newer' };
const PASSWORD = 'e2e-inbox-password';
const REASON = 'No reconocemos a esta familia.';

function summaryOf(who: typeof OLDER): string {
  return fill(common.approvals.summary.registration, {
    name: who.displayName,
    username: who.username,
    role: common.roles['guardian']!,
  });
}

/** Sends one self-registration request from a signed-out API context. */
async function requestAccount(
  request: APIRequest,
  baseURL: string,
  who: typeof OLDER,
): Promise<void> {
  // SIGNED_OUT_STATE: a new API context would otherwise inherit the shared
  // Owner session (see support/users.ts).
  const anonymous = await request.newContext({
    baseURL,
    storageState: SIGNED_OUT_STATE,
    extraHTTPHeaders: { Origin: new URL(baseURL).origin },
  });
  try {
    const sent = await anonymous.post('/api/v1/auth/registrations', {
      data: {
        username: who.username,
        display_name: who.displayName,
        password: PASSWORD,
        role: 'guardian',
      },
    });
    expect(sent.status()).toBe(202);
  } finally {
    await anonymous.dispose();
  }
}

async function press(control: Locator, key = 'Enter'): Promise<void> {
  await control.focus();
  await expect(control).toBeFocused();
  await control.page().keyboard.press(key);
}

test('the Owner filters, sorts, assigns and decides in the detail panel with the keyboard', async ({
  page,
  playwright,
  baseURL,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));

  // 1. Two registration requests, the newer one second.
  await page.goto('/approvals');
  await requestAccount(playwright.request, baseURL!, OLDER);
  await requestAccount(playwright.request, baseURL!, NEWER);
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const myName = (me.body as { display_name: string }).display_name;

  // 2. Registrations only, newest first.
  const main = page.getByRole('main');
  await expect(
    main.getByRole('heading', { level: 1, name: common.approvals.title }),
  ).toBeVisible();
  const type = main.getByLabel(common.approvals.filter.type, { exact: true });
  await type.focus();
  await type.selectOption('identity.registration');
  await expect(page).toHaveURL('/approvals?type=identity.registration');
  const sort = main.getByLabel(common.approvals.filter.sort, { exact: true });
  await sort.focus();
  await sort.selectOption('newest');
  await expect(page).toHaveURL(
    '/approvals?type=identity.registration&sort=newest',
  );
  const viewNewer = main.getByRole('button', {
    name: fill(common.approvals.list.viewFor, { summary: summaryOf(NEWER) }),
  });
  const viewOlder = main.getByRole('button', {
    name: fill(common.approvals.list.viewFor, { summary: summaryOf(OLDER) }),
  });
  await expect(viewNewer).toBeVisible();
  await expect(viewOlder).toBeVisible();
  // The list keeps showing the previous (priority, so oldest-first) result
  // until the newest-first one arrives (keepPreviousData), with both rows in
  // it: wait for the new order before measuring (#524).
  await expect(main.locator('tbody tr')).toContainText([
    summaryOf(NEWER),
    summaryOf(OLDER),
  ]);
  const [newerBox, olderBox] = [
    await viewNewer.boundingBox(),
    await viewOlder.boundingBox(),
  ];
  expect(newerBox!.y).toBeLessThan(olderBox!.y);

  // 3. Enter opens the newest request's panel (?item=); axe in both themes.
  await press(viewNewer);
  const panel = page.getByRole('dialog', {
    name: fill(common.approvals.panel.title, {
      type: common.approvals.types.identity_registration,
      summary: summaryOf(NEWER),
    }),
  });
  await expect(panel).toBeVisible();
  await expect(page).toHaveURL(/[?&]item=[0-9a-f-]{36}/);
  await expect(panel).toContainText(NEWER.username);
  for (const scheme of ['light', 'dark'] as const) {
    await switchColorScheme(page, scheme);
    await expectNoAxeViolations(page);
  }
  await switchColorScheme(page, 'light');

  // 4. "Asignarme"; Escape closes the panel and focus goes back to the row.
  await press(
    panel.getByRole('button', {
      name: common.approvals.assign.toMe,
      exact: true,
    }),
  );
  await expect(main.getByRole('status')).toHaveText(
    fill(common.approvals.assign.assignedTo, { name: myName }),
  );
  await expect(
    panel.getByRole('button', { name: common.approvals.assign.toMe }),
  ).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(viewNewer).toBeFocused();

  // 5. "Asignadas a mí" lists it, and not the other.
  const assignee = main.getByLabel(common.approvals.filter.assignee, {
    exact: true,
  });
  await assignee.focus();
  await assignee.selectOption('me');
  await expect(page).toHaveURL(/assignee=me/);
  await expect(viewNewer).toBeVisible();
  await expect(viewOlder).toHaveCount(0);

  // 6. Approve it in the panel: the decision dialog opens on top.
  await press(viewNewer);
  await expect(panel).toBeVisible();
  await press(
    panel.getByRole('button', {
      name: common.approvals.actions.approve,
      exact: true,
    }),
  );
  const approveDialog = page.getByRole('dialog', {
    name: fill(common.approvals.dialog.approveTitle, {
      summary: summaryOf(NEWER),
    }),
  });
  await expect(approveDialog).toBeVisible();
  await press(
    approveDialog.getByRole('button', {
      name: common.approvals.actions.approve,
      exact: true,
    }),
  );
  await expect(approveDialog).toBeHidden();
  await expect(panel).toContainText(common.approvals.status.approved);
  await expect(main.getByRole('status')).toHaveText(
    common.approvals.done.approved,
  );
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();

  // 7. Back to anyone's: reject the other one with a reason.
  await assignee.focus();
  await assignee.selectOption('any');
  await expect(viewOlder).toBeVisible();
  await expect(viewNewer).toHaveCount(0);
  await press(viewOlder);
  const olderPanel = page.getByRole('dialog', {
    name: fill(common.approvals.panel.title, {
      type: common.approvals.types.identity_registration,
      summary: summaryOf(OLDER),
    }),
  });
  await expect(olderPanel).toBeVisible();
  await press(
    olderPanel.getByRole('button', {
      name: common.approvals.actions.reject,
      exact: true,
    }),
  );
  const rejectDialog = page.getByRole('dialog', {
    name: fill(common.approvals.dialog.rejectTitle, {
      summary: summaryOf(OLDER),
    }),
  });
  await expect(rejectDialog).toBeVisible();
  const reason = rejectDialog.getByLabel(
    common.approvals.dialog.reasonOptionalLabel,
    { exact: true },
  );
  await reason.focus();
  await page.keyboard.type(REASON);
  await press(
    rejectDialog.getByRole('button', {
      name: common.approvals.actions.reject,
      exact: true,
    }),
  );
  await expect(rejectDialog).toBeHidden();
  await expect(olderPanel).toContainText(common.approvals.status.rejected);
  await expect(olderPanel).toContainText(REASON);
  await expect(main.getByRole('status')).toHaveText(
    common.approvals.done.rejected,
  );
  await page.keyboard.press('Escape');
  await expect(olderPanel).toBeHidden();
  await expect(viewOlder).toHaveCount(0);

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
