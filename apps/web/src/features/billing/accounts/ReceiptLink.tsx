import { useTranslation } from 'react-i18next';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { receiptUrl, type ReceiptReference } from './api';

// A payment's receipt (#98 H1): its number and a "Download receipt" link to
// the PDF route (`download`, so the browser saves `<number>.pdf`). A payment
// recorded before receipts has none: "—" and no link.

export function ReceiptLink({
  familyId,
  receipt,
}: {
  familyId: string;
  receipt: ReceiptReference | null;
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
        className={CATALOG_LINK_CLASS}
        aria-label={t('billing.accounts.receipt.downloadFor', {
          number: receipt.number,
        })}
      >
        {t('billing.accounts.receipt.download')}
      </a>
    </span>
  );
}
