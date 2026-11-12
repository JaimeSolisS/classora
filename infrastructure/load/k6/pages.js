// Page views per role (#152): the API calls the web app makes for each page
// (apps/web/src/features/*/api.ts), sent as one batch like the browser does,
// with each page's share of the views (weight). A page returns its count of
// unexpected responses. Writes are idempotent replays (same values), so a
// rerun does not grow the dataset beyond notifications read and exports.
import http from 'k6/http';

const TARGET = (__ENV.TARGET || 'https://dev.classora.dev').replace(/\/$/, '');

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function addDays(d, n) {
  const copy = new Date(d.getTime());
  copy.setUTCDate(copy.getUTCDate() + n);
  return copy;
}

function week() {
  const today = new Date();
  const monday = addDays(today, -((today.getUTCDay() + 6) % 7));
  // Dates for date parameters; UTC instants (…Z) for the date-time ranges
  // of class-sessions and the tutor and student schedules.
  return {
    today: isoDate(today),
    from: isoDate(monday),
    to: isoDate(addDays(monday, 6)),
    todayAt: `${isoDate(today)}T00:00:00Z`,
    tomorrowAt: `${isoDate(addDays(today, 1))}T00:00:00Z`,
    fromAt: `${isoDate(monday)}T00:00:00Z`,
    toAt: `${isoDate(addDays(monday, 7))}T00:00:00Z`,
  };
}

function pick(list) {
  return list && list.length ? list[Math.floor(Math.random() * list.length)] : null;
}

function params(session, template, extra) {
  return Object.assign(
    {
      headers: { Accept: 'application/json' },
      cookies: { [session.cookieName]: { value: session.cookie, replace: true } },
      tags: { kind: 'api', name: template, write: 'no' },
      timeout: '30s',
    },
    extra || {},
  );
}

// GETs in parallel: [[template, url], ...] -> responses.
function batch(session, calls) {
  return http.batch(calls.map(([template, url]) => ['GET', `${TARGET}${url}`, null, params(session, template)]));
}

function failures(responses) {
  const bad = responses.filter((r) => r.status < 200 || r.status >= 400);
  if (__ENV.DEBUG) bad.forEach((r) => console.warn(`${r.status} ${r.request.method} ${r.url} ${String(r.body).slice(0, 160)}`));
  return bad.length;
}

// Every page also refreshes the shell's unread count.
function shell(calls) {
  return [['/api/v1/notifications/unread-count', '/api/v1/notifications/unread-count'], ...calls];
}

function write(session, h, method, template, url, body) {
  const res = h.request(session, method, { template, url }, body, {});
  // 409 (a stale version or a state that moved on) is an expected answer.
  if (__ENV.DEBUG && res.status >= 400) console.warn(`${res.status} ${method} ${url} ${String(res.body).slice(0, 160)}`);
  return res.status >= 200 && (res.status < 400 || res.status === 409) ? 0 : 1;
}

function pdf(session, h, template, url) {
  const res = http.get(`${TARGET}${url}`, params(session, template, { tags: { kind: 'pdf', name: template, write: 'no' } }));
  h.pdfDuration.add(res.timings.duration);
  return res.status === 200 ? 0 : 1;
}

// ---------------------------------------------------------------- staff
const staffPages = [
  {
    name: 'home',
    weight: 4,
    run(s) {
      const w = week();
      return failures(
        batch(
          s,
          shell([
            ['/api/v1/auth/me', '/api/v1/auth/me'],
            ['/api/v1/reports/dashboard/layout', '/api/v1/reports/dashboard/layout'],
            ['/api/v1/approval-requests', '/api/v1/approval-requests?status=open&limit=1'],
            ['/api/v1/billing/overdue-accounts', '/api/v1/billing/overdue-accounts?min_days_overdue=1&family_status=all&limit=1'],
            ['/api/v1/class-sessions', `/api/v1/class-sessions?from=${w.todayAt}&to=${w.tomorrowAt}`],
          ]),
        ),
      );
    },
  },
  {
    name: 'schedule-week',
    weight: 4,
    run(s) {
      const w = week();
      return failures(
        batch(
          s,
          shell([
            ['/api/v1/class-sessions', `/api/v1/class-sessions?from=${w.fromAt}&to=${w.toAt}`],
            ['/api/v1/tutors', '/api/v1/tutors?limit=100'],
            ['/api/v1/classrooms', '/api/v1/classrooms'],
            ['/api/v1/subjects', '/api/v1/subjects'],
          ]),
        ),
      );
    },
  },
  {
    name: 'students-and-detail',
    weight: 3,
    run(s) {
      const w = week();
      const [unread, list] = batch(s, shell([['/api/v1/students', '/api/v1/students?limit=50']]));
      let bad = failures([unread, list]);
      const student = list.status === 200 ? pick(list.json().items) : null;
      if (student) {
        bad += failures(
          batch(s, [
            ['/api/v1/students/{student_id}', `/api/v1/students/${student.id}`],
            ['/api/v1/students/{student_id}/schedule', `/api/v1/students/${student.id}/schedule?from=${w.fromAt}&to=${w.toAt}`],
            ['/api/v1/students/{student_id}/academic-history', `/api/v1/students/${student.id}/academic-history`],
          ]),
        );
      }
      return bad;
    },
  },
  {
    name: 'families-and-billing',
    weight: 3,
    run(s) {
      const [unread, list] = batch(s, shell([['/api/v1/families', '/api/v1/families?limit=50']]));
      let bad = failures([unread, list]);
      const family = list.status === 200 ? pick(list.json().items) : null;
      if (family) {
        const base = `/api/v1/families/${family.id}`;
        bad += failures(
          batch(s, [
            ['/api/v1/families/{family_id}', base],
            ['/api/v1/families/{family_id}/billing-account', `${base}/billing-account`],
            ['/api/v1/families/{family_id}/billing-account/charges', `${base}/billing-account/charges?limit=50`],
            ['/api/v1/families/{family_id}/billing-account/payments', `${base}/billing-account/payments?limit=50`],
          ]),
        );
      }
      return bad;
    },
  },
  {
    name: 'reports',
    weight: 2,
    run(s) {
      const w = week();
      const from = isoDate(addDays(new Date(), -90));
      if (!s.roles.includes('owner') && !s.roles.includes('finance_staff')) {
        return failures(batch(s, shell([['/api/v1/closeouts/overdue', '/api/v1/closeouts/overdue?limit=50']])));
      }
      return failures(
        batch(
          s,
          shell([
            ['/api/v1/reports/kpis/revenue-trend', `/api/v1/reports/kpis/revenue-trend?basis=accrual&from=${from}&to=${w.today}`],
            ['/api/v1/reports/finance/balances', '/api/v1/reports/finance/balances'],
            ['/api/v1/reports/kpis/tutor-performance', `/api/v1/reports/kpis/tutor-performance?from=${from}&to=${w.today}`],
          ]),
        ),
      );
    },
  },
  {
    name: 'finance-or-audit',
    weight: 2,
    run(s) {
      if (s.roles.includes('finance_staff')) {
        return failures(
          batch(
            s,
            shell([
              ['/api/v1/finance/expenses', '/api/v1/finance/expenses?limit=50'],
              ['/api/v1/payroll/periods', '/api/v1/payroll/periods?limit=20'],
            ]),
          ),
        );
      }
      return failures(batch(s, shell([['/api/v1/users', '/api/v1/users?limit=50']])));
    },
  },
  {
    name: 'payslip-pdf',
    // Finance: about one every 5 minutes (about 1 in 10 of its page views).
    weight: 2,
    run(s, h) {
      const slip = s.ctx.payslips ? pick(s.ctx.payslips) : null;
      if (!slip) return failures(batch(s, shell([])));
      return pdf(
        s,
        h,
        '/api/v1/payroll/tutors/{tutor_user_id}/payslips/{payslip_id}/pdf',
        `/api/v1/payroll/tutors/${slip.tutorId}/payslips/${slip.payslipId}/pdf`,
      );
    },
  },
  {
    name: 'export',
    // About one export every 10 minutes per staff VU (1 in 20 page views).
    weight: 1,
    run(s, h) {
      const body = { dataset: 'students', format: 'xlsx' };
      let res = h.request(s, 'POST', { template: '/api/v1/exports', url: '/api/v1/exports' }, body, {});
      if (res.status === 403 && String(res.body).includes('reauth_required')) {
        // The web app asks for the password again (recent reauthentication, #30).
        const reauth = h.request(
          s,
          'POST',
          { template: '/api/v1/auth/reauthenticate', url: '/api/v1/auth/reauthenticate' },
          { password: s.password },
          {},
        );
        if (reauth.status !== 200) return 1 + failures([reauth]);
        res = h.request(s, 'POST', { template: '/api/v1/exports', url: '/api/v1/exports' }, body, {});
      }
      return failures([res]) + failures(batch(s, [['/api/v1/exports', '/api/v1/exports?limit=20']]));
    },
  },
  {
    name: 'read-notifications',
    weight: 1,
    run(s, h) {
      return (
        failures(batch(s, [['/api/v1/notifications', '/api/v1/notifications?limit=20']])) +
        write(s, h, 'POST', '/api/v1/notifications/read-all', '/api/v1/notifications/read-all', { up_to: null })
      );
    },
  },
  {
    name: 'customize-dashboard',
    // Saves the current dashboard layout again (a versionless upsert).
    weight: 1,
    run(s, h) {
      const [layout] = batch(s, [['/api/v1/reports/dashboard/layout', '/api/v1/reports/dashboard/layout']]);
      if (layout.status !== 200) return failures([layout]);
      const widgets = (layout.json().widgets || []).slice(0, 20);
      if (!widgets.length) return 0;
      return write(s, h, 'PUT', '/api/v1/reports/dashboard/layout', '/api/v1/reports/dashboard/layout', { widgets });
    },
  },
];

// ---------------------------------------------------------------- tutor
const tutorPages = [
  {
    name: 'my-schedule',
    weight: 4,
    run(s) {
      const w = week();
      return failures(
        batch(
          s,
          shell([
            ['/api/v1/auth/me', '/api/v1/auth/me'],
            ['/api/v1/tutors/{user_id}/schedule', `/api/v1/tutors/${s.id}/schedule?from=${w.fromAt}&to=${w.toAt}`],
            ['/api/v1/assignment-offers', '/api/v1/assignment-offers?status=pending&limit=1'],
            ['/api/v1/assignments', '/api/v1/assignments?due=past&limit=20'],
          ]),
        ),
      );
    },
  },
  {
    name: 'session-closeout',
    // Opens a session of this week and re-marks its attendance with the same marks.
    weight: 4,
    run(s, h) {
      const w = week();
      const [unread, schedule] = batch(
        s,
        shell([['/api/v1/tutors/{user_id}/schedule', `/api/v1/tutors/${s.id}/schedule?from=${w.fromAt}&to=${w.tomorrowAt}`]]),
      );
      let bad = failures([unread, schedule]);
      const item = schedule.status === 200 ? pick(schedule.json().items) : null;
      if (!item) return bad;
      const base = `/api/v1/class-sessions/${item.id}`;
      // The tutor's session page (features/tutor-portal): attendance, notes, closeout.
      const [attendance, notes, closeout] = batch(s, [
        ['/api/v1/class-sessions/{class_session_id}/attendance', `${base}/attendance`],
        ['/api/v1/class-sessions/{class_session_id}/notes', `${base}/notes`],
        ['/api/v1/class-sessions/{class_session_id}/closeout', `${base}/closeout`],
      ]);
      bad += failures([attendance]);
      // A session without notes or a closeout yet answers 404 there.
      bad += [notes, closeout].filter((r) => r.status >= 500 || r.status === 0).length;
      if (attendance.status === 200) {
        const sheet = attendance.json();
        const marks = (sheet.items || [])
          .filter((m) => m.status && m.student)
          .map((m) => ({ student_id: m.student.id, status: m.status }));
        if (sheet.can_mark && marks.length) {
          bad += write(s, h, 'PUT', '/api/v1/class-sessions/{class_session_id}/attendance', `${base}/attendance`, {
            version: sheet.version,
            marks,
          });
        }
      }
      return bad;
    },
  },
  {
    name: 'student-history',
    weight: 2,
    run(s) {
      const w = week();
      const [unread, schedule] = batch(
        s,
        shell([['/api/v1/tutors/{user_id}/schedule', `/api/v1/tutors/${s.id}/schedule?from=${w.fromAt}&to=${w.toAt}`]]),
      );
      let bad = failures([unread, schedule]);
      const item = schedule.status === 200 ? pick(schedule.json().items) : null;
      const participant = item ? pick(item.participants) : null;
      const studentId = participant ? participant.student_id || participant.id : null;
      if (studentId) {
        bad += failures(
          batch(s, [
            ['/api/v1/students/{student_id}/academic-history', `/api/v1/students/${studentId}/academic-history`],
            ['/api/v1/students/{student_id}/assignments', `/api/v1/students/${studentId}/assignments?limit=20`],
          ]),
        );
      }
      return bad;
    },
  },
  {
    name: 'earnings',
    weight: 1,
    run(s) {
      return failures(
        batch(s, shell([['/api/v1/payroll/tutors/{tutor_user_id}/earnings', `/api/v1/payroll/tutors/${s.id}/earnings?limit=12`]])),
      );
    },
  },
  {
    name: 'notifications',
    weight: 1,
    run(s, h) {
      return (
        failures(batch(s, [['/api/v1/notifications', '/api/v1/notifications?limit=20']])) +
        write(s, h, 'POST', '/api/v1/notifications/read-all', '/api/v1/notifications/read-all', { up_to: null })
      );
    },
  },
];

// ---------------------------------------------------------------- guardian / student
function familyHome(s) {
  const w = week();
  const studentId = pick(s.ctx.studentIds);
  const calls = [
    ['/api/v1/auth/me', '/api/v1/auth/me'],
    ['/api/v1/schedule/family-students', '/api/v1/schedule/family-students'],
  ];
  if (studentId) {
    calls.push([
      '/api/v1/students/{student_id}/schedule',
      `/api/v1/students/${studentId}/schedule?from=${w.fromAt}&to=${w.toAt}`,
    ]);
    calls.push(['/api/v1/students/{student_id}/assignments', `/api/v1/students/${studentId}/assignments?limit=1`]);
  }
  return failures(batch(s, shell(calls)));
}

function homework(s) {
  const studentId = pick(s.ctx.studentIds);
  if (!studentId) return failures(batch(s, shell([])));
  return failures(
    batch(
      s,
      shell([
        ['/api/v1/students/{student_id}/assignments', `/api/v1/students/${studentId}/assignments?limit=20`],
        ['/api/v1/students/{student_id}/academic-history', `/api/v1/students/${studentId}/academic-history`],
      ]),
    ),
  );
}

const guardianPages = [
  { name: 'home', weight: 4, run: familyHome },
  {
    name: 'my-billing',
    weight: 3,
    run(s) {
      if (!s.ctx.familyId) return failures(batch(s, shell([])));
      const base = `/api/v1/families/${s.ctx.familyId}/billing-account`;
      return failures(
        batch(
          s,
          shell([
            ['/api/v1/families/{family_id}/billing-account', base],
            ['/api/v1/families/{family_id}/billing-account/charges', `${base}/charges?limit=50`],
            ['/api/v1/families/{family_id}/billing-account/payments', `${base}/payments?limit=50`],
          ]),
        ),
      );
    },
  },
  {
    name: 'receipt-pdf',
    // About one every 5 minutes per guardian VU (1 in 10 page views).
    weight: 1.2,
    run(s, h) {
      const receipt = pick(s.ctx.receiptIds);
      if (!receipt || !s.ctx.familyId) return 0;
      return pdf(
        s,
        h,
        '/api/v1/families/{family_id}/billing-account/receipts/{receipt_id}/pdf',
        `/api/v1/families/${s.ctx.familyId}/billing-account/receipts/${receipt}/pdf`,
      );
    },
  },
  { name: 'homework', weight: 2, run: homework },
  {
    name: 'notifications',
    weight: 1.8,
    run(s, h) {
      return (
        failures(batch(s, [['/api/v1/notifications', '/api/v1/notifications?limit=20']])) +
        write(s, h, 'POST', '/api/v1/notifications/read-all', '/api/v1/notifications/read-all', { up_to: null })
      );
    },
  },
];

const studentPages = [
  { name: 'home', weight: 4, run: familyHome },
  { name: 'homework', weight: 4, run: homework },
  {
    name: 'notifications',
    weight: 2,
    run(s) {
      return failures(batch(s, shell([['/api/v1/notifications', '/api/v1/notifications?limit=20']])));
    },
  },
];

export const pages = {
  staff: staffPages,
  tutor: tutorPages,
  guardian: guardianPages,
  student: studentPages,
};
