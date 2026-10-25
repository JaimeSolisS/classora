import { ApiError } from '@/app/api';

// The session notes' pure rules (#79 H1-H3), mirroring the API's
// (academics/note_rules.py): the text bounds, the composer's and the
// unpublish reason's checks, the server's field errors mapped back, which
// failed writes reload and which show the not-found page, and when My
// schedule offers "Notes".

export const BODY_MAX = 4000;
export const UNPUBLISH_REASON_MAX = 500;

/** A text's length once trimmed (the counters). */
export function trimmedLength(value: string): number {
  return value.trim().length;
}

export type BodyError = 'body_required' | 'too_long';

/** The composer's error for `body` (trimmed, as the API checks it). */
export function bodyError(body: string): BodyError | undefined {
  const length = trimmedLength(body);
  if (length === 0) {
    return 'body_required';
  }
  return length > BODY_MAX ? 'too_long' : undefined;
}

export type ReasonError = 'reason_required' | 'reason_too_long';

/** A staff unpublish reason's error (required, 1-500 once trimmed). */
export function reasonError(reason: string): ReasonError | undefined {
  const length = trimmedLength(reason);
  if (length === 0) {
    return 'reason_required';
  }
  return length > UNPUBLISH_REASON_MAX ? 'reason_too_long' : undefined;
}

/** The body's field error code of a refused write (422), if any. */
export function serverBodyError(error: unknown): BodyError | undefined {
  if (!(error instanceof ApiError)) {
    return undefined;
  }
  const found = error.fieldErrors.find(({ code }) =>
    ['body_required', 'too_long'].includes(code),
  );
  return found ? (found.code as BodyError) : undefined;
}

/** The reason's field error code of a refused unpublish (422), if any. */
export function serverReasonError(error: unknown): ReasonError | undefined {
  if (!(error instanceof ApiError)) {
    return undefined;
  }
  const found = error.fieldErrors.find(({ code }) =>
    ['reason_required', 'reason_too_long'].includes(code),
  );
  return found ? (found.code as ReasonError) : undefined;
}

/** H1: a write refused because the note changed: "Note changed elsewhere"
 * with Reload. */
export function needsReload(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'version_conflict';
}

const NOT_FOUND_CODES = new Set([
  'note_published',
  'note_read_only',
  'session_not_notable',
  'student_not_participant',
  'not_found',
]);

/** H1: a write refused for one of these shows the not-found page. */
export function showsNotFound(error: unknown): boolean {
  if (!(error instanceof ApiError)) {
    return false;
  }
  if (error.status === 404 || NOT_FOUND_CODES.has(error.code)) {
    return true;
  }
  return error.fieldErrors.some(
    ({ code }) => code === 'student_not_participant',
  );
}

/** H2: a tutor's confirmed or completed entry offers "Notes". */
export function tutorNotesLink(item: { state: string }): boolean {
  return item.state === 'confirmed' || item.state === 'completed';
}
