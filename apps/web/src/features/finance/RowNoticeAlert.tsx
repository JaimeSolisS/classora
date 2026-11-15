import { useTranslation } from 'react-i18next';
import type { RowNotice } from './useRowSave';

// What a category, vendor or template dialog says after a reload (M7-11b B5, D2), in
// the row kind's words. Each "reloaded" text shows only when the list did.

export function RowNoticeAlert({
  notice,
  kind,
}: {
  notice: RowNotice | null;
  kind: 'categories' | 'vendors' | 'templates';
}) {
  const { t } = useTranslation();
  if (!notice) {
    return null;
  }
  return (
    <p role="alert" className="text-sm font-medium" data-row-notice={notice}>
      {t(`finance.${kind}.dialog.notices.${notice}`)}
    </p>
  );
}
