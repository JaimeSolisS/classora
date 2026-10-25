import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { Evaluation } from './api';

// One evaluation as text (#308 C5, C6): the read view of a final or voided
// evaluation, and of any evaluation the caller can't change. "Total
// {score_total} of {max_total} ({score_percent} %)" exactly as the API sent
// it, the finalized date, the author, "Updated on {date}" after an
// amendment, the internal last change reason when the API returns it (the
// author and staff), the void line, every criterion's score and comment,
// and the feedback. Every free text is plain text (never HTML).

export function EvaluationReadView({ evaluation }: { evaluation: Evaluation }) {
  const { t } = useTranslation();
  const { formatInstant, formatLocalDate } = useFormatters();
  const scores = [...evaluation.scores].sort((a, b) => a.position - b.position);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {evaluation.voided ? (
        <p className="rounded-md border border-border p-3 text-sm break-words whitespace-pre-line">
          {t('academics.evaluations.read.voided', {
            date: formatInstant(evaluation.voided.at, 'date'),
            name: evaluation.voided.by.display_name,
            reason: evaluation.voided.reason,
          })}
        </p>
      ) : null}
      {evaluation.status === 'final' && evaluation.score_total !== null ? (
        <p className="text-base font-semibold">
          {t('academics.evaluations.read.total', {
            total: evaluation.score_total,
            max: evaluation.max_total,
            percent: evaluation.score_percent,
          })}
        </p>
      ) : null}
      <dl className="grid min-w-0 grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="font-medium">
          {t('academics.evaluations.read.evaluatedOn')}
        </dt>
        <dd>{formatLocalDate(evaluation.evaluated_on)}</dd>
        {evaluation.finalized_at ? (
          <>
            <dt className="font-medium">
              {t('academics.evaluations.read.finalizedOn')}
            </dt>
            <dd>{formatInstant(evaluation.finalized_at, 'date')}</dd>
          </>
        ) : null}
        {evaluation.amended_at ? (
          <>
            <dt className="font-medium">
              {t('academics.evaluations.read.updated')}
            </dt>
            <dd>{formatInstant(evaluation.amended_at, 'date')}</dd>
          </>
        ) : null}
      </dl>
      {evaluation.last_change_reason ? (
        <p className="text-sm break-words whitespace-pre-line">
          {t('academics.evaluations.read.lastReason', {
            reason: evaluation.last_change_reason,
          })}
        </p>
      ) : null}
      <ol className="flex min-w-0 flex-col gap-3">
        {scores.map((score) => (
          <li
            key={score.position}
            className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3"
          >
            <p className="text-sm font-semibold break-words">
              {t('academics.evaluations.fields.criterion', {
                position: score.position,
                title: score.title,
              })}
            </p>
            <p className="text-sm">
              {score.score === null
                ? t('academics.evaluations.read.notScored', {
                    max: score.max_score,
                  })
                : t('academics.evaluations.read.score', {
                    score: score.score,
                    max: score.max_score,
                  })}
            </p>
            {score.comment ? (
              <p className="text-sm break-words whitespace-pre-line">
                {score.comment}
              </p>
            ) : null}
          </li>
        ))}
      </ol>
      <div className="flex min-w-0 flex-col gap-1">
        <h2 className="text-base font-semibold">
          {t('academics.evaluations.fields.feedback')}
        </h2>
        <p className="text-sm break-words whitespace-pre-line">
          {evaluation.feedback ?? t('academics.evaluations.read.noFeedback')}
        </p>
      </div>
    </div>
  );
}
