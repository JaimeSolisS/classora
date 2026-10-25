import { useTranslation } from 'react-i18next';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { AttendanceCorrection, AttendanceStatus } from './api';

// A row's correction history (#76 E3), oldest first: from and to (as text),
// who corrected, when, and the reason. Shared by the tutor's page and the
// staff session page.

export function CorrectionHistory({
  corrections,
  timeZone,
}: {
  corrections: readonly AttendanceCorrection[];
  timeZone: string;
}) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  if (corrections.length === 0) {
    return null;
  }
  const label = (status: AttendanceStatus | null) =>
    status ? t(`attendance.statuses.${status}`) : t('attendance.notRecorded');
  return (
    <details className="text-sm">
      <summary className="cursor-pointer underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring">
        {t('attendance.history', { count: corrections.length })}
      </summary>
      <ol className="mt-2 flex flex-col gap-2">
        {corrections.map((correction) => (
          <li key={correction.id} className="flex min-w-0 flex-col gap-0.5">
            <span className="font-medium">
              {t('attendance.change', {
                from: label(correction.previous_status),
                to: label(correction.new_status),
              })}
            </span>
            <span className="break-words">
              {t('attendance.correctedBy', {
                name: correction.corrected_by.display_name,
                when: formatInstant(correction.created_at, {
                  timeZone,
                  language,
                  style: 'dateTime',
                }),
              })}
            </span>
            <span className="break-words">
              {t('attendance.reason', { reason: correction.reason })}
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}
