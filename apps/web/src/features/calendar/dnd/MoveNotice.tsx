import { CircleCheck, TriangleAlert } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { useWarningMessage } from '@/features/scheduling/create/warningMessages';
import type { LabelContext } from '../labels';
import { moveAnnouncement } from './messages';
import type { MoveNotice } from './useMoveSession';
import { invalidMoveMessage } from './rules';

// What the last move did (#58 D6, F3), above the calendar:
// - moved: a polite status ("Moved to …", "Moved with an override", "Only
//   this occurrence was moved…") and, when the tutor's workload is over a
//   limit, a non-blocking list of the warnings;
// - refused: the shared ErrorAlert with the right message; a version
//   conflict has a Reload button (no automatic retry);
// - after Reload: "Calendar reloaded", a status that takes the focus (the
//   Reload button is gone).
// A notice from a dialog's refusal takes the focus (`focus`), so it is not
// lost when the dialog closes.

export function MoveNoticeView({
  notice,
  context,
  onReload,
  noticeRef,
}: {
  notice: MoveNotice | null;
  context: LabelContext;
  onReload: () => void;
  /** The focusable notice (the calendar focuses it after a dialog). */
  noticeRef?: React.RefObject<HTMLDivElement | null>;
}) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const warning = useWarningMessage();
  const own = useRef<HTMLDivElement | null>(null);
  const ref = noticeRef ?? own;

  useEffect(() => {
    if (
      notice?.kind === 'reloaded' ||
      (notice?.kind === 'failed' && notice.focus)
    ) {
      // After a closing dialog has handled its own focus.
      const timer = window.setTimeout(() => ref.current?.focus(), 0);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [notice, ref]);

  const moved = notice?.kind === 'moved' ? notice : null;
  const warnings = moved?.session.warnings ?? [];

  return (
    <div
      ref={ref}
      tabIndex={-1}
      className="flex flex-col gap-3 outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      <p role="status" aria-live="polite" className="text-sm empty:hidden">
        {moved ? (
          <span className="flex items-start gap-2">
            <CircleCheck
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0"
            />
            <span>{moveAnnouncement(moved, context)}</span>
          </span>
        ) : notice?.kind === 'reloaded' ? (
          t('scheduling.calendar.move.reloaded')
        ) : null}
      </p>
      {warnings.length > 0 ? (
        <div className="flex gap-3 rounded-md border border-border bg-muted p-3 text-sm">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0"
          />
          <div className="flex min-w-0 flex-col gap-1">
            <p className="font-medium">{t('scheduling.warnings.heading')}</p>
            <ul className="flex list-disc flex-col gap-1 pl-5">
              {warnings.map((item, index) => (
                <li key={`${item.code}-${index}`} className="break-words">
                  {warning(item)}
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground">
              {t('scheduling.warnings.intro')}
            </p>
          </div>
        </div>
      ) : null}
      {notice?.kind === 'failed' ? (
        notice.refusal === 'stale' ? (
          <ErrorAlert
            error={notice.error}
            message={t('scheduling.calendar.move.stale')}
            onRetry={onReload}
            retryLabel={t('scheduling.calendar.move.reload')}
          />
        ) : notice.refusal === 'invalid' ? (
          <ErrorAlert
            error={notice.error}
            title={t('scheduling.calendar.move.notSaved')}
            message={tErrors(invalidMoveMessage(notice.error))}
          />
        ) : (
          <ErrorAlert
            error={notice.error}
            title={t('scheduling.calendar.move.notSaved')}
          />
        )
      ) : null}
    </div>
  );
}
