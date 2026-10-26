import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Counter } from '@/features/academics/Counter';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import { useFormatters } from '@/lib/formatters';
import type { StudentAssignment, SubmissionPut } from './api';
import { AssignmentFailure } from './AssignmentFailure';
import {
  classifySubmitRefusal,
  TEXT_MAX,
  workBody,
  workErrors,
  workServerErrors,
  type FormMessage,
  type WorkField,
  type WorkInput,
} from './rules';

// The student's hand-in form (#313 E6; only when `can_submit`, on the
// student's own login): Text (counter) and Link (the C6 rule), at least one
// of them ("Write your answer or add a link."; trimmed empty is empty). A
// first submission sends {text, url}; a replacement adds the submission's
// version and starts from the current text and link. "Hand in" / "Replace
// my work", with "You can replace your work until {deadline}." or, for a
// missing item, "The deadline has passed. Your work will be marked late.".
// A refused field shows on its field (focused); a locked, graded or
// changed submission shows its message with Reload (focused).

export interface SubmitFormProps {
  item: StudentAssignment;
  /** Saves; resolves when stored (the view announces and moves focus). */
  onSubmit: (body: SubmissionPut) => Promise<void>;
  onReload: () => void;
}

const ORDER: readonly WorkField[] = ['root', 'text', 'url'];

export function SubmitForm({ item, onSubmit, onReload }: SubmitFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const { formatInstant } = useFormatters();
  const id = useId();
  const { submission } = item;
  const replacing = submission.submitted_at !== null;
  const [input, setInput] = useState<WorkInput>({
    text: submission.text ?? '',
    url: submission.url ?? '',
  });
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
        focus.to === 'failure'
          ? failureRef.current
          : document.getElementById(
              `${id}-${focus.to === 'root' ? 'text' : focus.to}`,
            );
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
    if (show(workErrors(input, { requireOne: true }))) {
      return;
    }
    setBusy(true);
    try {
      await onSubmit(workBody(input, replacing ? submission.version : null));
    } catch (caught) {
      setBusy(false);
      const refusal = classifySubmitRefusal(caught);
      if (refusal === 'missing') {
        return; // The page shows "This homework is no longer available."
      }
      if (refusal === 'fields' && show(workServerErrors(caught))) {
        return;
      }
      setFailure(caught);
      setFocus((current) => ({ to: 'failure', n: (current?.n ?? 0) + 1 }));
    }
  }

  const refusal = failure === null ? null : classifySubmitRefusal(failure);
  const failureMessage =
    refusal === 'locked'
      ? t('assignments.submit.locked')
      : refusal === 'graded'
        ? t('assignments.submit.graded')
        : refusal === 'conflict'
          ? t('assignments.submit.conflict')
          : undefined;
  const field = (part: string) => `${id}-${part}`;
  const textDescribed = [
    errors.root ? field('root-error') : '',
    errors.text ? field('text-error') : '',
    field('text-counter'),
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <form
      noValidate
      onSubmit={(event) => void submit(event)}
      className="flex min-w-0 flex-col gap-4"
    >
      <p id={field('help')} className="text-sm text-muted-foreground">
        {submission.state === 'missing'
          ? t('assignments.submit.lateHelp')
          : t('assignments.submit.help', {
              deadline: formatInstant(item.deadline_at),
            })}
      </p>
      {failure !== null ? (
        <AssignmentFailure
          ref={failureRef}
          error={failure}
          message={failureMessage}
          onReload={refusal === 'other' ? undefined : onReload}
        />
      ) : null}
      {errors.root ? (
        <p
          id={field('root-error')}
          role="alert"
          className="text-sm text-destructive"
        >
          {errors.root}
        </p>
      ) : null}
      <div className="flex min-w-0 flex-col gap-2">
        <label htmlFor={field('text')} className="text-sm font-medium">
          {t('assignments.submit.text')}
        </label>
        <textarea
          id={field('text')}
          rows={6}
          className={TEXTAREA_CLASS}
          value={input.text}
          aria-invalid={errors.text || errors.root ? true : undefined}
          aria-describedby={textDescribed}
          onChange={(event) =>
            setInput((current) => ({ ...current, text: event.target.value }))
          }
        />
        <FieldError id={field('text-error')} message={errors.text} />
        <Counter id={field('text-counter')} value={input.text} max={TEXT_MAX} />
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <label htmlFor={field('url')} className="text-sm font-medium">
          {t('assignments.submit.link')}
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
      <div>
        <Button type="submit" disabled={busy}>
          {busy ? (
            <Spinner label={t('assignments.busy')} showLabel />
          ) : replacing ? (
            t('assignments.submit.replace')
          ) : (
            t('assignments.submit.handIn')
          )}
        </Button>
      </div>
    </form>
  );
}
