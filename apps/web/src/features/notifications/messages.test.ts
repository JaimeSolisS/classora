import { describe, expect, it } from 'vitest';
import { i18n } from '@/i18n';
import { formatInstant } from '@/lib/time';
import { NOTIFICATION_LINKS, notificationMessage } from './messages';

// #232 C4: an occurrence skipped for a closure, with its date and time in
// the center zone (22:00Z is 16:00 in Mexico City, 14:00 in Tijuana in
// January).

const params = {
  class_session_id: '00000000-0000-4000-8000-000000000232',
  series_id: '00000000-0000-4000-8000-000000000233',
  starts_at: '2031-01-08T22:00:00Z',
};

function message(lng: 'es' | 'en', timeZone: string): string {
  return notificationMessage(
    i18n.getFixedT(lng),
    i18n,
    { notification_type: 'scheduling.occurrence_skipped_closure', params },
    (instant) =>
      formatInstant(instant, {
        timeZone,
        language: lng,
        style: 'dateTime',
      }),
  );
}

describe('the skipped-for-a-closure notification (#232 C4)', () => {
  it('names the date and time in the center zone, in both languages', () => {
    expect(message('es', 'America/Mexico_City')).toBe(
      'La clase del 8 ene 2031, 16:00 no se imparte porque el centro está cerrado.',
    );
    expect(message('en', 'America/Mexico_City')).toBe(
      'A class on Jan 8, 2031, 4:00 PM is not held because the center is closed.',
    );
    expect(message('es', 'America/Tijuana')).toContain('8 ene 2031, 14:00');
  });

  it('never shows the ids and only becomes read when activated', () => {
    expect(message('en', 'America/Mexico_City')).not.toContain(
      params.class_session_id,
    );
    expect(
      NOTIFICATION_LINKS['scheduling.occurrence_skipped_closure'],
    ).toBeUndefined();
  });
});

// #54 D3: the edit notifications. Instants in the center zone, local dates
// as dates; only ids, instants, dates and counts in the params.

function edited(
  lng: 'es' | 'en',
  notification_type: string,
  itemParams: Record<string, string | number | boolean | null>,
  timeZone = 'America/Mexico_City',
): string {
  return notificationMessage(
    i18n.getFixedT(lng),
    i18n,
    { notification_type, params: itemParams },
    (instant) =>
      formatInstant(instant, { timeZone, language: lng, style: 'dateTime' }),
  );
}

describe('the series and session edit notifications (#54 D3)', () => {
  const series_id = '00000000-0000-4000-8000-000000000054';

  it('names the new time of a moved session in the center zone', () => {
    const moved = {
      class_session_id: '00000000-0000-4000-8000-000000000055',
      starts_at: '2031-01-08T23:00:00Z',
    };
    expect(edited('en', 'scheduling.session_rescheduled', moved)).toBe(
      'A class was moved to Jan 8, 2031, 5:00 PM.',
    );
    expect(edited('es', 'scheduling.session_rescheduled', moved)).toBe(
      'Una clase se cambió al 8 ene 2031, 17:00.',
    );
    expect(
      edited('es', 'scheduling.session_rescheduled', moved, 'America/Tijuana'),
    ).toContain('8 ene 2031, 15:00');
  });

  // Local dates follow the app's language (i18n.resolvedLanguage), as in
  // the bell, where t() and i18n agree.
  async function inLanguage(lng: 'es' | 'en', run: () => void): Promise<void> {
    const previous = i18n.language;
    await i18n.changeLanguage(lng);
    try {
      run();
    } finally {
      await i18n.changeLanguage(previous);
    }
  }

  it('gives the date and the counts of a series change', async () => {
    const changed = {
      series_id,
      from_date: '2031-01-20',
      changed: 10,
      cancelled: 2,
    };
    await inLanguage('en', () => {
      expect(edited('en', 'scheduling.series_changed', changed)).toBe(
        'A weekly series changes from Jan 20, 2031 (classes moved: 10, cancelled: 2).',
      );
    });
    await inLanguage('es', () => {
      expect(edited('es', 'scheduling.series_changed', changed)).toBe(
        'Una serie semanal cambia desde el 20 ene 2031 (clases cambiadas: 10, canceladas: 2).',
      );
    });
  });

  it('gives the date of a series cancellation, never the ids', async () => {
    const cancelled = { series_id, from_date: '2031-01-20' };
    await inLanguage('en', () => {
      const en = edited('en', 'scheduling.series_cancelled', cancelled);
      expect(en).toBe('A weekly series is cancelled from Jan 20, 2031.');
      expect(en).not.toContain(series_id);
    });
    await inLanguage('es', () => {
      expect(edited('es', 'scheduling.series_cancelled', cancelled)).toBe(
        'Una serie semanal se cancela desde el 20 ene 2031.',
      );
    });
  });

  it('only become read when activated', () => {
    for (const type of [
      'scheduling.session_rescheduled',
      'scheduling.series_changed',
      'scheduling.series_cancelled',
    ]) {
      expect(NOTIFICATION_LINKS[type]).toBeUndefined();
    }
  });
});

// #55 F4: the substitution and cancellation notifications, with the start in
// the center zone (a Tijuana DST date: 2031-03-10 is the Monday after the
// switch, so 17:00Z is 10:00 there and 11:00 in Mexico City), and the
// substitution variants of the offer types (never a reason).

describe('substitutions and cancellations (#55 F4)', () => {
  const session = {
    class_session_id: '00000000-0000-4000-8000-000000000055',
    starts_at: '2031-03-10T17:00:00Z',
  };

  it('names the start in the center zone, in both languages', () => {
    expect(edited('en', 'scheduling.session_cancelled', session)).toBe(
      'A class on Mar 10, 2031, 11:00 AM is cancelled.',
    );
    expect(
      edited('es', 'scheduling.session_cancelled', session, 'America/Tijuana'),
    ).toBe('Una clase del 10 mar 2031, 10:00 se canceló.');
    expect(
      edited('en', 'scheduling.tutor_substituted', session, 'America/Tijuana'),
    ).toBe(
      'Your class on Mar 10, 2031, 10:00 AM is now taught by a substitute.',
    );
    expect(edited('es', 'scheduling.session_tutor_changed', session)).toBe(
      'Una clase del 10 mar 2031, 11:00 tiene un nuevo tutor.',
    );
    for (const type of [
      'scheduling.session_cancelled',
      'scheduling.tutor_substituted',
      'scheduling.session_tutor_changed',
    ]) {
      expect(edited('en', type, session)).not.toContain(
        session.class_session_id,
      );
      // #67 F3: they open My schedule (mySchedule.test.tsx).
      expect(
        NOTIFICATION_LINKS[type]?.(session, { userId: 'u', timeZone: 'UTC' }),
      ).toBe('/my-schedule?view=list&date=2031-03-10');
    }
  });

  it('uses the substitution variant of the offer types', () => {
    const offer = {
      offer_id: 'o-1',
      subject_name: 'Álgebra',
      starts_at: session.starts_at,
      respond_by: '2031-03-08T17:00:00Z',
      kind: 'substitution',
      original_tutor_name: 'Carmen Ruiz',
    };
    expect(edited('en', 'scheduling.offer_received', offer)).toBe(
      'Substitute for Carmen Ruiz: Álgebra, Mar 10, 2031, 11:00 AM. Answer by Mar 8, 2031, 11:00 AM.',
    );
    const closed = {
      offer_id: 'o-1',
      class_session_id: session.class_session_id,
      subject_name: 'Álgebra',
      starts_at: session.starts_at,
      tutor_name: 'Diego Torres',
      kind: 'substitution',
      original_tutor_name: 'Carmen Ruiz',
    };
    expect(edited('es', 'scheduling.offer_declined', closed)).toBe(
      'Diego Torres rechazó suplir a Carmen Ruiz: Álgebra, 10 mar 2031, 11:00. Carmen Ruiz sigue impartiéndola.',
    );
    expect(edited('en', 'scheduling.offer_expired', closed)).toContain(
      'Carmen Ruiz still teaches it.',
    );
    // An assignment offer keeps its message.
    expect(
      edited('en', 'scheduling.offer_received', {
        ...offer,
        kind: 'assignment',
      }),
    ).toContain('You have a new session offer');
  });
});

// #63 F1-F3: the availability decisions go to the tutor's own availability;
// the reassignment notice to the calendar filtered by the tutor on the
// first session's local date (22:00Z on Jan 6 is Jan 6 in Mexico City).
describe('availability change notifications (#63)', () => {
  const me = { userId: '00000000-0000-4000-8000-0000000000b1' };
  const tutor = '00000000-0000-4000-8000-0000000000b2';

  it('link to the availability and the calendar', () => {
    for (const type of [
      'staff.availability_change_approved',
      'staff.availability_change_rejected',
    ]) {
      expect(NOTIFICATION_LINKS[type]?.({}, me)).toBe(
        `/tutors/${me.userId}#availability`,
      );
    }
    const params = {
      tutor_user_id: tutor,
      tutor_name: 'Carmen Ruiz',
      session_count: 3,
      first_starts_at: '2031-01-07T03:00:00Z',
    };
    expect(
      NOTIFICATION_LINKS['scheduling.reassignment_needed']?.(params, {
        ...me,
        timeZone: 'America/Mexico_City',
      }),
    ).toBe(`/schedule?tutor=${tutor}&date=2031-01-06`);
    expect(
      NOTIFICATION_LINKS['scheduling.reassignment_needed']?.(params, me),
    ).toBe(`/schedule?tutor=${tutor}`);
  });

  it('never name the rejection reason; show the first start in the zone', () => {
    expect(
      notificationMessage(i18n.getFixedT('en'), i18n, {
        notification_type: 'staff.availability_change_rejected',
        params: { change_request_id: 'x', kind: 'weekly', affected_count: 2 },
      }),
    ).toBe(
      'Your availability change was rejected. See the reason in your availability.',
    );
    expect(
      notificationMessage(
        i18n.getFixedT('es'),
        i18n,
        {
          notification_type: 'scheduling.reassignment_needed',
          params: {
            tutor_user_id: tutor,
            tutor_name: 'Carmen Ruiz',
            session_count: 3,
            first_starts_at: '2031-01-06T22:00:00Z',
          },
        },
        (instant) =>
          formatInstant(instant, {
            timeZone: 'America/Mexico_City',
            language: 'es',
            style: 'dateTime',
          }),
      ),
    ).toBe(
      'La disponibilidad de Carmen Ruiz cambió: 3 clases necesitan otro tutor; la primera, el 6 ene 2031, 16:00.',
    );
  });
});

// #78 F1: the tutor's closeout decisions (no reason in the params) open the
// closeout.

describe('closeout review notifications (#78 F1)', () => {
  const closeout = {
    class_session_id: '00000000-0000-4000-8000-000000000078',
    starts_at: '2031-01-08T22:00:00Z',
    subject_name: 'Matemáticas',
  };
  const when = (lng: 'es' | 'en') => (instant: string) =>
    formatInstant(instant, {
      timeZone: 'America/Mexico_City',
      language: lng,
      style: 'dateTime',
    });
  const text = (
    lng: 'es' | 'en',
    type: string,
    extra: Record<string, number | null> = {},
  ) =>
    notificationMessage(
      i18n.getFixedT(lng),
      i18n,
      { notification_type: type, params: { ...closeout, ...extra } },
      when(lng),
    );

  it('renders the three types in both languages', () => {
    expect(
      text('en', 'academics.closeout_approved', { approved_minutes: 50 }),
    ).toBe(
      'Your closeout of the Matemáticas class on Jan 8, 2031, 4:00 PM was approved: 50 min.',
    );
    expect(
      text('es', 'academics.closeout_approved', { approved_minutes: 50 }),
    ).toBe(
      'Se aprobó tu cierre de la clase de Matemáticas del 8 ene 2031, 16:00: 50 min.',
    );
    expect(text('en', 'academics.closeout_changes_requested')).toBe(
      'Changes were requested to your closeout of the Matemáticas class on Jan 8, 2031, 4:00 PM.',
    );
    expect(
      text('es', 'academics.closeout_corrected', { approved_minutes: null }),
    ).toBe(
      'Un revisor corrigió tu cierre de la clase de Matemáticas del 8 ene 2031, 16:00.',
    );
  });

  it('renders the on-behalf submission in both languages (#301b F1)', () => {
    expect(text('en', 'academics.closeout_submitted_on_behalf')).toBe(
      'Your closeout of the Matemáticas class on Jan 8, 2031, 4:00 PM was submitted on your behalf.',
    );
    expect(text('es', 'academics.closeout_submitted_on_behalf')).toBe(
      'Se envió en tu nombre el cierre de la clase de Matemáticas del 8 ene 2031, 16:00.',
    );
  });

  it('links each type to the closeout', () => {
    const recipient = { userId: '00000000-0000-4000-8000-0000000000aa' };
    for (const type of [
      'academics.closeout_approved',
      'academics.closeout_corrected',
      'academics.closeout_changes_requested',
      // #301b F1.
      'academics.closeout_submitted_on_behalf',
    ]) {
      expect(NOTIFICATION_LINKS[type]?.(closeout, recipient)).toBe(
        `/my-schedule/sessions/${closeout.class_session_id}/closeout`,
      );
      expect(NOTIFICATION_LINKS[type]?.({}, recipient)).toBe('/my-schedule');
    }
  });
});

// #80 H1: the evaluation notifications name the evaluation's local date,
// never a score, a reason or an id; #308 F2 links them.

describe('evaluation notifications (#80 H1)', () => {
  const finalized = {
    student_id: '00000000-0000-4000-8000-000000000080',
    evaluation_id: '00000000-0000-4000-8000-000000000081',
    subject_id: '00000000-0000-4000-8000-000000000082',
    evaluated_on: '2031-03-04',
  };

  async function inLanguage(lng: 'es' | 'en', run: () => void): Promise<void> {
    const previous = i18n.language;
    await i18n.changeLanguage(lng);
    try {
      run();
    } finally {
      await i18n.changeLanguage(previous);
    }
  }

  const text = (lng: 'es' | 'en', type: string, params: object) =>
    notificationMessage(i18n.getFixedT(lng), i18n, {
      notification_type: type,
      params: params as Record<string, string>,
    });

  it('renders both types in both languages', async () => {
    await inLanguage('en', () => {
      const en = text('en', 'academics.evaluation_finalized', finalized);
      expect(en).toBe(
        'A tutor finalized an evaluation from Mar 4, 2031: it is in the academic history.',
      );
      expect(en).not.toContain(finalized.evaluation_id);
      expect(
        text('en', 'academics.evaluation_voided', {
          student_id: finalized.student_id,
          evaluation_id: finalized.evaluation_id,
        }),
      ).toBe('A reviewer voided one of your evaluations.');
    });
    await inLanguage('es', () => {
      expect(text('es', 'academics.evaluation_finalized', finalized)).toBe(
        'Un tutor finalizó una evaluación del 4 mar 2031: ya está en el historial académico.',
      );
      expect(text('es', 'academics.evaluation_voided', {})).toBe(
        'Un revisor anuló una de tus evaluaciones.',
      );
    });
  });

  // #308 F2: the finalized one opens the family's history of the student,
  // the voided one the evaluation; without an id, the fallbacks.
  it('link to the history and the evaluation (#308 F2)', () => {
    const recipient = { userId: 'u-1' };
    const finalizedLink = NOTIFICATION_LINKS['academics.evaluation_finalized']!;
    const voidedLink = NOTIFICATION_LINKS['academics.evaluation_voided']!;
    expect(finalizedLink(finalized, recipient)).toBe(
      `/my-history?student=${finalized.student_id}`,
    );
    expect(finalizedLink({}, recipient)).toBe('/my-history');
    expect(
      voidedLink(
        {
          student_id: finalized.student_id,
          evaluation_id: finalized.evaluation_id,
        },
        recipient,
      ),
    ).toBe(`/evaluations/${finalized.evaluation_id}`);
    expect(voidedLink({}, recipient)).toBe('/');
  });
});

// #81 H1: the learning plan notifications carry ids only (no title or
// text); #310 F1 links them to the family's page of the plan.

describe('learning plan notifications (#81 H1)', () => {
  const plan = {
    student_id: '00000000-0000-4000-8000-000000000081',
    plan_id: '00000000-0000-4000-8000-000000000082',
    subject_id: '00000000-0000-4000-8000-000000000083',
  };

  const text = (lng: 'es' | 'en', type: string) =>
    notificationMessage(i18n.getFixedT(lng), i18n, {
      notification_type: type,
      params: plan,
    });

  it('renders both types in both languages, without an id', () => {
    expect(text('en', 'academics.learning_plan_activated')).toBe(
      'A new learning plan is ready: see its objectives and milestones.',
    );
    expect(text('en', 'academics.learning_plan_completed')).toBe(
      'A learning plan was completed.',
    );
    expect(text('es', 'academics.learning_plan_activated')).toBe(
      'Hay un nuevo plan de aprendizaje: consulta sus objetivos y sus hitos.',
    );
    expect(text('es', 'academics.learning_plan_completed')).toBe(
      'Se completó un plan de aprendizaje.',
    );
    for (const lng of ['es', 'en'] as const) {
      for (const type of [
        'academics.learning_plan_activated',
        'academics.learning_plan_completed',
      ]) {
        expect(text(lng, type)).not.toContain(plan.plan_id);
        expect(text(lng, type)).not.toContain('notifications.types');
      }
    }
  });

  // #310 F1: both open the family's page of the plan; without a plan id
  // the student's plans, without either /my-learning-plans. Never the
  // staff route /learning-plans/:id.
  it.each([
    'academics.learning_plan_activated',
    'academics.learning_plan_completed',
  ])('%s links to the family page of the plan (#310 F1)', (type) => {
    const recipient = { userId: 'u-1' };
    const link = NOTIFICATION_LINKS[type]!;
    expect(link).toBeTypeOf('function');
    expect(link(plan, recipient)).toBe(`/my-learning-plans/${plan.plan_id}`);
    expect(link({ student_id: plan.student_id }, recipient)).toBe(
      `/my-learning-plans?student=${plan.student_id}`,
    );
    expect(link({}, recipient)).toBe('/my-learning-plans');
    expect(link({ plan_id: '', student_id: 7 }, recipient)).toBe(
      '/my-learning-plans',
    );
  });
});

// #82 I1: the homework notifications carry ids (and the deadline's date)
// only, never a title, text, score or feedback, and have no link until #313.

describe('homework notifications (#82 I1)', () => {
  const homework = {
    student_id: '00000000-0000-4000-8000-000000000082',
    assignment_id: '00000000-0000-4000-8000-000000000083',
    subject_id: '00000000-0000-4000-8000-000000000084',
    due_on: '2031-03-12',
  };
  const types = [
    'academics.assignment_set',
    'academics.assignment_submitted',
    'academics.assignment_graded',
  ];

  async function inLanguage(lng: 'es' | 'en', run: () => void): Promise<void> {
    const previous = i18n.language;
    await i18n.changeLanguage(lng);
    try {
      run();
    } finally {
      await i18n.changeLanguage(previous);
    }
  }

  const text = (lng: 'es' | 'en', type: string) =>
    notificationMessage(i18n.getFixedT(lng), i18n, {
      notification_type: type,
      params: homework,
    });

  it('renders the three types in both languages, without an id', async () => {
    await inLanguage('en', () => {
      expect(text('en', 'academics.assignment_set')).toBe(
        'New homework is due on Mar 12, 2031.',
      );
      expect(text('en', 'academics.assignment_submitted')).toBe(
        'A student submitted homework.',
      );
      expect(text('en', 'academics.assignment_graded')).toBe(
        'Homework was graded.',
      );
    });
    await inLanguage('es', () => {
      expect(text('es', 'academics.assignment_set')).toBe(
        'Hay una tarea nueva para el 12 mar 2031.',
      );
      expect(text('es', 'academics.assignment_submitted')).toBe(
        'Un alumno entregó una tarea.',
      );
      expect(text('es', 'academics.assignment_graded')).toBe(
        'Se calificó una tarea.',
      );
    });
    for (const lng of ['es', 'en'] as const) {
      for (const type of types) {
        expect(text(lng, type)).not.toContain(homework.assignment_id);
        expect(text(lng, type)).not.toContain('notifications.types');
      }
    }
  });

  it('only become read when activated (no page until #313)', () => {
    for (const type of types) {
      expect(NOTIFICATION_LINKS[type]).toBeUndefined();
    }
  });
});
