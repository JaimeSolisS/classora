// Classora load profile (#152, M10-04; ADR-0018, runbook _docs/runbooks/load-test.md).
// Run through `make load-test PROFILE=p1|p2|p3|staff-600 [TARGET=…] [VUS=…] [DURATION=…]`.
//
// Every virtual user (VU) is one signed-in person of a role, browsing page
// views (the API calls the web app makes for that page, sent together like
// the browser does) with 20-40 s of think time, plus one SSE notification
// stream per VU in the `sse` scenario. setup() signs each account in ONCE
// (at most 40 logins, under login.ip 50 per 15 minutes) and the VUs of an
// account share its session cookie and CSRF token.
//
// Mix per 1x (P1): 4 staff (owner, administrator, coordinator, finance),
// 8 tutors, 15 guardians, 5 students. P2 = 3x for DURATION (default 15m).
// P3 = a ramp from 3x to 8x over 10 minutes (stopped early by an abort
// threshold). staff-600 = only the staff VUs at 3x, for the run with the
// default per-IP limit (600/min, #163) still on.
//
// Pass thresholds are judged SERVER-side (Prometheus); the k6 thresholds
// below are the client-side view (includes the round trip to us-east-2).
import http from 'k6/http';
import { sleep } from 'k6';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';
import { pages } from './pages.js';

const TARGET = (__ENV.TARGET || 'https://dev.classora.dev').replace(/\/$/, '');
const PROFILE = __ENV.PROFILE || 'p1';
const ACCOUNTS_FILE = __ENV.ACCOUNTS || '/load/accounts.local.json';
const THINK_MIN = Number(__ENV.THINK_MIN || 20);
const THINK_MAX = Number(__ENV.THINK_MAX || 40);

const MIX = { staff: 4, tutor: 8, guardian: 15, student: 5 };
// Accounts signed in per role (40 in total, under login.ip 50 per 15 min).
const ACCOUNT_CAP = { staff: 4, tutor: 12, guardian: 18, student: 6 };
const STAFF_ROLES = ['owner', 'administrator', 'academic_coordinator', 'finance_staff'];

const accountsFile = JSON.parse(open(ACCOUNTS_FILE));

export const pdfDuration = new Trend('pdf_duration', true);
export const pageErrors = new Counter('page_errors');

function roleCounts(scale) {
  const counts = {};
  if (__ENV.VUS) {
    const total = Number(__ENV.VUS);
    for (const role of Object.keys(MIX)) {
      counts[role] = Math.max(1, Math.round((MIX[role] * total) / 32));
    }
    return counts;
  }
  for (const role of Object.keys(MIX)) counts[role] = Math.round(MIX[role] * scale);
  return counts;
}

function profileStages() {
  const duration = __ENV.DURATION;
  switch (PROFILE) {
    case 'p1':
      return { roles: Object.keys(MIX), scale: 1, peak: 1, stages: [{ duration: duration || '30m', target: 1 }] };
    case 'p2':
      return { roles: Object.keys(MIX), scale: 3, peak: 3, stages: [{ duration: duration || '15m', target: 1 }] };
    case 'p3':
      // 3x -> 8x in 10 minutes, then 2 minutes at 8x (targets are multiples of 1x).
      return {
        roles: Object.keys(MIX),
        scale: 1,
        peak: 8,
        ramp: [
          { duration: '1m', target: 3 },
          { duration: duration || '10m', target: 8 },
          { duration: '2m', target: 8 },
        ],
      };
    case 'staff-600':
      return { roles: ['staff'], scale: 3, peak: 3, stages: [{ duration: duration || '5m', target: 1 }] };
    default:
      throw new Error(`unknown PROFILE ${PROFILE} (p1, p2, p3, staff-600)`);
  }
}

function buildScenarios() {
  const plan = profileStages();
  const scenarios = {};
  let streams = 0;
  for (const role of plan.roles) {
    if (plan.ramp) {
      const base = MIX[role];
      scenarios[role] = {
        executor: 'ramping-vus',
        exec: 'browse',
        env: { ROLE: role },
        startVUs: base,
        stages: plan.ramp.map((s) => ({ duration: s.duration, target: base * s.target })),
        gracefulRampDown: '30s',
        tags: { role },
      };
      streams += base * plan.peak;
    } else {
      const vus = roleCounts(plan.scale)[role];
      if (vus === 0) continue;
      scenarios[role] = {
        executor: 'constant-vus',
        exec: 'browse',
        env: { ROLE: role },
        vus,
        duration: plan.stages[0].duration,
        tags: { role },
      };
      streams += vus;
    }
  }
  // One SSE stream per browsing VU, as one VU each (a held stream blocks its VU).
  for (const role of Object.keys(scenarios)) {
    const s = scenarios[role];
    scenarios[`sse_${role}`] = Object.assign({}, s, { exec: 'stream', tags: { role, kind: 'sse' } });
  }
  return { scenarios, streams };
}

const built = buildScenarios();

export const options = {
  scenarios: built.scenarios,
  setupTimeout: '5m',
  discardResponseBodies: false,
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    'http_req_failed{kind:api}': [
      { threshold: 'rate<0.005' },
      // P3 breakpoint: stop at errors > 2% (1-minute delay before it counts).
      ...(PROFILE === 'p3' ? [{ threshold: 'rate<0.02', abortOnFail: true, delayAbortEval: '1m' }] : []),
    ],
    'http_req_failed{kind:pdf}': [{ threshold: 'rate<0.005' }],
    'http_req_duration{kind:api,method:GET}': ['p(95)<500'],
    'http_req_duration{kind:api,write:yes}': ['p(95)<1000'],
    pdf_duration: ['p(95)<3000'],
    // P3 breakpoint: stop at p95 > 2 s (client-side, 1-minute delay).
    ...(PROFILE === 'p3'
      ? { 'http_req_duration{kind:api}': [{ threshold: 'p(95)<2000', abortOnFail: true, delayAbortEval: '1m' }] }
      : {}),
  },
};

function roleOf(account) {
  const roles = account.roles;
  if (roles.some((r) => STAFF_ROLES.includes(r))) return 'staff';
  if (roles.includes('tutor')) return 'tutor';
  if (roles.includes('guardian')) return 'guardian';
  if (roles.includes('student')) return 'student';
  return null;
}

function headers(session, unsafe) {
  const h = { Accept: 'application/json' };
  if (unsafe) {
    h['Content-Type'] = 'application/json';
    h.Origin = TARGET;
    h['X-CSRF-Token'] = session.csrf;
  }
  return h;
}

export function request(session, method, path, body, extraTags, alsoExpected) {
  const unsafe = method !== 'GET';
  const params = {
    headers: headers(session, unsafe),
    cookies: { [session.cookieName]: { value: session.cookie, replace: true } },
    tags: Object.assign({ kind: 'api', name: path.template, write: unsafe ? 'yes' : 'no' }, extraTags || {}),
    timeout: '30s',
  };
  // 409 (stale version, a state that moved on) is an expected answer to a write.
  if (unsafe) params.responseCallback = http.expectedStatuses({ min: 200, max: 399 }, 409, ...(alsoExpected || []));
  return http.request(method, `${TARGET}${path.url}`, body === undefined ? null : JSON.stringify(body), params);
}

function getJson(session, url) {
  const res = http.get(`${TARGET}${url}`, {
    headers: headers(session, false),
    cookies: { [session.cookieName]: { value: session.cookie, replace: true } },
    tags: { kind: 'setup' },
  });
  if (res.status !== 200) throw new Error(`setup GET ${url}: ${res.status}`);
  return res.json();
}

function login(account) {
  const res = http.post(
    `${TARGET}/api/v1/auth/login`,
    JSON.stringify({ username: account.username, password: account.password }),
    {
      headers: { 'Content-Type': 'application/json', Origin: TARGET, Accept: 'application/json' },
      // A fresh jar: a login that carries another live session cookie revokes it.
      jar: new http.CookieJar(),
      tags: { kind: 'setup' },
    },
  );
  if (res.status !== 200) throw new Error(`login ${account.username}: ${res.status}`);
  const names = Object.keys(res.cookies);
  if (names.length !== 1) throw new Error(`login ${account.username}: expected one cookie`);
  const me = res.json();
  return {
    username: account.username,
    id: me.id,
    roles: me.roles,
    csrf: me.csrf_token,
    cookieName: names[0],
    cookie: res.cookies[names[0]][0].value,
  };
}

// Ids each role's pages need, read once per account (setup only).
function discover(session, role) {
  const ctx = {};
  const status = (url) =>
    http.get(`${TARGET}${url}`, {
      headers: headers(session, false),
      cookies: { [session.cookieName]: { value: session.cookie, replace: true } },
      tags: { kind: 'setup' },
    }).status;
  if (role === 'guardian' || role === 'student') {
    // Only the students whose pages this account may open (the web app shows
    // a "consent required" state instead of calling them for the others).
    const students = getJson(session, '/api/v1/schedule/family-students').items || [];
    const today = new Date().toISOString().slice(0, 10);
    ctx.studentIds = students
      .map((s) => s.id || s.student_id)
      .filter(Boolean)
      .filter(
        (id) =>
          status(`/api/v1/students/${id}/schedule?from=${today}T00:00:00Z&to=${today}T23:00:00Z`) === 200 &&
          status(`/api/v1/students/${id}/assignments?limit=1`) === 200 &&
          status(`/api/v1/students/${id}/academic-history`) === 200,
      );
  }
  if (role === 'staff') {
    // Pages the web app hides from this staff role (no permission): skipped.
    const probes = {
      '/api/v1/billing/overdue-accounts': '/api/v1/billing/overdue-accounts?limit=1',
      '/api/v1/approval-requests': '/api/v1/approval-requests?status=open&limit=1',
      '/api/v1/tutors': '/api/v1/tutors?limit=1',
      '/api/v1/users': '/api/v1/users?limit=1',
      '/api/v1/reports/kpis/tutor-performance': `/api/v1/reports/kpis/tutor-performance?from=2026-09-01&to=2026-09-30`,
      '/api/v1/reports/kpis/revenue-trend': `/api/v1/reports/kpis/revenue-trend?basis=accrual&from=2026-09-01&to=2026-09-30`,
      '/api/v1/reports/finance/balances': '/api/v1/reports/finance/balances',
      '/api/v1/closeouts/overdue': '/api/v1/closeouts/overdue?limit=1',
      '/api/v1/finance/expenses': '/api/v1/finance/expenses?limit=1',
      '/api/v1/payroll/periods': '/api/v1/payroll/periods?limit=1',
      '/api/v1/families': '/api/v1/families?limit=1',
      '/api/v1/students': '/api/v1/students?limit=1',
      '/api/v1/class-sessions': '/api/v1/class-sessions?from=2026-11-02T00:00:00Z&to=2026-11-03T00:00:00Z',
      '/api/v1/exports': '/api/v1/exports?limit=1',
    };
    ctx.forbidden = Object.keys(probes).filter((template) => status(probes[template]) === 403);
  }
  if (role === 'guardian') {
    const own = getJson(session, '/api/v1/families/own');
    ctx.familyId = own.family ? own.family.id : null;
    ctx.receiptIds = [];
    if (ctx.familyId) {
      const payments = getJson(session, `/api/v1/families/${ctx.familyId}/billing-account/payments?limit=50`);
      ctx.receiptIds = (payments.items || []).filter((p) => p.receipt).map((p) => p.receipt.id);
    }
  }
  if (role === 'staff' && session.roles.includes('finance_staff') && !ctx.forbidden.includes('/api/v1/payroll/periods')) {
    ctx.payslips = [];
    const periods = getJson(session, '/api/v1/payroll/periods?limit=20').items || [];
    const tutors = getJson(session, '/api/v1/payroll/tutors').items || [];
    for (const period of periods.slice(0, 3)) {
      for (const tutor of tutors.slice(0, 6)) {
        const tutorId = tutor.user_id || tutor.id;
        const res = http.get(`${TARGET}/api/v1/payroll/tutors/${tutorId}/earnings/${period.id}`, {
          headers: headers(session, false),
          cookies: { [session.cookieName]: { value: session.cookie, replace: true } },
          tags: { kind: 'setup' },
        });
        if (res.status === 200 && res.json().payslip) {
          ctx.payslips.push({ tutorId, payslipId: res.json().payslip.id });
        }
      }
    }
  }
  return ctx;
}

export function setup() {
  const plan = profileStages();
  const peak = plan.ramp ? null : roleCounts(plan.scale);
  const byRole = { staff: [], tutor: [], guardian: [], student: [] };
  for (const account of accountsFile.accounts) {
    const role = roleOf(account);
    if (role) byRole[role].push(account);
  }
  // Staff: the four staff roles first, one account each.
  byRole.staff.sort((a, b) => STAFF_ROLES.indexOf(a.roles[0]) - STAFF_ROLES.indexOf(b.roles[0]));
  const sessions = {};
  for (const role of plan.roles) {
    const wanted = Math.min(ACCOUNT_CAP[role], peak ? Math.max(peak[role], 1) : ACCOUNT_CAP[role]);
    sessions[role] = byRole[role].slice(0, wanted).map((account) => {
      const session = login(account);
      session.ctx = discover(session, role);
      // Staff exports need a recent reauthentication; kept in memory only.
      if (role === 'staff') session.password = account.password;
      return session;
    });
    if (sessions[role].length === 0) throw new Error(`no ${role} account in ${ACCOUNTS_FILE}`);
  }
  return { sessions, startedAt: new Date().toISOString() };
}

function sessionFor(data, role) {
  const list = data.sessions[role];
  return list[(exec.vu.idInTest - 1) % list.length];
}

function think() {
  sleep(THINK_MIN + Math.random() * (THINK_MAX - THINK_MIN));
}

export function browse(data) {
  const role = __ENV.ROLE;
  const session = sessionFor(data, role);
  // People arrive spread out: a VU's first page view waits 0 to THINK_MAX s
  // (all VUs starting in the same second is a burst no center produces).
  if (exec.vu.iterationInScenario === 0) sleep(Math.random() * THINK_MAX);
  const rolePages = pages[role];
  // Weighted pick of the next page view.
  const total = rolePages.reduce((sum, p) => sum + p.weight, 0);
  let pick = Math.random() * total;
  let page = rolePages[0];
  for (const candidate of rolePages) {
    pick -= candidate.weight;
    if (pick <= 0) {
      page = candidate;
      break;
    }
  }
  const failures = page.run(session, { request, pdfDuration });
  if (failures > 0) pageErrors.add(failures, { page: page.name, role });
  think();
}

export function stream(data) {
  const role = __ENV.ROLE;
  const session = sessionFor(data, role);
  if (exec.vu.iterationInScenario === 0) sleep(Math.random() * THINK_MAX);
  // Held open up to 5 minutes, like a tab; the browser reconnects after 5 s.
  const res = http.get(`${TARGET}/api/v1/notifications/stream`, {
    headers: { Accept: 'text/event-stream' },
    cookies: { [session.cookieName]: { value: session.cookie, replace: true } },
    responseType: 'none',
    timeout: '300s',
    tags: { kind: 'sse', name: '/api/v1/notifications/stream' },
  });
  if (__ENV.DEBUG && res.status !== 200) console.warn(`stream ${res.status} ${res.error}`);
  sleep(5);
}
