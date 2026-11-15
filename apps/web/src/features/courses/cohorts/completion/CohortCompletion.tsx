import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import {
  COMPLETION_META,
  completionKey,
  fetchCohortCompletion,
  hidesCompletion,
  retryCompletion,
} from './api';
import { CompletionSection } from './CompletionSection';
import { MyProgressSection } from './MyProgressSection';
import type { CompletionAudience } from './rules';

// The cohort page's completion request and section (#323 W0, W4), mounted
// only once the cohort detail has loaded and only for a non-null
// audience: one GET /cohorts/{id}/completion under the cohort key prefix.
// A 403 or 404 renders nothing (expected, so never reported, and never
// retried); the roster shows its loading and errors inside its section;
// the family shows nothing until rows arrive.

export interface CohortCompletionProps {
  cohortId: string;
  audience: Exclude<CompletionAudience, null>;
  /** The family heading rule (rules.familyHeading). */
  familyHeading: 'mine' | 'named';
}

export function CohortCompletion({
  cohortId,
  audience,
  familyHeading,
}: CohortCompletionProps) {
  const { t } = useTranslation();
  const completion = useQuery({
    queryKey: completionKey(cohortId),
    queryFn: ({ signal }) => fetchCohortCompletion(cohortId, signal),
    retry: retryCompletion,
    meta: COMPLETION_META,
  });

  if (completion.isError && hidesCompletion(completion.error)) {
    return null;
  }
  const retry = () => void completion.refetch();

  if (audience === 'roster') {
    return (
      <CompletionSection
        state={
          completion.data
            ? { status: 'ready', data: completion.data }
            : completion.isError
              ? { status: 'error', error: completion.error, onRetry: retry }
              : { status: 'loading' }
        }
      />
    );
  }

  if (completion.data) {
    return <MyProgressSection data={completion.data} heading={familyHeading} />;
  }
  if (completion.isError) {
    // No row to name the section after: the roster's heading.
    return (
      <section className="flex min-w-0 flex-col gap-3">
        <h2 className="text-lg font-semibold">
          {t('courses.cohorts.completion.heading')}
        </h2>
        <ErrorAlert error={completion.error} onRetry={retry} />
      </section>
    );
  }
  return null;
}
