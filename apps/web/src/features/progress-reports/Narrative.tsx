import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { ProgressReport } from './api';
import { historyEntries, NARRATIVE_FIELDS, type NarrativeField } from './rules';

// A report's narrative read as plain text (#315 C6, E2, F1): each written
// section under its heading (whitespace-pre-line, long words wrap); empty
// optional sections are left out. And the review history (C8), built from
// R3's fields, in time order.

const LABELS = {
  summary: 'progressReports.fields.summary',
  strengths: 'progressReports.fields.strengths',
  areas_for_improvement: 'progressReports.fields.areas',
  next_steps: 'progressReports.fields.nextSteps',
} as const;

export function NarrativeView({
  report,
}: {
  report: Record<NarrativeField, string | null>;
}) {
  const { t } = useTranslation();
  const written = NARRATIVE_FIELDS.filter((field) => report[field]);
  if (written.length === 0) {
    return <p className="text-sm">{t('progressReports.noNarrative')}</p>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {written.map((field) => (
        <section key={field} className="flex min-w-0 flex-col gap-1">
          <h2 className="text-base font-semibold">{t(LABELS[field])}</h2>
          <p className="text-sm break-words whitespace-pre-line">
            {report[field]}
          </p>
        </section>
      ))}
    </div>
  );
}

export function ReportHistory({
  report,
}: {
  report: Pick<
    ProgressReport,
    'submitted_at' | 'changes_requested' | 'approved' | 'retracted'
  >;
}) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const entries = historyEntries(report);
  if (entries.length === 0) {
    return null;
  }
  return (
    <section className="flex min-w-0 flex-col gap-2">
      <h2 className="text-lg font-semibold">
        {t('progressReports.history.title')}
      </h2>
      <ol className="flex min-w-0 flex-col gap-2 text-sm">
        {entries.map((entry) => {
          const at = formatInstant(entry.at, 'dateTime');
          let text: string;
          if (entry.kind === 'submitted') {
            text = t('progressReports.history.submitted', { at });
          } else if (entry.kind === 'changes') {
            text = t('progressReports.history.changes', {
              at,
              by: entry.by,
              reason: entry.reason,
            });
          } else if (entry.kind === 'approved') {
            text = t('progressReports.history.approved', { at, by: entry.by });
          } else {
            text = t('progressReports.history.retracted', {
              at,
              by: entry.by,
              reason: entry.reason,
            });
          }
          return (
            <li
              key={`${entry.kind}-${entry.at}`}
              className="break-words whitespace-pre-line"
            >
              {text}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
