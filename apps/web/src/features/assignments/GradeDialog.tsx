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
import type { GradePut, RosterEntry } from './api';
import { AssignmentFailure } from './AssignmentFailure';
import {
  classifyRosterRefusal,
  FEEDBACK_MAX,
  gradeBody,
  gradeErrors,
  gradeServerErrors,
  REASON_MAX,
  type FormMessage,
  type GradeField,
  type GradeInput,
} from './rules';

// "Grade {student}" (#313 C5): with a max score, a required Score followed
// by "of {max}" (lib/decimal: 0 to max, two decimals at most) and optional
// Feedback; feedback-only homework, a required Feedback. A re-grade also
// asks for a "Reason for the change" (internal: families only see
// "Updated"); a first grade never sends one. One request per save; a
// refused field shows on its field (focused), a conflict or a missing
// submission shows its message with Reload, a read-only refusal its
// message. The page moves focus once the dialog closes.

export interface GradeDialogProps {
  open: boolean;
  row: RosterEntry | null;
  maxScore: string | null;
  /** Saves; resolves when stored (the page closes the dialog). */
  onSave: (body: GradePut) => Promise<void>;
  onCancel: () => void;
  /** Closes the dialog and reloads the roster. */
  onReload: () => void;
}

const ORDER: readonly GradeField[] = ['score', 'feedback', 'reason'];

export function GradeDialog({
  open,
  row,
  maxScore,
  onSave,
  onCancel,
  onReload,
}: GradeDialogProps) {
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
          <GradeForm
            key={`${row.student.id}-${row.version}`}
            row={row}
            maxScore={maxScore}
            onSave={onSave}
            onCancel={onCancel}
            onReload={onReload}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function GradeForm({
  row,
  maxScore,
  onSave,
  onCancel,
  onReload,
}: Omit<GradeDialogProps, 'open' | 'row'> & { row: RosterEntry }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const regrade = row.state === 'graded';
  const [input, setInput] = useState<GradeInput>({
    score: row.score ?? '',
    feedback: row.feedback ?? '',
    reason: '',
  });
  const [errors, setErrors] = useState<Partial<Record<GradeField, string>>>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const failureRef = useRef<HTMLDivElement>(null);
  const [focus, setFocus] = useState<{
    to: GradeField | 'failure';
    n: number;
  } | null>(null);
  const name = row.student.display_name;

  useEffect(() => {
    if (!focus) {
      return;
    }
    const timer = window.setTimeout(() => {
      const target =
        focus.to === 'failure'
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

  function show(found: Partial<Record<GradeField, FormMessage>>): boolean {
    const shown: Partial<Record<GradeField, string>> = {};
    for (const field of ORDER) {
      const message = found[field];
      if (message) {
        shown[field] = text(message);
      }
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
    if (show(gradeErrors(input, { maxScore, regrade }))) {
      return;
    }
    setBusy(true);
    try {
      await onSave(
        gradeBody(input, { version: row.version, maxScore, regrade }),
      );
    } catch (caught) {
      setBusy(false);
      if (
        classifyRosterRefusal(caught) === 'fields' &&
        show(gradeServerErrors(caught))
      ) {
        return;
      }
      setFailure(caught);
      setFocus((current) => ({ to: 'failure', n: (current?.n ?? 0) + 1 }));
    }
  }

  const refusal = failure === null ? null : classifyRosterRefusal(failure);
  const field = (part: GradeField) => `${id}-${part}`;
  const set = (part: GradeField, value: string) =>
    setInput((current) => ({ ...current, [part]: value }));

  return (
    <form noValidate onSubmit={(event) => void submit(event)}>
      <DialogHeader>
        <DialogTitle>{t('assignments.grade.title', { name })}</DialogTitle>
        <DialogDescription>
          {t('assignments.grade.visible', { name })}
        </DialogDescription>
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
        {maxScore !== null ? (
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={field('score')} className="text-sm font-medium">
              {t('assignments.grade.score')}
            </label>
            <div className="flex items-center gap-2">
              <Input
                id={field('score')}
                inputMode="decimal"
                autoComplete="off"
                className="max-w-28"
                value={input.score}
                aria-invalid={errors.score ? true : undefined}
                aria-describedby={[
                  `${field('score')}-of`,
                  errors.score ? `${field('score')}-error` : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onChange={(event) => set('score', event.target.value)}
              />
              <span id={`${field('score')}-of`} className="text-sm">
                {t('assignments.grade.of', { max: maxScore })}
              </span>
            </div>
            <FieldError id={`${field('score')}-error`} message={errors.score} />
          </div>
        ) : null}
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('feedback')} className="text-sm font-medium">
            {maxScore !== null
              ? t('assignments.grade.feedbackOptional')
              : t('assignments.grade.feedback')}
          </label>
          <textarea
            id={field('feedback')}
            rows={4}
            className={TEXTAREA_CLASS}
            value={input.feedback}
            aria-invalid={errors.feedback ? true : undefined}
            aria-describedby={[
              errors.feedback ? `${field('feedback')}-error` : '',
              `${field('feedback')}-counter`,
            ]
              .filter(Boolean)
              .join(' ')}
            onChange={(event) => set('feedback', event.target.value)}
          />
          <FieldError
            id={`${field('feedback')}-error`}
            message={errors.feedback}
          />
          <Counter
            id={`${field('feedback')}-counter`}
            value={input.feedback}
            max={FEEDBACK_MAX}
          />
        </div>
        {regrade ? (
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={field('reason')} className="text-sm font-medium">
              {t('assignments.grade.reason')}
            </label>
            <textarea
              id={field('reason')}
              rows={2}
              className={TEXTAREA_CLASS}
              value={input.reason}
              aria-invalid={errors.reason ? true : undefined}
              aria-describedby={[
                `${field('reason')}-help`,
                errors.reason ? `${field('reason')}-error` : '',
                `${field('reason')}-counter`,
              ]
                .filter(Boolean)
                .join(' ')}
              onChange={(event) => set('reason', event.target.value)}
            />
            <p
              id={`${field('reason')}-help`}
              className="text-sm text-muted-foreground"
            >
              {t('assignments.grade.reasonHelp')}
            </p>
            <FieldError
              id={`${field('reason')}-error`}
              message={errors.reason}
            />
            <Counter
              id={`${field('reason')}-counter`}
              value={input.reason}
              max={REASON_MAX}
            />
          </div>
        ) : null}
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
            t('assignments.grade.save')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
