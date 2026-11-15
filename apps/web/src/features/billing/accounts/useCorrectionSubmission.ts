import { useState } from 'react';
import { useIdempotentSubmission } from '@/lib/idempotency';
import { isCancelledReauth } from './corrections';

// The submission of a correction dialog (#386 D1, D2; #88): the key is
// computed from the body with `keyFor` BEFORE the call runs, so apiCall's
// retry after the password dialog (`reauth_required`, #26) and a second
// press after a network error resend the same key; a changed body gets a
// new key, and `succeeded()` gives the next submission a new one. Nothing
// changes before the server answers: the caller closes the dialog and
// invalidates after the 201. A cancelled password dialog leaves the form
// as it was, without an error (D3); a 422 goes on the fields (`onFields`
// returns true when it placed one); anything else is the dialog's alert,
// and `onStale` reloads the data a 409 says changed.

export interface CorrectionSubmission {
  submitting: boolean;
  failure: unknown;
  run: <B>(
    body: B,
    send: (body: B, idempotencyKey: string) => Promise<void>,
    handlers?: {
      onFields?: (error: unknown) => boolean;
      onStale?: (error: unknown) => void;
    },
  ) => Promise<void>;
}

/**
 * `busyRef` is the dialog's ref that keeps it open while a submission runs
 * (Escape and the close button are ignored meanwhile).
 */
export function useCorrectionSubmission(busyRef: {
  current: boolean;
}): CorrectionSubmission {
  const submission = useIdempotentSubmission();
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  async function run<B>(
    body: B,
    send: (body: B, idempotencyKey: string) => Promise<void>,
    handlers: {
      onFields?: (error: unknown) => boolean;
      onStale?: (error: unknown) => void;
    } = {},
  ) {
    if (busyRef.current) return;
    setFailure(null);
    // Outside the call (#88): a retry of the same body resends this key.
    const key = submission.keyFor(body);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await send(body, key);
      submission.succeeded();
    } catch (caught) {
      if (isCancelledReauth(caught)) {
        // The password dialog was cancelled: keep the form, no alert.
      } else if (!handlers.onFields?.(caught)) {
        setFailure(caught);
        handlers.onStale?.(caught);
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  return { submitting, failure, run };
}
