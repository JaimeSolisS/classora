import { useRef, useState, type FormEvent } from 'react';
import {
  isGone,
  isLostAnswer,
  isNameTaken,
  isVersionConflict,
  type FinanceMessage,
} from './forms';

// The save flow shared by the category and vendor dialogs (M7-11b B, C, D),
// without any API call. An edit works from one snapshot, as the pay item type
// dialog does: a newer version replaces it and the form and says so, never
// during a save. Refusals:
// - a field refusal (a taken name, a text code) goes on its field;
// - `version_conflict` reloads the list, and the newer version replaces the
//   form once it arrives (B5); a failed reload leaves the refusal with a
//   Reload button, so nothing claims it reloaded;
// - `404` reloads the list, says the row is gone (reloaded or not) and locks
//   the form (B5);
// - a lost answer (no answer or a 5xx) may have stored the row. A create
//   retried after one can only end in `…_exists` (#114 E4): then the dialog
//   reloads the list and says the name may already have been saved (D2).

export type RowNotice =
  | 'conflictReloaded'
  | 'goneReloaded'
  | 'goneNotReloaded'
  | 'maybeSavedReloaded'
  | 'maybeSavedNotReloaded';

/** What a submit did: stored a change, or found nothing to send. */
export type SaveResult = 'saved' | 'unchanged';

export interface RowSaveOptions<
  Row extends { version: number },
  Values,
  Field extends string,
> {
  /** The row being edited, as the list has it now; null adds one. */
  row: Row | null;
  initial: (row: Row | null) => Values;
  initialValues?: Values;
  initialErrors?: Partial<Record<Field, FinanceMessage>>;
  /** Stories: the notice to start with. */
  initialNotice?: RowNotice | null;
  validate: (values: Values) => Partial<Record<Field, FinanceMessage>>;
  serverErrors: (
    error: unknown,
  ) => Partial<Record<Field, FinanceMessage>> | null;
  /** Stores `values` based on `base`; rejects with the refusal. */
  onSubmit: (values: Values, base: Row | null) => Promise<SaveResult>;
  /** Refetches the list; resolves whether it reloaded. */
  onReload: () => Promise<boolean>;
  onClose: (saved: boolean) => void;
  /** Moves the focus to the first field with an error. */
  focusFirst: (errors: Partial<Record<Field, FinanceMessage>>) => void;
  busyRef: { current: boolean };
}

export function useRowSave<
  Row extends { version: number },
  Values,
  Field extends string,
>({
  row,
  initial,
  initialValues,
  initialErrors = {},
  initialNotice = null,
  validate,
  serverErrors,
  onSubmit,
  onReload,
  onClose,
  focusFirst,
  busyRef,
}: RowSaveOptions<Row, Values, Field>) {
  const [base, setBase] = useState(row);
  const [values, setValues] = useState<Values>(
    () => initialValues ?? initial(row),
  );
  const [errors, setErrors] =
    useState<Partial<Record<Field, FinanceMessage>>>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [notice, setNotice] = useState<RowNotice | null>(initialNotice);
  const [submitting, setSubmitting] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [gone, setGone] = useState(
    initialNotice === 'goneReloaded' || initialNotice === 'goneNotReloaded',
  );
  // A create whose answer was lost may have stored the row (D2).
  const lostBefore = useRef(false);
  if (!submitting && !gone && row && row.version !== base?.version) {
    // Adjusting state while rendering: see PayItemTypeDialog.
    setBase(row);
    setValues(initial(row));
    setErrors({});
    setFailure(null);
    setNotice('conflictReloaded');
  }

  async function reload(): Promise<boolean> {
    setReloading(true);
    try {
      return await onReload();
    } finally {
      setReloading(false);
    }
  }

  async function submit() {
    const found = validate(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    setFailure(null);
    setNotice(null);
    busyRef.current = true;
    setSubmitting(true);
    try {
      const result = await onSubmit(values, base);
      busyRef.current = false;
      onClose(result === 'saved');
      return;
    } catch (caught) {
      const fromServer = serverErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
        if (base === null && lostBefore.current && isNameTaken(caught)) {
          setSubmitting(false);
          const reloaded = await reload();
          setNotice(reloaded ? 'maybeSavedReloaded' : 'maybeSavedNotReloaded');
        }
      } else if (isVersionConflict(caught)) {
        setFailure(caught);
        setSubmitting(false);
        await reload();
      } else if (isGone(caught)) {
        setGone(true);
        setSubmitting(false);
        const reloaded = await reload();
        setNotice(reloaded ? 'goneReloaded' : 'goneNotReloaded');
      } else {
        setFailure(caught);
        if (isLostAnswer(caught)) {
          lostBefore.current = true;
        }
      }
    }
    busyRef.current = false;
    setSubmitting(false);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current || gone) {
      return;
    }
    void submit();
  }

  return {
    base,
    values,
    setValues,
    errors,
    failure,
    notice,
    submitting,
    reloading,
    gone,
    /** Fields and Save are disabled. */
    locked: submitting || reloading || gone,
    conflict: isVersionConflict(failure),
    reload,
    onFormSubmit,
  };
}
