import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Counter } from '@/features/academics/Counter';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import type { RosterEntry, SubmissionPut } from './api';
import { AssignmentFailure } from './AssignmentFailure';
import {
  classifyRosterRefusal,
  TEXT_MAX,
  workBody,
  workErrors,
  workServerErrors,
  type FormMessage,
  type WorkField,
  type WorkInput,
} from './rules';

// "Record handed-in work" (#313 C4): for work handed in on paper or in
// class, an optional note (counter) and an optional link (the C6 rule).
// One request (with the row's version when something was already handed
// in); nobody is notified. A refused field shows on its field (focused), a
// conflict with Reload. The page moves focus once the dialog closes.

export interface RecordSubmissionDialogProps {
  open: boolean;
  row: RosterEntry | null;
  onSave: (body: SubmissionPut) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
}

const ORDER: readonly WorkField[] = ['text', 'url'];

export function RecordSubmissionDialog({
  open,
  row,
  onSave,
  onCancel,
  onReload,
}: RecordSubmissionDialogProps) {
  return (
    <Dialog
      open={open && row !== null}
      onOpenChange={(next) => {
        if (!next) {
          onCancel();
        }
      }}
    >
      {open && row ? (
        <DialogContent
          className="sm:max-w-lg"
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <RecordForm
            row={row}
            onSave={onSave}
            onCancel={onCancel}
            onReload={onReload}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function RecordForm({
  row,
  onSave,
  onCancel,
  onReload,
}: Omit<RecordSubmissionDialogProps, 'open' | 'row'> & { row: RosterEntry }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const [input, setInput] = useState<WorkInput>({ text: '', url: '' });
  const [errors, setErrors] = useState<Partial<Record<WorkField, string>>>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const failureRef = useRef<HTMLDivElement>(null);
  const [focus, setFocus] = useState<{
    to: WorkField | 'failure';
    n: number;
  } | null>(null);

  useEffect(() => {
    if (!focus) {
      return;
    }
    const timer = window.setTimeout(() => {
      const target =
        focus.to === 'failure' || focus.to === 'root'
          ? failureRef.current
          : document.getElementById(`${id}-${focus.to}`);
      target?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focus, id]);

  const text = (message: FormMessage): string =>
    message.startsWith('errors:')
      ? errorMessage(
          new ApiError({ code: message.slice(7), status: 422 }),
          tErrors,
        )
      : (t as unknown as (key: string) => string)(message);

  function show(found: Partial<Record<WorkField, FormMessage>>): boolean {
    const shown: Partial<Record<WorkField, string>> = {};
    for (const [field, message] of Object.entries(found) as [
      WorkField,
      FormMessage,
    ][]) {
      shown[field] = text(message);
    }
    setErrors(shown);
    const first = ORDER.find((field) => shown[field]);
    if (first) {
      setFocus((current) => ({ to: first, n: (current?.n ?? 0) + 1 }));
      return true;
    }
    return false;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    setFailure(null);
    if (show(workErrors(input, { requireOne: false }))) {
      return;
    }
    setBusy(true);
    try {
      await onSave(workBody(input, row.submitted_at ? row.version : null));
    } catch (caught) {
      setBusy(false);
      if (
        classifyRosterRefusal(caught) === 'fields' &&
        show(workServerErrors(caught))
      ) {
        return;
      }
      setFailure(caught);
      setFocus((current) => ({ to: 'failure', n: (current?.n ?? 0) + 1 }));
    }
  }

  const refusal = failure === null ? null : classifyRosterRefusal(failure);
  const field = (part: string) => `${id}-${part}`;

  return (
    <form noValidate onSubmit={(event) => void submit(event)}>
      <DialogHeader>
        <DialogTitle>
          {t('assignments.record.title', { name: row.student.display_name })}
        </DialogTitle>
        <DialogDescription>{t('assignments.record.help')}</DialogDescription>
      </DialogHeader>
      <div className="flex min-w-0 flex-col gap-4 py-4">
        {failure !== null ? (
          <AssignmentFailure
            ref={failureRef}
            error={failure}
            message={
              failure instanceof ApiError && failure.code === 'version_conflict'
                ? t('assignments.conflict')
                : undefined
            }
            onReload={refusal === 'reload' ? onReload : undefined}
          />
        ) : null}
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('text')} className="text-sm font-medium">
            {t('assignments.record.note')}
          </label>
          <textarea
            id={field('text')}
            rows={4}
            className={TEXTAREA_CLASS}
            value={input.text}
            aria-invalid={errors.text ? true : undefined}
            aria-describedby={[
              errors.text ? field('text-error') : '',
              field('text-counter'),
            ]
              .filter(Boolean)
              .join(' ')}
            onChange={(event) =>
              setInput((current) => ({ ...current, text: event.target.value }))
            }
          />
          <FieldError id={field('text-error')} message={errors.text} />
          <Counter
            id={field('text-counter')}
            value={input.text}
            max={TEXT_MAX}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('url')} className="text-sm font-medium">
            {t('assignments.record.link')}
          </label>
          <Input
            id={field('url')}
            type="url"
            inputMode="url"
            autoComplete="url"
            value={input.url}
            aria-invalid={errors.url ? true : undefined}
            aria-describedby={errors.url ? field('url-error') : undefined}
            onChange={(event) =>
              setInput((current) => ({ ...current, url: event.target.value }))
            }
          />
          <FieldError id={field('url-error')} message={errors.url} />
        </div>
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={onCancel}
        >
          {t('assignments.cancel')}
        </Button>
        <Button type="submit" disabled={busy}>
          {busy ? (
            <Spinner label={t('assignments.busy')} showLabel />
          ) : (
            t('assignments.record.save')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
