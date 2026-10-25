import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { SessionCloseoutView } from '@/features/tutor-portal/closeout/api';
import { SummaryList } from '@/features/tutor-portal/closeout/CloseoutFlow';

// The session's closeout on the staff session page (#77 G3), read-only:
// for a completed session the status badge ("Submitted for review"), the
// actual vs scheduled minutes with the difference as text, who submitted it
// and when, and the summary (or "Summary visible to academic reviewers"
// when it is hidden, E5); "No closeout recorded" for a completed session
// without one. A started confirmed session reads "Waiting for the tutor's
// closeout". No review actions (#78). The page renders it for completed
// and started confirmed sessions only.

export type CloseoutSectionState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; closeout: SessionCloseoutView };

export interface CloseoutSectionProps {
  state: CloseoutSectionState;
  timeZone: string;
}

export function CloseoutSection({ state, timeZone }: CloseoutSectionProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={2} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { closeout } = state;
    const record = closeout.closeout;
    if (closeout.state === 'confirmed') {
      body = <p className="text-sm">{t('closeout.section.waiting')}</p>;
    } else if (!record) {
      body = <p className="text-sm">{t('closeout.noCloseout')}</p>;
    } else {
      const actual = record.actual_minutes ?? 0;
      const difference = actual - closeout.scheduled_minutes;
      body = (
        <div className="flex min-w-0 flex-col gap-3">
          <p>
            <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-sm font-medium">
              {t('closeout.submitted')}
            </span>
          </p>
          <dl className="grid gap-2 text-sm sm:grid-cols-[12rem_1fr]">
            <dt className="font-medium">{t('closeout.actualMinutes')}</dt>
            <dd>
              {t('closeout.minutesOfScheduled', {
                actual,
                scheduled: closeout.scheduled_minutes,
              })}{' '}
              <span className="text-muted-foreground">
                (
                {difference === 0
                  ? t('closeout.section.asScheduled')
                  : difference > 0
                    ? t('closeout.section.more', { count: difference })
                    : t('closeout.section.less', { count: -difference })}
                )
              </span>
            </dd>
            <dt className="font-medium">{t('closeout.section.submittedBy')}</dt>
            <dd>
              {t('closeout.section.byWhen', {
                name: record.submitted_by.display_name,
                when: formatInstant(record.submitted_at, {
                  timeZone,
                  language,
                  style: 'dateTime',
                }),
              })}
            </dd>
          </dl>
          {record.summary_hidden || !record.summary ? (
            <p className="text-sm">{t('closeout.summaryHidden')}</p>
          ) : (
            <SummaryList summary={record.summary} />
          )}
        </div>
      );
    }
  }

  return (
    <section
      aria-labelledby={`${id}-closeout`}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2 id={`${id}-closeout`} className="text-lg font-semibold">
        {t('closeout.section.heading')}
      </h2>
      {body}
    </section>
  );
}
