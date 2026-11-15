import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { occurrenceCodeName } from '@/features/approvals/summary';
import { toLanguage } from '@/i18n/language';
import { formatInstant, formatLocalDate, toLocalDate } from '@/lib/time';
import { conflictsOf, type SchedulingConflict } from '../create/api';
import { useConflictMessage } from '../create/conflictMessages';
import { OverrideDialog } from '../create/OverrideDialog';
import { orderedConflicts } from '../create/rules';
import type { SeriesOccurrence } from './api';
import { useReturnFocus } from './hooks';
import { SOFT_CODES } from './overrides';
import { isVersionConflict } from './rules';

// "Override and schedule" for one `conflict` row of the series page (#236
// H3): the #49 dialog for that occurrence, listing its soft codes under its
// local date and time, with the reason. The page sends POST
// /class-sessions/{id}/conflict-overrides with the row's version. A new
// 409 scheduling_conflict shows the new list here (its message above it);
// version_conflict, session_not_in_conflict and session_not_editable show
// their translated message with "Reload" (the row changed); a 422 on the
// reason goes on the field. The focus returns to the row's button.

const RELOAD_CODES = new Set([
  'session_not_in_conflict',
  'session_not_editable',
]);

function needsReload(error: unknown): boolean {
  return (
    isVersionConflict(error) ||
    (error instanceof ApiError && RELOAD_CODES.has(error.code ?? ''))
  );
}

export interface ConflictOccurrenceDialogProps {
  /** The row being overridden; null: closed. */
  occurrence: SeriesOccurrence | null;
  timeZone: string;
  /** Sends the override; resolves once the dialog may close. */
  onSubmit: (reason: string) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** The reason to start from (stories). */
  initialReason?: string;
}

export function ConflictOccurrenceDialog({
  occurrence,
  timeZone,
  onSubmit,
  onCancel,
  onReload,
  initialReason,
}: ConflictOccurrenceDialogProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const message = useConflictMessage();
  const returnFocus = useReturnFocus();
  const [found, setFound] = useState<{
    id: string;
    conflicts: SchedulingConflict[];
  } | null>(null);
  const conflicts =
    occurrence && found?.id === occurrence.id ? found.conflicts : null;

  async function confirm(reason: string) {
    try {
      await onSubmit(reason);
    } catch (caught) {
      const list = conflictsOf(caught);
      if (list && occurrence) {
        setFound({ id: occurrence.id, conflicts: list });
      }
      throw caught;
    }
  }

  const when = occurrence
    ? `${formatLocalDate(toLocalDate(occurrence.starts_at, timeZone), {
        language,
        style: 'long',
      })} · ${formatInstant(occurrence.starts_at, {
        timeZone,
        language,
        style: 'time',
      })}`
    : '';

  return (
    <OverrideDialog
      open={occurrence !== null}
      conflicts={[]}
      initialReason={initialReason}
      onConfirm={confirm}
      onCancel={onCancel}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      reload={{ when: needsReload, onReload }}
      labels={{
        title: t('scheduling.override.title'),
        description: t('scheduling.series.override.rowDescription'),
        submit: t('scheduling.override.submit'),
        submitting: t('scheduling.override.submitting'),
      }}
      rules={
        <div className="flex flex-col gap-1">
          <h4 className="text-sm font-medium">{when}</h4>
          {conflicts ? (
            <ul className="flex flex-col gap-1 text-sm">
              {orderedConflicts(conflicts).map((conflict, index) => (
                <li
                  key={`${conflict.code}-${index}`}
                  className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2"
                >
                  <span className="w-fit shrink-0 rounded-sm border border-border px-1.5 text-xs font-medium">
                    {conflict.severity === 'hard'
                      ? t('scheduling.severity.hard')
                      : t('scheduling.severity.soft')}
                  </span>
                  <span className="min-w-0 break-words">
                    {message(conflict)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
              {(occurrence?.conflict_codes ?? [])
                .filter((code) => SOFT_CODES.has(code))
                .map((code) => (
                  <li key={code} className="break-words">
                    {occurrenceCodeName(code, t)}
                  </li>
                ))}
            </ul>
          )}
        </div>
      }
    />
  );
}
