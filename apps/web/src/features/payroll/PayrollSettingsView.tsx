import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { useFormatters } from '@/lib/formatters';
import type { PayrollSettings } from './api';
import { PayrollTabs } from './PayrollTabs';

// The Settings tab (#425 D), without any query (stories render each state):
// the Payroll tabs, the title and a card with the start date ("Payroll is
// off" or "Payroll starts on {date}"), the biweekly anchor and the salary
// proration basis, each with its rule. A locked field says periods already
// exist. payroll.manage holders get "Change".

export type PayrollSettingsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; settings: PayrollSettings };

export interface PayrollSettingsViewProps {
  state: PayrollSettingsState;
  /** payroll.manage: shows "Change"; otherwise the card is read-only. */
  canManage: boolean;
  onChange: () => void;
  /** The last save's notice (a polite status message), or empty. */
  announcement?: string;
}

export function PayrollSettingsView({
  state,
  canManage,
  onChange,
  announcement = '',
}: PayrollSettingsViewProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const cardId = `${id}-card`;

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={3} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { settings } = state;
    const startsOn = settings.payroll_starts_on;
    body = (
      <section
        aria-labelledby={cardId}
        className="flex max-w-3xl flex-col gap-4 rounded-lg border border-border p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 id={cardId} className="text-lg font-semibold">
            {t('payroll.settings.card')}
          </h2>
          {canManage ? (
            <Button
              type="button"
              variant="outline"
              className="min-h-11 md:min-h-9"
              onClick={onChange}
            >
              {t('payroll.settings.change')}
            </Button>
          ) : null}
        </div>
        <p className="font-medium" data-payroll-status>
          {startsOn === null
            ? t('payroll.settings.off')
            : t('payroll.settings.on', { date: formatLocalDate(startsOn) })}
        </p>
        <p className="text-sm text-muted-foreground">
          {t('payroll.settings.startHelp')}
        </p>
        {settings.start_locked ? (
          <p className="text-sm" data-payroll-locked="start">
            {t('payroll.settings.startLocked')}
          </p>
        ) : null}
        <dl className="flex flex-col gap-3 text-sm">
          <div className="flex flex-col gap-1">
            <dt className="font-medium">{t('payroll.settings.anchor')}</dt>
            <dd>{formatLocalDate(settings.biweekly_anchor_on)}</dd>
            <dd className="text-muted-foreground">
              {t('payroll.settings.anchorHelp')}
            </dd>
            {settings.anchor_locked ? (
              <dd data-payroll-locked="anchor">
                {t('payroll.settings.anchorLocked')}
              </dd>
            ) : null}
          </div>
          <div className="flex flex-col gap-1">
            <dt className="font-medium">{t('payroll.settings.basis')}</dt>
            <dd data-payroll-basis>
              {t(`payroll.settings.bases.${settings.salary_proration_basis}`)}
            </dd>
            <dd className="text-muted-foreground">
              {t('payroll.settings.basisHelp')}
            </dd>
          </div>
        </dl>
      </section>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <PayrollTabs />
      <h1 className="text-2xl font-semibold">{t('payroll.settings.title')}</h1>
      {body}
      <p role="status" className="text-sm">
        {announcement}
      </p>
    </div>
  );
}
