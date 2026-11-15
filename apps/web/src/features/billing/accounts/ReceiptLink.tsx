import { useTranslation } from 'react-i18next';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { cn } from '@/lib/utils';
import { receiptUrl, type ReceiptReference } from './api';

// A payment's receipt (#98 H1): its number and a "Download receipt" link to
// the PDF route (`download`, so the browser saves `<number>.pdf`). A payment
// recorded before receipts has none: "—" and no link. The family portal
// (#388 E2) passes a `linkClassName` for its 44 px touch target.

export function ReceiptLink({
  familyId,
  receipt,
  linkClassName,
}: {
  familyId: string;
  receipt: ReceiptReference | null;
  /** Classes added to the link (the family portal's touch target). */
  linkClassName?: string;
}) {
  const { t } = useTranslation();
  if (receipt === null) {
    return <span>—</span>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2">
      <span className="tabular-nums">{receipt.number}</span>
      <a
        href={receiptUrl(familyId, receipt.id)}
        download={`${receipt.number}.pdf`}
        className={cn(CATALOG_LINK_CLASS, linkClassName)}
        aria-label={t('billing.accounts.receipt.downloadFor', {
          number: receipt.number,
        })}
      >
        {t('billing.accounts.receipt.download')}
      </a>
    </span>
  );
}
