import { describe, expect, it } from 'vitest';
import { i18n } from '@/i18n';
import es from '@/i18n/locales/es/common.json';
import en from '@/i18n/locales/en/common.json';
import { formatMoney } from '@/lib/money';
import { formatInstant, formatLocalDate } from '@/lib/time';
import type { Formatters } from '@/lib/formatters';
import {
  APPROVAL_TYPE_LABELS,
  APPROVAL_TYPES,
  REJECT_REASON_REQUIRED,
  type ApprovalRequestItem,
} from './api';
import { approvalDetails, DETAIL_RENDERERS } from './details';
import { ITEMS_BY_TYPE } from './fixtures';

// M8-01b B2 and E1: the detail panel's per-type fields, and the pin of the
// approval types against #118 F2 (test_approval_inbox_types.py EXPECTED).

/**
 * #118 F2's table: each registered type and its `reason_required_on_reject`.
 * A new approval type must be added here, to APPROVAL_TYPE_LABELS (es and
 * en labels), DETAIL_RENDERERS and REJECT_REASON_REQUIRED, and to the API's
 * EXPECTED table.
 */
const EXPECTED_TYPES: Readonly<Record<string, boolean>> = {
  'academics.progress_report': true,
  'academics.session_closeout': true,
  'identity.registration': false,
  'payroll.period': true,
  'scheduling.change_request': true,
  'scheduling.occurrence_conflict': true,
  'scheduling.offer_reassignment': true,
  'scheduling.session_request': true,
  'staff.availability_change': true,
  'staff.qualification': true,
};

const t = i18n.getFixedT('en');

const formatters: Formatters = {
  formatInstant: (instant, style) =>
    formatInstant(instant, {
      timeZone: 'America/Mexico_City',
      language: 'en',
      style,
    }),
  formatLocalDate: (localDate, style) =>
    formatLocalDate(localDate, { language: 'en', style }),
  formatMoney,
};

function rows(item: ApprovalRequestItem) {
  const details = approvalDetails(item, t, formatters);
  return {
    known: details.known,
    fields: Object.fromEntries(
      details.rows.map((row) => [row.label, row.value]),
    ),
  };
}

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-/i;

describe('the approval type pin (E1)', () => {
  it('knows the same ten types as #118 F2', () => {
    expect([...APPROVAL_TYPES].sort()).toEqual(
      Object.keys(EXPECTED_TYPES).sort(),
    );
    expect(Object.keys(DETAIL_RENDERERS).sort()).toEqual(
      Object.keys(EXPECTED_TYPES).sort(),
    );
    expect(Object.keys(ITEMS_BY_TYPE).sort()).toEqual(
      Object.keys(EXPECTED_TYPES).sort(),
    );
  });

  it('gives each type a label in es and en', () => {
    for (const type of APPROVAL_TYPES) {
      const key = APPROVAL_TYPE_LABELS[type]!.replace('approvals.types.', '');
      expect(es.approvals.types, type).toHaveProperty(key);
      expect(en.approvals.types, type).toHaveProperty(key);
    }
  });

  it('matches reason_required_on_reject', () => {
    for (const [type, required] of Object.entries(EXPECTED_TYPES)) {
      expect(REJECT_REASON_REQUIRED.has(type), type).toBe(required);
    }
    expect(
      [...REJECT_REASON_REQUIRED].every((type) => type in EXPECTED_TYPES),
    ).toBe(true);
  });
});

describe('the per-type fields (B2)', () => {
  it('every type has fields, without an id', () => {
    for (const type of APPROVAL_TYPES) {
      const { known, fields } = rows(ITEMS_BY_TYPE[type]!);
      expect(known, type).toBe(true);
      expect(Object.keys(fields).length, type).toBeGreaterThan(1);
      expect(JSON.stringify(fields), type).not.toMatch(UUID);
    }
  });

  it('a registration', () => {
    expect(rows(ITEMS_BY_TYPE['identity.registration']!).fields).toEqual({
      Name: 'Rosa Díaz',
      Username: 'rosa.diaz',
      'Requested role': 'Parent or guardian',
    });
  });

  it('a qualification', () => {
    expect(rows(ITEMS_BY_TYPE['staff.qualification']!).fields).toEqual({
      Tutor: 'Carmen Ruiz',
      Subject: 'Física',
      Level: 'Preparatoria',
    });
  });

  it("a substitute's declined offer", () => {
    expect(
      rows(ITEMS_BY_TYPE['scheduling.offer_reassignment']!).fields,
    ).toEqual({
      Tutor: 'Luis Gómez',
      'Offer outcome': 'Declined by the tutor',
      'Offer kind': 'Substitution',
      Subject: 'Química',
      'Date and time': 'Apr 7, 2031, 10:00 AM',
      'Session state': 'Confirmed',
      'Still taught by': 'Carmen Ruiz',
      "Tutor's note": 'Tengo otra clase.',
    });
  });

  it('a series occurrence in conflict', () => {
    expect(
      rows(ITEMS_BY_TYPE['scheduling.occurrence_conflict']!).fields,
    ).toEqual({
      'Date and time': 'Apr 8, 2031, 10:00 AM',
      Subject: 'Matemáticas',
      Students: ['Ana López', 'Beto Ruiz'],
      Tutor: 'Carmen Ruiz',
      Conflicts: ['Inactive tutor'],
    });
  });

  it('a session request', () => {
    expect(rows(ITEMS_BY_TYPE['scheduling.session_request']!).fields).toEqual({
      Student: 'Ana López',
      Subject: 'Matemáticas',
      Level: 'Secundaria',
      'Date and time': 'Apr 9, 2031, 10:00 AM',
      Ends: '11:00 AM',
      Duration: '60 min',
      'Requested by': 'Rosa Díaz',
      Note: 'Por la tarde, por favor.',
      'Request status': 'Pending',
    });
  });

  it('a change request', () => {
    expect(rows(ITEMS_BY_TYPE['scheduling.change_request']!).fields).toEqual({
      'Change kind': 'Reschedule',
      'Initiated by': 'The family',
      Student: 'Ana López',
      Subject: 'Matemáticas',
      Level: 'Secundaria',
      'Date and time': 'Apr 10, 2031, 10:00 AM',
      'Proposed new time': 'Apr 11, 2031, 10:00 AM',
      Classification: 'On time',
      'Request status': 'Pending',
    });
  });

  it('an availability change, with its affected classes', () => {
    const { fields } = rows(ITEMS_BY_TYPE['staff.availability_change']!);
    expect(fields.Tutor).toBe('Carmen Ruiz');
    expect(fields.Change).toContain('Time off');
    expect(fields['Affected classes']).toBe('3');
    expect(fields['Affected classes (when the change was sent)']).toEqual([
      expect.stringContaining('Física'),
      expect.stringContaining('Química'),
      'and 1 more',
    ]);
    expect(fields['Request status']).toBe('Pending');
  });

  it('a session closeout', () => {
    expect(rows(ITEMS_BY_TYPE['academics.session_closeout']!).fields).toEqual({
      Review: 'New',
      Subject: 'Matemáticas',
      'Date and time': 'Apr 7, 2031, 10:00 AM',
      Tutor: 'Carmen Ruiz',
      'Class type': 'Group',
      'Scheduled length': '60 min',
      'Actual length': '55 min',
      'Students in the class': '3',
    });
  });

  it('a progress report', () => {
    expect(rows(ITEMS_BY_TYPE['academics.progress_report']!).fields).toEqual({
      Student: 'Ana López',
      'Report type': 'Weekly',
      Period: 'Mar 3, 2031 – Mar 9, 2031',
      Subject: 'Matemáticas',
      Author: 'Carmen Ruiz',
      Submitted: 'Mar 5, 2031, 12:00 PM',
    });
  });

  it('a payroll period, with no amount', () => {
    expect(rows(ITEMS_BY_TYPE['payroll.period']!).fields).toEqual({
      Tutor: 'Carmen Ruiz',
      Period: 'Mar 31, 2031 – Apr 6, 2031',
      'Prepared by': 'Laura Méndez',
      'Prepared at': 'Apr 7, 2031, 9:00 AM',
    });
  });

  it('an unknown type, or a subject its renderer cannot read, shows the raw keys', () => {
    const unknown = {
      ...ITEMS_BY_TYPE['identity.registration']!,
      request_type: 'finance.refund',
      subject: { amount: '10.00', items: [1, 2], note: null },
    };
    expect(rows(unknown)).toEqual({
      known: false,
      fields: { amount: '10.00', items: '[1,2]', note: '' },
    });
    const unreadable = {
      ...ITEMS_BY_TYPE['staff.qualification']!,
      subject: { tutor_display_name: 'Carmen Ruiz' },
    };
    expect(rows(unreadable)).toEqual({
      known: false,
      fields: { tutor_display_name: 'Carmen Ruiz' },
    });
  });

  it('labels the fields in Spanish too', () => {
    const details = approvalDetails(
      ITEMS_BY_TYPE['scheduling.change_request']!,
      i18n.getFixedT('es'),
      formatters,
    );
    expect(details.rows.map((row) => row.label)).toContain('Iniciada por');
    expect(details.rows.map((row) => row.value)).toContain('La familia');
  });
});
