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
