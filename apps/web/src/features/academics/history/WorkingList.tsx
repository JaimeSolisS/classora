import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { SELECT_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { evaluationPath, type Evaluation } from '../evaluations/api';
import { StatusBadge } from '../evaluations/EvaluationDetail';
import type { WorkingStatus } from './api';

// The "Evaluations" tab (#308 E8, E2), staff and tutors: the working list
// of a student's evaluations (GET /students/{id}/evaluations, newest
// first): each row has its status as text, the template (a link to the
// evaluation), the subject, the date, the author and, once final, the
// total and percentage as the API sent them. A status filter offers All
// and Final, plus Drafts for tutors and Voided for evaluation.manage
// holders. "Load more" follows the cursor (20 per page).

export type WorkingState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly Evaluation[];
      hasMore: boolean;
      loadingMore: boolean;
      onLoadMore: () => void;
    };

export interface WorkingListProps {
  state: WorkingState;
  status: WorkingStatus | null;
  onStatus: (status: WorkingStatus | null) => void;
  /** The statuses this user may filter by (besides All). */
  statuses: readonly WorkingStatus[];
}

export function WorkingList({
  state,
  status,
  onStatus,
  statuses,
}: WorkingListProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();

  let content;
  if (state.status === 'loading') {
    content = <LoadingState rows={3} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.items.length === 0) {
    content = (
      <p role="status" className="text-sm">
        {t('academics.history.working.empty')}
      </p>
    );
  } else {
    content = (
      <>
        <ul className="flex min-w-0 flex-col gap-3">
          {state.items.map((evaluation) => {
            const date = formatLocalDate(evaluation.evaluated_on);
            return (
              <li
                key={evaluation.id}
                className="flex min-w-0 flex-col gap-1 rounded-lg border border-border p-3 text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge evaluation={evaluation} />
                  <Link
                    to={evaluationPath(evaluation.id)}
                    className={`${TEXT_LINK_CLASS} break-words`}
                    aria-label={t('academics.history.working.open', {
                      name: evaluation.template.name,
                      date,
                    })}
                  >
                    {evaluation.template.name}
                  </Link>
                </div>
                <p className="break-words">
                  {t('academics.history.working.meta', {
                    subject: evaluation.subject.name,
                    date,
                    name: evaluation.author.display_name,
                  })}
                </p>
                {evaluation.status === 'final' &&
                evaluation.score_total !== null ? (
                  <p className="font-medium">
                    {t('academics.history.entry.total', {
                      total: evaluation.score_total,
                      max: evaluation.max_total,
                      percent: evaluation.score_percent,
                    })}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
        {state.hasMore ? (
          <div>
            <Button
              type="button"
              variant="outline"
              disabled={state.loadingMore}
              onClick={state.onLoadMore}
            >
              {state.loadingMore ? (
                <Spinner
                  label={t('academics.history.working.loadingMore')}
                  showLabel
                />
              ) : (
                t('academics.history.working.loadMore')
              )}
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex min-w-0 flex-col gap-2 sm:max-w-xs">
        <label htmlFor={`${id}-status`} className="text-sm font-medium">
          {t('academics.history.working.status')}
        </label>
        <select
          id={`${id}-status`}
          className={SELECT_CLASS}
          value={status ?? ''}
          onChange={(event) =>
            onStatus(
              statuses.find((value) => value === event.target.value) ?? null,
            )
          }
        >
          <option value="">{t('academics.history.working.all')}</option>
          {statuses.map((value) => (
            <option key={value} value={value}>
              {t(`academics.history.working.statuses.${value}`)}
            </option>
          ))}
        </select>
      </div>
      {content}
    </div>
  );
}
