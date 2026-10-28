import { useState } from 'react';

// Idempotency keys for financial submissions (#88; ADR-0006 E6; _docs/api.md
// "Idempotency"). A route that posts a ledger row requires an
// `Idempotency-Key` header: one key per submission, sent again when the same
// submission is retried, so a retry returns the first response instead of
// posting twice.
//
// Compute the key OUTSIDE the `call` closure given to `apiCall`:
//
//   const key = submission.keyFor(body);
//   const data = await apiCall(() =>
//     api.POST('/api/v1/...', { body, headers: idempotencyHeaders(key) }),
//   );
//   submission.succeeded();
//
// `apiCall` calls the closure again after `csrf_failed` or `reauth_required`,
// and those retries then resend the same key. A person pressing Submit again
// after a network error or a 5xx gets the same key too, as long as the body is
// unchanged; a changed body is a new submission with a new key.

export const IDEMPOTENCY_HEADER = 'Idempotency-Key';

/** A new key: a random UUID (36 characters of `[0-9a-f-]`). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

/** The request header for `key` (openapi-fetch `headers` or `params.header`). */
export function idempotencyHeaders(
  key: string,
): Record<'Idempotency-Key', string> {
  return { [IDEMPOTENCY_HEADER]: key };
}

/** JSON with object keys sorted at every depth (arrays keep their order). */
function stableJson(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) {
      return item;
    }
    const record = item as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(record)
        .sort()
        .map((key) => [key, record[key]]),
    );
  });
}

/**
 * The key of one form's submissions. `keyFor(body)` returns the same key while
 * the body (compared as key-sorted JSON) is unchanged and no attempt has
 * succeeded; it returns a new key after `succeeded()`, after `reset()`, or
 * when the body changes.
 */
export class IdempotentSubmission {
  private key: string | null = null;
  private body: string | null = null;

  keyFor(body: unknown): string {
    const serialized = stableJson(body) ?? '';
    if (this.key === null || this.body !== serialized) {
      this.key = newIdempotencyKey();
      this.body = serialized;
    }
    return this.key;
  }

  /** The submission was accepted: the next one gets a new key. */
  succeeded(): void {
    this.reset();
  }

  /** Forget the key (for example when the form is cleared). */
  reset(): void {
    this.key = null;
    this.body = null;
  }
}

/**
 * One `IdempotentSubmission` for the lifetime of the mounted component (a
 * lazy `useState` initializer: created once, never replaced, and reading it
 * during render is allowed, unlike a ref).
 */
export function useIdempotentSubmission(): IdempotentSubmission {
  const [submission] = useState(() => new IdempotentSubmission());
  return submission;
}
