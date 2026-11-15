import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { SubstitutionItem } from './api';

// The substitutions of a session (#55 H1), presentational: the pending one
// (the substitute and the deadline; "Withdraw substitution" for holders of
// schedule.assign, after a confirmation) and the history (original →
// substitute, the status as text, the reason, who asked and when, the
// answer or deadline, and the decline note). Nothing is rendered for a
// session that never had a substitution.

export interface SubstitutionSectionProps {
  items: readonly SubstitutionItem[];
  timeZone: string;
  /** Whether the user may withdraw the pending substitution. */
  canWithdraw: boolean;
  /** Withdraws; rejects with the error to show. */
  onWithdraw: (item: SubstitutionItem) => Promise<void>;
  /** An error to start with (stories). */
  initialError?: unknown;
}

const STATUS_KEYS = {
  pending: 'offers.status.pending',
  accepted: 'offers.status.accepted',
  declined: 'offers.status.declined',
  expired: 'offers.status.expired',
  withdrawn: 'offers.status.withdrawn',
} as const;

export function SubstitutionSection({
  items,
  timeZone,
  canWithdraw,
  onWithdraw,
  initialError = null,
}: SubstitutionSectionProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const [confirming, setConfirming] = useState(false);
  const [failure, setFailure] = useState<unknown>(initialError);
  const when = (instant: string) =>
    formatInstant(instant, { timeZone, language, style: 'dateTime' });

  if (items.length === 0) {
    return null;
  }
  const pending = items.find((item) => item.status === 'pending') ?? null;

  async function withdraw() {
    if (!pending) {
      return;
    }
    setFailure(null);
    try {
      await onWithdraw(pending);
    } catch (caught) {
      setFailure(caught);
    } finally {
      setConfirming(false);
    }
  }

  return (
    <section aria-labelledby={`${id}-heading`} className="flex flex-col gap-3">
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('scheduling.substitution.historyHeading')}
      </h2>
      {failure ? <ErrorAlert error={failure} /> : null}
      {pending ? (
        <div className="flex flex-col gap-2 rounded-md border border-border p-3">
          <p className="font-medium break-words">
            {t('scheduling.substitution.pendingText', {
              tutor: pending.substitute_tutor.display_name,
              when: when(pending.respond_by),
            })}
          </p>
          <p className="text-sm text-muted-foreground">
            {t('scheduling.substitution.pendingHelp', {
              tutor: pending.original_tutor.display_name,
            })}
          </p>
          {canWithdraw ? (
            <div>
              <Button
                type="button"
                variant="outline"
                onClick={() => setConfirming(true)}
              >
                {t('scheduling.substitution.withdraw')}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
      <ol className="flex flex-col gap-3">
        {items.map((item) => (
          <li
            key={item.id}
            className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-sm"
          >
            <p className="flex flex-wrap items-center gap-2">
              <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-xs font-medium">
                {t(STATUS_KEYS[item.status])}
              </span>
              <span className="font-medium break-words">
                {t('scheduling.substitution.change', {
                  from: item.original_tutor.display_name,
                  to: item.substitute_tutor.display_name,
                })}
              </span>
            </p>
            <p className="break-words">
              {t('scheduling.substitution.reasonText', { reason: item.reason })}
            </p>
            <p className="break-words">
              {t('scheduling.session.offeredBy', {
                name: item.requested_by.display_name,
                when: when(item.requested_at),
              })}
            </p>
            <p>
              {item.responded_at
                ? t('scheduling.session.respondedAt', {
                    when: when(item.responded_at),
                  })
                : t('scheduling.session.respondBy', {
                    when: when(item.respond_by),
                  })}
            </p>
            {item.decline_note ? (
              <p className="break-words text-muted-foreground">
                {t('offers.card.note', { note: item.decline_note })}
              </p>
            ) : null}
          </li>
        ))}
      </ol>
      <ConfirmDialog
        open={confirming}
        title={t('scheduling.substitution.withdrawTitle')}
        body={
          pending
            ? t('scheduling.substitution.withdrawBody', {
                tutor: pending.substitute_tutor.display_name,
              })
            : ''
        }
        confirmLabel={t('scheduling.substitution.withdraw')}
        onConfirm={withdraw}
        onCancel={() => setConfirming(false)}
      />
    </section>
  );
}
