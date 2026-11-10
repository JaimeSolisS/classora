import { describe, expect, it } from 'vitest';
import {
  addMinutes,
  formatTime,
  minutesOfDay,
  parseTime,
  timeMatches,
  timeOptions,
} from './timeOfDay';

// HF-13 (#598 E1 a, AC4, AC5): reading a typed time, showing one in the app
// language and the quarter-hour options.

describe('parseTime', () => {
  it.each([
    ['9', '09:00'],
    ['930', '09:30'],
    ['9:30', '09:30'],
    ['09.30', '09:30'],
    ['9:30 pm', '21:30'],
    ['21:30', '21:30'],
    ['9 p.m.', '21:00'],
    ['0930', '09:30'],
    ['  10:10 ', '10:10'],
    ['9:', '09:00'],
    ['9:5', '09:05'],
    ['12 am', '00:00'],
    ['12:15 PM', '12:15'],
    ['9a', '09:00'],
    ['9 A.M.', '09:00'],
    ['9:30 p. m.', '21:30'],
    ['9:30 PM', '21:30'],
    ['0', '00:00'],
    ['23:59', '23:59'],
  ])('reads %j as %s', (text, expected) => {
    expect(parseTime(text)).toBe(expected);
  });

  it.each([
    '',
    '   ',
    '25:00',
    '9:75',
    'abc',
    '24:00',
    '93',
    '13 pm',
    '0 am',
    '21:30 pm',
    '9:300',
    '12345',
    'pm',
    '-1',
  ])('reads %j as nothing', (text) => {
    expect(parseTime(text)).toBeNull();
  });
});

describe('formatTime', () => {
  it('shows 24-hour HH:MM in es and 12-hour in en', () => {
    expect(formatTime('09:30', 'es')).toBe('09:30');
    expect(formatTime('21:05', 'es')).toBe('21:05');
    expect(formatTime('09:30', 'en')).toBe('9:30 AM');
    expect(formatTime('00:15', 'en')).toBe('12:15 AM');
    expect(formatTime('12:00', 'en')).toBe('12:00 PM');
    expect(formatTime('21:05', 'en')).toBe('9:05 PM');
  });

  it('returns anything else as is', () => {
    expect(formatTime('', 'en')).toBe('');
    expect(formatTime('9:30', 'en')).toBe('9:30');
  });
});

describe('timeOptions', () => {
  it('lists the quarter hours 00:00 to 23:45', () => {
    const options = timeOptions();
    expect(options).toHaveLength(96);
    expect(options[0]).toBe('00:00');
    expect(options[38]).toBe('09:30');
    expect(options.at(-1)).toBe('23:45');
  });

  it('takes another step', () => {
    expect(timeOptions(30)).toHaveLength(48);
  });
});

describe('minutesOfDay and addMinutes', () => {
  it('counts the minutes and stays in the day', () => {
    expect(minutesOfDay('09:30')).toBe(570);
    expect(minutesOfDay('9:30')).toBeNull();
    expect(addMinutes('10:00', 60)).toBe('11:00');
    expect(addMinutes('23:30', 60)).toBeNull();
    expect(addMinutes('', 60)).toBeNull();
  });
});

describe('timeMatches', () => {
  it('matches the label, HH:MM or H:MM from the start, ignoring separators', () => {
    expect(timeMatches('09:30', '930', 'en')).toBe(true);
    expect(timeMatches('21:30', '9:30 p', 'en')).toBe(true);
    expect(timeMatches('09:30', '9:30 p', 'en')).toBe(false);
    expect(timeMatches('09:30', '9', 'es')).toBe(true);
    expect(timeMatches('09:30', '09.3', 'es')).toBe(true);
    expect(timeMatches('19:30', '9', 'es')).toBe(false);
    expect(timeMatches('21:00', '21', 'es')).toBe(true);
    expect(timeMatches('10:00', '', 'es')).toBe(true);
  });
});
