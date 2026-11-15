import { useState } from 'react';
import {
  isLostAnswer,
  worseFailure,
  type AttemptFailure,
} from '@/features/billing/accounts/rules';
import { isCode } from '@/features/payroll/periodRules';
import { useIdempotentSubmission } from '@/lib/idempotency';

// The #458 locked-resend rule for the four expense dialogs (M7-12b E2 (a),
// D6). Each posts money, and expenses have no duplicate rule (#115 E5; a
// payment's applies only with a reference), so a second body with a new key
// could be a second row. The key is computed before the call, so apiCall's
// retries (csrf, the password dialog) resend it. After a lost answer (no
// response, or a 5xx) the fields lock and the only action resends the exact
// body of the lost attempt with its key, never one rebuilt from the form. A
// settled answer ends that: a 201 as usual, a refusal unlocks the fields. A
// cancelled password dialog leaves everything as it was. The dialog's shell
// (`LockedDialog`) keeps the worst failure of the opening, which `onClose`
// gets, so the page refetches before another action can start.

/** What `LockedDialog` gives the body it mounts per opening. */
export interface LockedControl {
  /** A call is in flight: the dialog can't close. */
  busyRef: { current: boolean };
  /** The worst failure of this opening's attempts. */
  failedRef: { current: AttemptFailure };
  /** Closes the dialog (Cancel), passing that failure on. */
  onCancel: () => void;
}

export interface SubmitSteps<B> {
  /** The body from the form, or null when a client check failed. */
  build: () => B | null;
  send: (body: B, key: string) => Promise<void>;
  /** After the 201. */
  onSaved: () => void;
  /** A settled refusal: true when it went on the fields, else the alert. */
  onRefused: (error: unknown) => boolean;
}

export function useLockedSubmission<B>(
  busyRef: LockedControl['busyRef'],
  failedRef: LockedControl['failedRef'],
) {
  const submission = useIdempotentSubmission();
  const [lost, setLost] = useState<{ body: B; key: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  async function submit(steps: SubmitSteps<B>): Promise<void> {
    if (busyRef.current) return;
    let body: B;
    let key: string;
    if (lost) {
      // The stored body and key, as they went: nothing rebuilt.
      ({ body, key } = lost);
    } else {
      const built = steps.build();
      if (built === null) {
        setFailure(null);
        return;
      }
      body = built;
      // Outside the call (#88): apiCall's retries resend this key.
      key = submission.keyFor(body);
    }
    setFailure(null);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await steps.send(body, key);
    } catch (caught) {
      busyRef.current = false;
      setSubmitting(false);
      if (isCode(caught, 'reauth_required')) {
        // The password dialog was cancelled: nothing was sent.
        return;
      }
      failedRef.current = worseFailure(failedRef.current, caught);
      if (isLostAnswer(caught)) {
        setLost({ body, key });
        setFailure(caught);
        return;
      }
      setLost(null);
      if (!steps.onRefused(caught)) setFailure(caught);
      return;
    }
    submission.succeeded();
    failedRef.current = 'none';
    busyRef.current = false;
    setSubmitting(false);
    steps.onSaved();
  }

  return {
    submit,
    submitting,
    /** A lost answer waits: only the resend is offered. */
    lost: lost !== null,
    /** Fields are disabled. */
    locked: submitting || lost !== null,
    failure,
  };
}
