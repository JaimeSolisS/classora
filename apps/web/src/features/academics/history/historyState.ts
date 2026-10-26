import { ApiError } from '@/app/api';
import type { AcademicHistory as History, HistoryEntry } from './api';

// The academic history's pure helpers (#308 E5, E7): the entries the view
// renders (an unknown kind is skipped), the subjects of a history, and the
// state of its query (consent required, not available, a refused range).

export type HistoryState =
  | { status: 'loading' }
  | { status: 'invalid' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'consent' }
  | { status: 'missing' }
  | { status: 'ready'; history: History };

export interface SubjectChoice {
  id: string;
  name: string;
}

/** The entries this view knows how to render (E5: others are skipped;
 * #82's homework grade entries until #313 renders them). */
export function knownEntries(history: History): HistoryEntry[] {
  return history.entries.filter(
    (entry): entry is HistoryEntry => entry.kind === 'evaluation',
  );
}

/** The subjects of a history's entries, by name. */
export function subjectsOf(history: History | undefined): SubjectChoice[] {
  const seen = new Map<string, SubjectChoice>();
  for (const entry of history ? knownEntries(history) : []) {
    seen.set(entry.subject.id, entry.subject);
  }
  return [...seen.values()].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
  );
}

/** The state of a history query (E7). */
export function historyState(
  invalid: boolean,
  query: {
    data: History | undefined;
    error: unknown;
    isError: boolean;
    refetch: () => unknown;
  },
): { state: HistoryState; rangeError: boolean } {
  if (invalid) {
    return { state: { status: 'invalid' }, rangeError: false };
  }
  const { error } = query;
  if (error instanceof ApiError) {
    if (error.code === 'student_consent_required') {
      return { state: { status: 'consent' }, rangeError: false };
    }
    if (error.status === 404) {
      return { state: { status: 'missing' }, rangeError: false };
    }
    const range =
      error.code === 'range_invalid' ||
      error.fieldErrors.some((item) => item.code === 'range_invalid');
    if (range) {
      return { state: { status: 'invalid' }, rangeError: true };
    }
  }
  if (query.isError) {
    return {
      state: {
        status: 'error',
        error,
        onRetry: () => void query.refetch(),
      },
      rangeError: false,
    };
  }
  return query.data
    ? { state: { status: 'ready', history: query.data }, rangeError: false }
    : { state: { status: 'loading' }, rangeError: false };
}
