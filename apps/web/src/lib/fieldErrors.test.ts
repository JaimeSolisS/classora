import { describe, expect, it, vi } from 'vitest';
import { changedFields, shownErrors, withoutErrors } from './fieldErrors';

// HF-13 (#598 E4): errors that follow the values after the first submit.

type Field = 'name' | 'start' | 'end';

describe('shownErrors', () => {
  it('shows only the server errors before the first submit', () => {
    const client = vi.fn(() => ({ name: 'required' }));
    expect(
      shownErrors<Field, string>(false, { end: 'server' }, client),
    ).toEqual({ end: 'server' });
    expect(client).not.toHaveBeenCalled();
  });

  it('shows the client checks over the server errors once checked', () => {
    expect(
      shownErrors<Field, string>(
        true,
        { end: 'server', name: 'taken' },
        () => ({ name: 'required' }),
      ),
    ).toEqual({ end: 'server', name: 'required' });
  });
});

describe('withoutErrors and changedFields', () => {
  it('drops the changed fields, start and end together', () => {
    const errors = { start: 'a', end: 'b', name: 'c' };
    expect(withoutErrors<Field, string>(errors, changedFields('end'))).toEqual({
      name: 'c',
    });
    expect(withoutErrors<Field, string>(errors, changedFields('name'))).toEqual(
      { start: 'a', end: 'b' },
    );
  });

  it('returns the same object when nothing goes', () => {
    const errors = { name: 'c' };
    expect(withoutErrors<Field, string>(errors, ['start'])).toBe(errors);
  });
});
