import { describe, expect, it } from 'vitest';
import { TIJUANA, ZONE } from '../fixtures';
import {
  clickPrefill,
  isSelectionAllowed,
  isSingleSnap,
  newSessionPath,
  selectionPrefill,
} from './rules';

// HF-02 F1: the pure rules of scheduling from the calendar. FullCalendar
// gives real instants; everything here is read in the center zone, under a
// host zone that is neither the center's nor Tijuana's.

/** An instant from a UTC ISO string. */
const at = (iso: string) => new Date(iso);

// Tuesday 2026-11-10 in America/Mexico_City (UTC-6, no DST).
const TEN = at('2026-11-10T16:00:00Z'); // 10:00 local
const NOW = Date.parse('2026-11-10T15:00:00Z'); // 09:00 local

describe('the host zone', () => {
  it('is not the center zone', () => {
    const host = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(host).not.toBe(ZONE);
    expect(host).not.toBe(TIJUANA);
  });
});

describe('a dragged range (B1, B6)', () => {
  it('pre-fills the center-zone date and times', () => {
    expect(
      selectionPrefill({ start: TEN, end: at('2026-11-10T17:30:00Z') }, ZONE),
    ).toEqual({ date: '2026-11-10', start: '10:00', end: '11:30' });
  });

  it('reads another center zone the same way (Tijuana, UTC-8)', () => {
    expect(
      selectionPrefill(
        { start: TEN, end: at('2026-11-10T17:30:00Z') },
        TIJUANA,
      ),
    ).toEqual({ date: '2026-11-10', start: '08:00', end: '09:30' });
  });

  it('keeps the center date when the UTC date is the next day', () => {
    // 21:00-22:00 in Mexico City is 03:00-04:00Z the next day.
    expect(
      selectionPrefill(
        { start: at('2026-11-11T03:00:00Z'), end: at('2026-11-11T04:00:00Z') },
        ZONE,
      ),
    ).toEqual({ date: '2026-11-10', start: '21:00', end: '22:00' });
  });

  it('treats one 15-minute snap as a click, two as a range', () => {
    expect(isSingleSnap({ start: TEN, end: at('2026-11-10T16:15:00Z') })).toBe(
      true,
    );
    expect(isSingleSnap({ start: TEN, end: at('2026-11-10T16:30:00Z') })).toBe(
      false,
    );
  });
});

describe('a click (B2)', () => {
  it('lasts one hour from the 15-minute mark under the pointer', () => {
    expect(clickPrefill(at('2026-11-10T20:15:00Z'), ZONE)).toEqual({
      date: '2026-11-10',
      start: '14:15',
      end: '15:15',
    });
  });

  it('caps the end at 23:45', () => {
    expect(clickPrefill(at('2026-11-11T05:00:00Z'), ZONE)).toEqual({
      date: '2026-11-10',
      start: '23:00',
      end: '23:45',
    });
    expect(clickPrefill(at('2026-11-11T05:30:00Z'), ZONE).end).toBe('23:45');
  });

  it('fills the date and start only from 23:45 on', () => {
    expect(clickPrefill(at('2026-11-11T05:45:00Z'), ZONE)).toEqual({
      date: '2026-11-10',
      start: '23:45',
      end: null,
    });
  });

  it('reads the center zone, not the host zone', () => {
    expect(clickPrefill(TEN, TIJUANA)).toEqual({
      date: '2026-11-10',
      start: '08:00',
      end: '09:00',
    });
  });
});

describe('selectAllow (B3)', () => {
  const allowed = (startIso: string, endIso: string, now = NOW) =>
    isSelectionAllowed({ start: at(startIso), end: at(endIso) }, now, ZONE);

  it('accepts a future range on one local day', () => {
    expect(allowed('2026-11-10T16:00:00Z', '2026-11-10T17:30:00Z')).toBe(true);
  });

  it('accepts a range starting exactly now', () => {
    expect(
      allowed('2026-11-10T16:00:00Z', '2026-11-10T17:00:00Z', TEN.getTime()),
    ).toBe(true);
  });

  it('refuses a past range', () => {
    expect(allowed('2026-11-10T13:00:00Z', '2026-11-10T14:00:00Z')).toBe(false);
  });

  it('refuses a partly past range', () => {
    expect(allowed('2026-11-10T14:45:00Z', '2026-11-10T16:00:00Z')).toBe(false);
  });

  it('refuses a range across two local days (Week columns)', () => {
    expect(allowed('2026-11-10T16:00:00Z', '2026-11-11T17:00:00Z')).toBe(false);
  });

  it('refuses a range ending at 24:00', () => {
    // 23:00 to 24:00 in Mexico City.
    expect(allowed('2026-11-11T05:00:00Z', '2026-11-11T06:00:00Z')).toBe(false);
    expect(allowed('2026-11-11T05:00:00Z', '2026-11-11T05:45:00Z')).toBe(true);
  });

  it('accepts exactly 480 minutes and refuses more', () => {
    expect(allowed('2026-11-10T16:00:00Z', '2026-11-11T00:00:00Z')).toBe(true);
    expect(allowed('2026-11-10T16:00:00Z', '2026-11-11T00:15:00Z')).toBe(false);
  });

  it('judges the day in the center zone', () => {
    // 17:00-19:00 in Tijuana is 01:00-03:00Z the next UTC day: one local day.
    expect(
      isSelectionAllowed(
        { start: at('2026-11-11T01:00:00Z'), end: at('2026-11-11T03:00:00Z') },
        NOW,
        TIJUANA,
      ),
    ).toBe(true);
  });
});

describe('the "Schedule a session" URL (E1, C2)', () => {
  const PREFILL = {
    date: '2026-11-10',
    start: '10:00',
    end: '11:30',
  } as Parameters<typeof newSessionPath>[0];
  const TUTOR = '00000000-0000-4000-8000-000000057b01';
  const ROOM = '00000000-0000-4000-8000-000000057d01';

  it('carries the date and times without filters', () => {
    expect(newSessionPath(PREFILL, { tutor: null, room: null })).toBe(
      '/schedule/new?date=2026-11-10&start=10:00&end=11:30',
    );
  });

  it('adds the tutor and room filters', () => {
    expect(newSessionPath(PREFILL, { tutor: TUTOR, room: ROOM })).toBe(
      `/schedule/new?date=2026-11-10&start=10:00&end=11:30&tutor=${TUTOR}&room=${ROOM}`,
    );
    expect(newSessionPath(PREFILL, { tutor: null, room: ROOM })).toBe(
      `/schedule/new?date=2026-11-10&start=10:00&end=11:30&room=${ROOM}`,
    );
  });

  it('leaves the end out after a click at 23:45', () => {
    expect(
      newSessionPath(
        { ...PREFILL, start: '23:45', end: null },
        { tutor: TUTOR, room: null },
      ),
    ).toBe(`/schedule/new?date=2026-11-10&start=23:45&tutor=${TUTOR}`);
  });
});
