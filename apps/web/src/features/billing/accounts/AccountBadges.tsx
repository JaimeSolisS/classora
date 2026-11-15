import { useTranslation } from 'react-i18next';

// Text badges of the account page (#379 C4): a reversed payment and a
// released allocation (never color alone), on the PriceStatusBadge style.

const BADGE =
  'inline-flex items-center rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

export function ReversedBadge() {
  const { t } = useTranslation();
  return (
    <span data-slot="payment-reversed-badge" className={BADGE}>
      {t('billing.accounts.payments.reversed')}
    </span>
  );
}

export function ReleasedBadge() {
  const { t } = useTranslation();
  return (
    <span data-slot="allocation-released-badge" className={BADGE}>
      {t('billing.accounts.payments.released')}
    </span>
  );
}
