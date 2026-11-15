import type { Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Button } from '@/components/ui/button';
import { SMALL_LINK_CLASS } from '../styles';
import type { FormCriterion, Refusal } from './rules';

// The failure region of the evaluation pages (#308 C5, C8), focusable so
// focus can move to it after a refused write: "This evaluation changed
// elsewhere." with Reload, a stale page or a changed state with its message
// and Reload, a class that can't be evaluated with the link to the form
// without it, the missing criteria of a blocked Finalize, or the error's
// message.

export interface FailureState {
  refusal: Refusal;
  error: unknown;
}

interface FailureProps {
  failure: FailureState;
  onReload: () => void;
  /** The form without the session (the "can't be evaluated" link). */
  withoutSession?: string;
  /** The criteria named by a blocked Finalize. */
  criteria?: readonly FormCriterion[];
  ref?: Ref<HTMLDivElement>;
}

export function Failure({
  failure,
  onReload,
  withoutSession,
  criteria = [],
  ref,
}: FailureProps) {
  const { t } = useTranslation();
  const { refusal, error } = failure;
  let content;
  if (refusal.kind === 'conflict') {
    content = (
      <div
        role="alert"
        className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive p-3 text-sm text-destructive"
      >
        <span>{t('academics.evaluations.conflict')}</span>
        <Button type="button" size="sm" variant="outline" onClick={onReload}>
          {t('academics.reload')}
        </Button>
      </div>
    );
  } else if (refusal.kind === 'incomplete') {
    const missing = criteria.filter((criterion) =>
      refusal.positions.includes(criterion.position),
    );
    content = (
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-lg border border-destructive p-3 text-sm text-destructive"
      >
        <p className="font-medium">{t('academics.evaluations.missing')}</p>
        <ul className="list-disc pl-5">
          {missing.map((criterion) => (
            <li key={criterion.position} className="break-words">
              {t('academics.evaluations.fields.criterion', {
                position: criterion.position,
                title: criterion.title,
              })}
            </li>
          ))}
        </ul>
      </div>
    );
  } else if (refusal.kind === 'session') {
    content = (
      <div
        role="alert"
        className="flex flex-col items-start gap-2 rounded-lg border border-destructive p-3 text-sm"
      >
        <p className="font-medium text-destructive">
          {t('academics.evaluations.notEvaluable')}
        </p>
        {withoutSession ? (
          <Link to={withoutSession} className={SMALL_LINK_CLASS}>
            {t('academics.evaluations.withoutSession')}
          </Link>
        ) : null}
      </div>
    );
  } else if (
    refusal.kind === 'state' ||
    refusal.kind === 'stale' ||
    refusal.kind === 'forbidden'
  ) {
    content = (
      <ErrorAlert
        error={error}
        retryLabel={t('academics.reload')}
        onRetry={onReload}
      />
    );
  } else {
    content = <ErrorAlert error={error} />;
  }
  return (
    <div ref={ref} tabIndex={-1} className="outline-none">
      {content}
    </div>
  );
}
