import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import type { LocalDate } from '@/lib/time';
import { Counter } from '../Counter';
import { TEXTAREA_CLASS } from '../styles';
import {
  COMMENT_MAX,
  FEEDBACK_MAX,
  REASON_MAX,
  type EvaluationErrors,
  type EvaluationValues,
  type FormCriterion,
} from './rules';
import { useEvaluationMessage } from './useEvaluationMessage';

// The evaluation form's fields (#308 C1, C2, C5), phone-first in one
// column: "Evaluated on" (a date input, at most today), one fieldset per
// criterion in position order (its legend is the position and title, plus
// the description) with a Score text input (inputmode decimal) followed by
// "of {max}" and an optional Comment with a counter, then the overall
// Feedback with its help text and, while amending, the required "Reason
// for the change" (internal). Every error is under its field and linked
// with aria-describedby. Controlled: the container keeps the values. The
// client never computes totals.

export interface EvaluationFieldsProps {
  /** A unique prefix for the field ids. */
  idPrefix: string;
  criteria: readonly FormCriterion[];
  values: EvaluationValues;
  errors: EvaluationErrors;
  onChange: (next: EvaluationValues) => void;
  today: LocalDate;
  /** Amending a final evaluation: the reason field shows. */
  amend?: boolean;
  disabled?: boolean;
}

export function EvaluationFields({
  idPrefix,
  criteria,
  values,
  errors,
  onChange,
  today,
  amend = false,
  disabled = false,
}: EvaluationFieldsProps) {
  const { t } = useTranslation();
  const message = useEvaluationMessage();
  const id = (name: string) => `${idPrefix}-${name}`;
  const described = (...parts: (string | false | undefined)[]) =>
    parts.filter(Boolean).join(' ') || undefined;
  const dateMessage = message(errors.evaluatedOn);
  const feedbackMessage = message(errors.feedback);
  const reasonMessage = message(errors.changeReason);
  const sorted = [...criteria].sort((a, b) => a.position - b.position);

  function setScore(
    position: number,
    change: Partial<{ score: string; comment: string }>,
  ) {
    onChange({
      ...values,
      scores: values.scores.map((entry) =>
        entry.position === position ? { ...entry, ...change } : entry,
      ),
    });
  }

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <div className="flex min-w-0 flex-col gap-2 sm:max-w-xs">
        <label htmlFor={id('date')} className="text-sm font-medium">
          {t('academics.evaluations.fields.evaluatedOn')}
        </label>
        <Input
          id={id('date')}
          type="date"
          max={today}
          value={values.evaluatedOn}
          disabled={disabled}
          aria-invalid={dateMessage ? true : undefined}
          aria-describedby={described(dateMessage && id('date-error'))}
          onChange={(event) =>
            onChange({ ...values, evaluatedOn: event.target.value })
          }
        />
        <FieldError id={id('date-error')} message={dateMessage} />
      </div>
      {sorted.map((criterion) => {
        const entry = values.scores.find(
          (s) => s.position === criterion.position,
        );
        const found = errors.scores[criterion.position];
        const scoreMessage = message(found?.score);
        const commentMessage = message(found?.comment);
        const key = (name: string) => id(`${criterion.position}-${name}`);
        return (
          <fieldset
            key={criterion.position}
            className="flex min-w-0 flex-col gap-3 rounded-md border border-border p-3"
          >
            <legend className="px-1 text-base font-semibold break-words">
              {t('academics.evaluations.fields.criterion', {
                position: criterion.position,
                title: criterion.title,
              })}
            </legend>
            {criterion.description ? (
              <p className="text-sm break-words whitespace-pre-line text-muted-foreground">
                {criterion.description}
              </p>
            ) : null}
            <div className="flex min-w-0 flex-col gap-2">
              <label htmlFor={key('score')} className="text-sm font-medium">
                {t('academics.evaluations.fields.score')}
              </label>
              <div className="flex items-center gap-2">
                <Input
                  id={key('score')}
                  data-score-position={criterion.position}
                  inputMode="decimal"
                  autoComplete="off"
                  className="w-28"
                  value={entry?.score ?? ''}
                  disabled={disabled}
                  aria-invalid={scoreMessage ? true : undefined}
                  aria-describedby={described(
                    key('of'),
                    scoreMessage && key('score-error'),
                  )}
                  onChange={(event) =>
                    setScore(criterion.position, { score: event.target.value })
                  }
                />
                <span id={key('of')} className="text-sm">
                  {t('academics.evaluations.fields.of', {
                    max: criterion.max_score,
                  })}
                </span>
              </div>
              <FieldError id={key('score-error')} message={scoreMessage} />
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <label htmlFor={key('comment')} className="text-sm font-medium">
                {t('academics.evaluations.fields.comment')}
              </label>
              <textarea
                id={key('comment')}
                rows={2}
                className={TEXTAREA_CLASS}
                value={entry?.comment ?? ''}
                disabled={disabled}
                aria-invalid={commentMessage ? true : undefined}
                aria-describedby={described(
                  commentMessage && key('comment-error'),
                  key('comment-counter'),
                )}
                onChange={(event) =>
                  setScore(criterion.position, {
                    comment: event.target.value,
                  })
                }
              />
              <FieldError id={key('comment-error')} message={commentMessage} />
              <Counter
                id={key('comment-counter')}
                value={entry?.comment ?? ''}
                max={COMMENT_MAX}
              />
            </div>
          </fieldset>
        );
      })}
      <div className="flex min-w-0 flex-col gap-2">
        <label htmlFor={id('feedback')} className="text-sm font-medium">
          {t('academics.evaluations.fields.feedback')}
        </label>
        <p id={id('feedback-help')} className="text-sm text-muted-foreground">
          {t('academics.evaluations.fields.feedbackHelp')}
        </p>
        <textarea
          id={id('feedback')}
          rows={4}
          className={TEXTAREA_CLASS}
          value={values.feedback}
          disabled={disabled}
          aria-invalid={feedbackMessage ? true : undefined}
          aria-describedby={described(
            id('feedback-help'),
            feedbackMessage && id('feedback-error'),
            id('feedback-counter'),
          )}
          onChange={(event) =>
            onChange({ ...values, feedback: event.target.value })
          }
        />
        <FieldError id={id('feedback-error')} message={feedbackMessage} />
        <Counter
          id={id('feedback-counter')}
          value={values.feedback}
          max={FEEDBACK_MAX}
        />
      </div>
      {amend ? (
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={id('reason')} className="text-sm font-medium">
            {t('academics.evaluations.fields.changeReason')}
          </label>
          <p id={id('reason-help')} className="text-sm text-muted-foreground">
            {t('academics.evaluations.fields.changeReasonHelp')}
          </p>
          <textarea
            id={id('reason')}
            rows={3}
            className={TEXTAREA_CLASS}
            value={values.changeReason}
            disabled={disabled}
            aria-invalid={reasonMessage ? true : undefined}
            aria-describedby={described(
              id('reason-help'),
              reasonMessage && id('reason-error'),
              id('reason-counter'),
            )}
            onChange={(event) =>
              onChange({ ...values, changeReason: event.target.value })
            }
          />
          <FieldError id={id('reason-error')} message={reasonMessage} />
          <Counter
            id={id('reason-counter')}
            value={values.changeReason}
            max={REASON_MAX}
          />
        </div>
      ) : null}
    </div>
  );
}
