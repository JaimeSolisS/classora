// Field errors that follow the values (HF-13, #598 E4), the pattern of
// `AvailabilitySection` and `ClassroomDialog`: before the first submit
// attempt nothing shows while typing; after it, every render shows the
// client checks of the current values. The server's field errors stay on
// their field until that field changes. Pure: the forms keep `checked` and
// the server's errors in state and derive what they show.

/** A form's errors: a message (key) per field. */
export type FieldErrors<F extends string, M> = Partial<Record<F, M>>;

/**
 * What the fields show: once `checked`, the client checks (they win over a
 * server error on the same field), else only the server's errors.
 */
export function shownErrors<F extends string, M>(
  checked: boolean,
  server: FieldErrors<F, M>,
  client: () => FieldErrors<F, M>,
): FieldErrors<F, M> {
  return checked ? { ...server, ...client() } : server;
}

/**
 * The server's errors without those of `fields` (the ones that changed).
 * The same object when nothing goes (no extra render).
 */
export function withoutErrors<F extends string, M>(
  errors: FieldErrors<F, M>,
  fields: readonly F[],
): FieldErrors<F, M> {
  if (!fields.some((field) => errors[field] !== undefined)) {
    return errors;
  }
  const next = { ...errors };
  for (const field of fields) {
    delete next[field];
  }
  return next;
}

/**
 * The fields whose server errors a change of `field` clears: a start or
 * end clears both (end before start and the duration concern the pair).
 */
export function changedFields<F extends string>(field: F): F[] {
  return field === 'start' || field === 'end'
    ? (['start', 'end'] as F[])
    : [field];
}
