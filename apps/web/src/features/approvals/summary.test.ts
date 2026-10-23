import { describe, expect, it } from 'vitest';
import { i18n } from '@/i18n';
import { formatInstant } from '@/lib/time';
import type { ApprovalRequestItem } from './api';
import {
  approvalSummary,
  itemName,
  occurrenceCodeName,
  occurrenceConflictSubject,
  sessionRequestSubject,
  typeLabel,
} from './summary';

// The scheduling.occurrence_conflict summary (#53 G5): the occurrence's date
// and time in the center zone, the students, the subject and the codes.

const OCCURRENCE: ApprovalRequestItem = {
  id: '00000000-0000-4000-8000-0000000000d1',
  request_type: 'scheduling.occurrence_conflict',
  status: 'pending',
  priority: 'normal',
  requester: null,
  assignee: null,
  created_at: '2031-01-12T14:00:00Z',
  resolved_at: null,
  resolved_by: null,
  reason: null,
  subject: {
    class_session_id: '00000000-0000-4000-8000-0000000000d2',
    series_id: '00000000-0000-4000-8000-0000000000d3',
    // 16:00 in Mexico City (UTC-6); 15:00 in Tijuana (PDT, UTC-7).
    starts_at: '2031-04-07T22:00:00Z',
    subject_name: 'Matemáticas',
    student_display_names: ['Ana López'],
    tutor_display_name: 'Luis Pérez',
    conflict_codes: ['tutor_overlap', 'tutor_inactive'],
  },
  allowed_actions: ['approve', 'reject'],
  version: 1,
};

const en = i18n.getFixedT('en', 'common');
const es = i18n.getFixedT('es', 'common');

describe('the series occurrence conflict summary', () => {
  it('reads the subject only for its own type', () => {
    expect(occurrenceConflictSubject(OCCURRENCE)?.subject_name).toBe(
      'Matemáticas',
    );
    expect(
      occurrenceConflictSubject({ ...OCCURRENCE, request_type: 'other.type' }),
    ).toBeNull();
    expect(
      occurrenceConflictSubject({ ...OCCURRENCE, subject: { x: 1 } }),
    ).toBeNull();
  });

  it('shows the time in the center zone, the student, subject and codes', () => {
    expect(
      approvalSummary(OCCURRENCE, en, (instant) =>
        formatInstant(instant, {
          timeZone: 'America/Mexico_City',
          language: 'en',
        }),
      ),
    ).toBe(
      'Series occurrence Apr 7, 2031, 4:00 PM — Ana López · Matemáticas: Tutor overlap, Inactive tutor',
    );
    expect(
      approvalSummary(OCCURRENCE, es, (instant) =>
        formatInstant(instant, { timeZone: 'America/Tijuana', language: 'es' }),
      ),
    ).toBe(
      'Sesión de serie 7 abr 2031, 15:00 — Ana López · Matemáticas: Traslape del tutor, Tutor inactivo',
    );
  });

  it('names its buttons with the summary and labels its type', () => {
    const format = (instant: string) =>
      formatInstant(instant, {
        timeZone: 'America/Mexico_City',
        language: 'en',
      });

    expect(itemName(OCCURRENCE, en, format)).toBe(
      approvalSummary(OCCURRENCE, en, format),
    );
    expect(typeLabel('scheduling.occurrence_conflict', en)).toBe(
      'Series session conflict',
    );
    expect(typeLabel('scheduling.occurrence_conflict', es)).toBe(
      'Conflicto en sesión de serie',
    );
  });

  it('names every code it knows and shows an unknown one as it is', () => {
    expect(occurrenceCodeName('notice_window', en)).toBe('Booking notice');
    expect(occurrenceCodeName('classroom_inactive', es)).toBe('Aula inactiva');
    expect(occurrenceCodeName('crosses_local_date', en)).toBe(
      'Crosses midnight',
    );
    expect(occurrenceCodeName('something_new', en)).toBe('something_new');
  });
});

// The scheduling.session_request summary (#59 E1, E3): the student, the
// subject and level, the time in the center zone, the length and who asked.
describe('the session request summary', () => {
  const REQUEST: ApprovalRequestItem = {
    ...OCCURRENCE,
    id: '00000000-0000-4000-8000-0000000000e1',
    request_type: 'scheduling.session_request',
    subject: {
      student_name: 'Ana López',
      subject_name: 'Física',
      education_level_name: 'Secundaria',
      starts_at: '2031-01-06T16:00:00Z',
      ends_at: '2031-01-06T17:00:00Z',
      duration_minutes: 60,
      requested_by: 'Laura López',
      note: null,
    },
  };

  it('reads the subject only for its own type and labels the type', () => {
    expect(sessionRequestSubject(REQUEST)?.student_name).toBe('Ana López');
    expect(sessionRequestSubject(OCCURRENCE)).toBeNull();
    expect(typeLabel('scheduling.session_request', en)).toBe('Session request');
    expect(typeLabel('scheduling.session_request', es)).toBe(
      'Solicitud de sesión',
    );
  });

  it('shows the time in the center zone', () => {
    expect(
      approvalSummary(REQUEST, en, (instant) =>
        formatInstant(instant, {
          timeZone: 'America/Mexico_City',
          language: 'en',
        }),
      ),
    ).toBe(
      'Ana López · Física (Secundaria) · Jan 6, 2031, 10:00 AM · 60 min · requested by Laura López',
    );
  });
});
