import { fileURLToPath } from 'node:url';
import { expect, type Page } from '@playwright/test';
import { api } from './api';

// The shared signed-in state (#28 H1). e2e/global-setup.ts signs the demo
// Owner in once through the API and saves the browser state here
// (gitignored); every test starts from it unless it opts out. The shared
// session is never logged out.
export const OWNER_STATE_PATH = fileURLToPath(
  new URL('../../.auth/owner.json', import.meta.url),
);

/**
 * For tests that log in or out (#28 H2): a context WITHOUT the shared state.
 * A login sent with the shared cookie would revoke the shared session (#23
 * rotation), and a logout would end it.
 */
export const SIGNED_OUT_STATE = { cookies: [], origins: [] };

/**
 * Login budget (#27, ADR-0008): one run shares one `login.ip` budget of 50
 * per 15 minutes, and `demo.owner` has 10 failures per 15 minutes. Every
 * `make e2e` starts from an empty volume, so the budget is per run.
 * createActiveUser invites a tutor or guardian with the shared Owner session
 * (no login) and logs the Owner in only for a staff role (#36 rebalance).
 * Logins in the whole run today: global setup 1, auth/session.spec.ts 8 (2
 * of them wrong passwords), security/headers.spec.ts 1,
 * auth/invitations.spec.ts 2, auth/password-reset.spec.ts 6,
 * auth/registration.spec.ts 2 (the first refused while the account is
 * pending: 1 failure on `e2e.parent`'s own `login.username` budget),
 * users/suspend.spec.ts 4 (1 refused while the tutor is suspended: a failure
 * on `e2e.suspend.tutor`'s own budget), users/roles.spec.ts 6 (1 by
 * createActiveUser for the Administrator), students/students.spec.ts 1,
 * families/guardians.spec.ts 1 (the guardian; its account is a
 * self-registration the Owner approves, #36), families/families.spec.ts 1
 * (the guardian; its account is invited with the shared session, #172),
 * auth/privacy-notice.spec.ts 2, a11y/shell.spec.ts 1 (the privacy-notice
 * scan; #174), families/consent.spec.ts 4 (2 parents and 2 students; every
 * account there is a self-registration approved by the Owner or an
 * invitation, never createActiveUser; #35), preferences/preferences.spec.ts
 * 2 (the tutor on two devices; #36), tutors/profiles.spec.ts 1 (the tutor;
 * #43), tutors/qualifications.spec.ts 3 (the demo Owner's reauthenticated
 * API context for the tutor's role change, and the tutor; #44; and the
 * guardian who requests a session, #59; #60, #67 G8, #79 I8, #308 G3,
 * #310 G3, #315 H3 and #62b E1 reuse both contexts: 0 more),
 * scheduling/offers.spec.ts 3
 * (`demo.tutor`, a #195 demo account, #52; the demo Owner's
 * reauthenticated API context of createApprovedTutor for the substitute's
 * role change, #55; and `demo.student`, #313 G3, the one login reserved
 * for it; the #76 attendance, #77 closeout, #78 review, #305
 * resubmission, #322 progress and #323 course completion steps reuse the
 * contexts and the shared Owner session: 0 more; scheduling/overdue-closeouts.spec.ts (#301b) runs on the shared
 * Owner session: 0), billing/corrections.spec.ts 1 (the demo Owner in
 * its own context, for the password dialog of a correction; the shared
 * session is never reauthenticated; #386; #387's package withdrawal runs
 * in the same context and reauthentication window: 0 more) = 50 attempts,
 * the whole
 * `login.ip` budget: a new spec that needs a login must rebalance,
 * 2 failures for
 * `demo.owner`. Specs on the shared session add none (for example
 * courses/cohorts.spec.ts, #243 and #323). Keep the total well under both limits;
 * never add a setting that disables the limits.
 *
 * Token redemption (#29, #30, ADR-0008 D15): `token_redemption.ip` allows 20
 * lookups and redemptions per 15 minutes for the run's one IP, invitations
 * and resets together. createActiveUser redeems without a lookup (1 call
 * per user, #34). auth/invitations.spec.ts makes 3 (2 lookups, 1
 * redemption), auth/password-reset.spec.ts 5 (2 of them by
 * createActiveUser), users/suspend.spec.ts 1, users/roles.spec.ts 2,
 * students/students.spec.ts 1, auth/privacy-notice.spec.ts 1,
 * a11y/shell.spec.ts 1 and preferences/preferences.spec.ts 1 (all by
 * createActiveUser), families/families.spec.ts 1
 * (createGuardianAccountViaPage), families/consent.spec.ts 3 (the minor's
 * invitation page: 1 lookup, 1 redemption refused by the consent gate and
 * 1 that succeeds; #35), tutors/profiles.spec.ts 1 (createActiveUser; #43)
 * = 20, the whole budget: a new spec must invite without redeeming (as
 * a11y/shell.spec.ts does for its tutor, support/tutors.ts), get its
 * account through an approved self-registration (as
 * tutors/qualifications.spec.ts does with createApprovedTutor, #44) or
 * rebalance.
 */
export const LOGIN_ATTEMPTS_PER_RUN = 50;

/** Invitation and reset lookups and redemptions in one run (see above). */
export const TOKEN_REDEMPTIONS_PER_RUN = 20;

/**
 * Self-registration requests in one run (#169; `registration.ip` allows 10
 * per hour for the run's one IP): auth/registration.spec.ts sends 1,
 * families/consent.spec.ts 3 (createApprovedAccount for two parents and the
 * adult student, #35), families/guardians.spec.ts 1 (createApprovedAccount
 * for the guardian, #36), tutors/qualifications.spec.ts 2
 * (createApprovedTutor, #44; createApprovedAccount for the guardian who
 * requests a session, #59) and scheduling/offers.spec.ts 1
 * (createApprovedTutor for the substitute, #55) = 8.
 */
export const REGISTRATIONS_PER_RUN = 8;

/** Records CSP violations from the first script on (the #24 listener). */
export async function recordCspViolations(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const target = window as Window & { __cspViolations?: string[] };
    target.__cspViolations = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      target.__cspViolations?.push(
        `${event.violatedDirective} ${event.blockedURI}`,
      );
    });
  });
}

export async function cspViolations(page: Page): Promise<string[]> {
  return page.evaluate(
    () =>
      (window as Window & { __cspViolations?: string[] }).__cspViolations ?? [],
  );
}

/** "I accept" in either UI language (#174). */
const ACCEPT_BUTTON = /^(Acepto|I accept)$/;

/**
 * Accepts the privacy notice when the app shows it (#174 I1): every new user
 * must accept the current version before any other page. Call it once the
 * sign-in finished (the header shows "Signed in as …"); it asks /auth/me
 * (notice-exempt) and does nothing when the notice is already accepted. On
 * the notice page it clicks "I accept" and waits until the app returns to
 * the page the user was going to. No login and no token redemption.
 */
export async function acceptPrivacyNoticeIfShown(page: Page): Promise<void> {
  const me = await api(page, '/api/v1/auth/me');
  const body = me.body as { privacy_notice_required?: boolean } | null;
  if (me.status !== 200 || body?.privacy_notice_required !== true) {
    return;
  }
  await expect(page).toHaveURL(/\/privacy-notice/);
  await page
    .getByRole('main')
    .getByRole('button', { name: ACCEPT_BUTTON })
    .click();
  await expect(page).not.toHaveURL(/\/privacy-notice/);
}
