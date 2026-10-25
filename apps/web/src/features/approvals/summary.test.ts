import { describe, expect, it } from 'vitest';
import { i18n } from '@/i18n';
import { formatInstant } from '@/lib/time';
import type { ApprovalRequestItem } from './api';
import {
  AVAILABILITY_CHANGE,
  SESSION_CLOSEOUT,
  SESSION_CLOSEOUT_CORRECTION,
  SESSION_CLOSEOUT_ON_BEHALF,
} from './fixtures';
import {
  approvalSummary,
  availabilityChangeLink,
  availabilityChangeSubject,
  itemName,
  occurrenceCodeName,
  occurrenceConflictSubject,
  sessionCloseoutLink,
  sessionCloseoutReviewLine,
  sessionCloseoutSubject,
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

// #63 H2: an availability change names the tutor, the change and how many
// classes it affects, and links to the tutor's availability.
describe('staff.availability_change', () => {
  it('summarizes the change', () => {
    expect(
      approvalSummary(AVAILABILITY_CHANGE, en, undefined, (date) => date),
    ).toBe(
      'Carmen Ruiz · Time off: 2031-11-12 – 2031-11-16 · 3 classes affected',
    );
    expect(
      approvalSummary(AVAILABILITY_CHANGE, es, undefined, (date) => date),
    ).toBe(
      'Carmen Ruiz · Tiempo libre: 2031-11-12 – 2031-11-16 · 3 clases afectadas',
    );
    expect(typeLabel('staff.availability_change', en)).toBe(
      'Availability change',
    );
  });

  it('links to the tutor availability', () => {
    expect(availabilityChangeLink(AVAILABILITY_CHANGE)).toBe(
      '/tutors/00000000-0000-4000-8000-0000000000b1#availability',
    );
    expect(
      availabilityChangeSubject({ ...AVAILABILITY_CHANGE, subject: {} }),
    ).toBeNull();
  });
});

describe('academics.session_closeout (#77 G4)', () => {
  it('names what the item asks (#78 G2)', () => {
    const t = i18n.getFixedT('en', 'common');
    const es = i18n.getFixedT('es', 'common');
    expect(sessionCloseoutReviewLine(SESSION_CLOSEOUT, t)).toBe('New');
    expect(sessionCloseoutReviewLine(SESSION_CLOSEOUT_CORRECTION, t)).toBe(
      'Correction · Approved now: 55 min',
    );
    expect(
      sessionCloseoutReviewLine(
        {
          ...SESSION_CLOSEOUT,
          subject: {
            ...(SESSION_CLOSEOUT.subject as Record<string, unknown>),
            kind_of_review: 'resubmission',
          },
        },
        es,
      ),
    ).toBe('Reenviado');
    expect(sessionCloseoutReviewLine(AVAILABILITY_CHANGE, t)).toBe('');
  });

  it('adds "On behalf" as text (#301b E4)', () => {
    const t = i18n.getFixedT('en', 'common');
    const es = i18n.getFixedT('es', 'common');
    expect(sessionCloseoutReviewLine(SESSION_CLOSEOUT_ON_BEHALF, t)).toBe(
      'New · On behalf',
    );
    expect(sessionCloseoutReviewLine(SESSION_CLOSEOUT_ON_BEHALF, es)).toBe(
      'Nuevo · En nombre del tutor',
    );
    expect(
      sessionCloseoutReviewLine(
        {
          ...SESSION_CLOSEOUT,
          subject: {
            ...(SESSION_CLOSEOUT.subject as Record<string, unknown>),
            on_behalf_by_display_name: null,
          },
        },
        t,
      ),
    ).toBe('New');
  });

  it('summarizes the session, the tutor and the minutes', () => {
    const t = i18n.getFixedT('en', 'common');
    const when = (instant: string) =>
      formatInstant(instant, {
        timeZone: 'America/Mexico_City',
        language: 'en',
        style: 'dateTime',
      });

    expect(approvalSummary(SESSION_CLOSEOUT, t, when)).toBe(
      'Matemáticas · Apr 7, 2031, 10:00 AM · Carmen Ruiz · 55 of 60 min · 3 students',
    );
    expect(typeLabel('academics.session_closeout', t)).toBe('Session closeout');
    expect(
      typeLabel('academics.session_closeout', i18n.getFixedT('es', 'common')),
    ).toBe('Cierre de clase');
  });

  it('reads the subject only for its own type and links to the session', () => {
    expect(sessionCloseoutSubject(SESSION_CLOSEOUT)?.participant_count).toBe(3);
    expect(sessionCloseoutLink(SESSION_CLOSEOUT)).toBe(
      '/schedule/sessions/00000000-0000-4000-8000-0000000000f2#closeout',
    );
    expect(sessionCloseoutLink(AVAILABILITY_CHANGE)).toBeNull();
    expect(
      sessionCloseoutSubject({
        ...AVAILABILITY_CHANGE,
        subject: SESSION_CLOSEOUT.subject,
      }),
    ).toBeNull();
  });
});
