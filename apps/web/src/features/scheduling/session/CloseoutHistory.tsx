import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { SessionCloseoutRecord } from '@/features/tutor-portal/closeout/api';

// A closeout's history on the staff session page (#78 G1): the approval
// revisions (newest first, the one in force marked) and the staff
// corrections (fields changed, minutes before and after, the reason). Staff
// who don't see the lesson content (Finance) get the minutes and metadata
// and "Reason visible to academic reviewers" instead of the reason.

const FIELD_KEYS = {
  actual_minutes: 'closeout.actualMinutes',
  topics: 'closeout.fields.topics',
  performance: 'closeout.fields.performance',
  homework: 'closeout.fields.homework',
  next_steps: 'closeout.fields.next_steps',
} as const;

export function CloseoutHistory({
  record,
  timeZone,
}: {
  record: SessionCloseoutRecord;
  timeZone: string;
}) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  if (record.revisions.length === 0 && record.corrections.length === 0) {
    return null;
  }
  const when = (instant: string) =>
    formatInstant(instant, { timeZone, language, style: 'dateTime' });

  return (
    <section
      aria-labelledby={`${id}-history`}
      className="flex min-w-0 flex-col gap-2"
    >
      <h3 id={`${id}-history`} className="text-base font-semibold">
        {t('closeout.review.history.heading')}
      </h3>
      <ul className="flex min-w-0 flex-col gap-2 text-sm">
        {record.revisions.map((revision) => (
          <li
            key={revision.id}
            className="rounded-md border border-border p-2 break-words"
          >
            {t('closeout.review.history.revision', {
              revision: revision.revision,
              minutes: revision.approved_minutes,
              name: revision.approved_by.display_name,
              when: when(revision.approved_at),
            })}{' '}
            <span className="text-muted-foreground">
              ({t(`closeout.review.history.sources.${revision.source}`)})
            </span>
            {record.approval?.id === revision.id ? (
              <span className="font-medium">
                {' '}
                {t('closeout.review.history.current')}
              </span>
            ) : null}
          </li>
        ))}
        {record.corrections.map((correction) => (
          <li
            key={correction.id}
            className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-2 break-words"
          >
            <span className="font-medium">
              {t('closeout.review.history.correction', {
                name: correction.corrected_by.display_name,
                when: when(correction.created_at),
              })}
            </span>
            <span>
              {correction.fields.length === 0
                ? t('closeout.review.history.reapproval')
                : t('closeout.review.history.fields', {
                    fields: correction.fields
                      .map((field) => t(FIELD_KEYS[field]))
                      .join(', '),
                  })}
            </span>
            {correction.new_minutes !== null ? (
              <span>
                {t('closeout.review.history.minutes', {
                  previous: correction.previous_minutes ?? '—',
                  next: correction.new_minutes,
                })}
              </span>
            ) : null}
            <span className="whitespace-pre-line text-muted-foreground">
              {correction.reason === null
                ? t('closeout.review.history.reasonHidden')
                : t('closeout.review.history.reason', {
                    reason: correction.reason,
                  })}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
