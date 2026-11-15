import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { useFormatters } from '@/lib/formatters';
import { BillingTabs } from '../BillingTabs';
import type { BillingSettings } from './api';

// The Settings tab (#366 B1, B3), without any query (stories render each
// state): the Billing tabs, the title, and the billing start date card:
// "Billing is off" or "Billing charges sessions from {date}", the rules
// (sessions before the date are never charged; turning billing off never
// removes posted charges) and, for billing.manage holders, "Change". The
// cards below it (the cancellation charges, #385) come as `children`. The
// page's save notices are announced in the status line under the cards.

export type BillingSettingsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; settings: BillingSettings };

export interface BillingSettingsViewProps {
  state: BillingSettingsState;
  /** billing.manage: shows "Change"; otherwise the card is read-only. */
  canManage: boolean;
  onChange: () => void;
  /** The last save's notice (a polite status message), or empty. */
  announcement?: string;
  /** The cards under the billing start date card. */
  children?: ReactNode;
}

export function BillingSettingsView({
  state,
  canManage,
  onChange,
  announcement = '',
  children,
}: BillingSettingsViewProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const ids = { card: `${id}-card` };

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={3} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const startsOn = state.settings.billing_starts_on;
    body = (
      <section
        aria-labelledby={ids.card}
        className="flex max-w-3xl flex-col gap-4 rounded-lg border border-border p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 id={ids.card} className="text-lg font-semibold">
            {t('billing.settings.card.title')}
          </h2>
          {canManage ? (
            <Button type="button" variant="outline" onClick={onChange}>
              {t('billing.settings.change')}
            </Button>
          ) : null}
        </div>
        <p className="font-medium" data-billing-status>
          {startsOn === null
            ? t('billing.settings.status.off')
            : t('billing.settings.status.on', {
                date: formatLocalDate(startsOn),
              })}
        </p>
        <p className="text-sm text-muted-foreground">
          {t('billing.settings.card.rules')}
        </p>
      </section>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('billing.settings.title')}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t('billing.settings.intro')}
        </p>
      </div>
      {body}
      {children}
      <p role="status" className="text-sm">
        {announcement}
      </p>
    </div>
  );
}
