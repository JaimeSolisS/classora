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
      expect(NOTIFICATION_LINKS[type]).toBeUndefined();
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
