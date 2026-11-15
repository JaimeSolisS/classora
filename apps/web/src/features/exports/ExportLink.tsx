import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useSession } from '@/features/auth/useSession';
import { mayExport, type ExportDatasetCode } from './access';

// The "Export" link of a list page's header (M8-07b A3, E1): /students,
// /schedule and /expenses/records. It opens /exports with the page's
// dataset preselected, and shows only to a user who may export it (the
// API's rule, `access.ts`). A 44 px target on a phone.

export const EXPORT_LINK_CLASS =
  'inline-flex min-h-11 items-center rounded-md border border-border px-2.5 text-sm font-medium outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring md:min-h-9';

export function ExportLink({ dataset }: { dataset: ExportDatasetCode }) {
  const { t } = useTranslation();
  const session = useSession();
  if (
    !mayExport(session.status === 'signedIn' ? session.user : null, dataset)
  ) {
    return null;
  }
  return (
    <Link
      to={`/exports?dataset=${dataset}`}
      className={EXPORT_LINK_CLASS}
      data-export-link={dataset}
    >
      {t('exports.exportLink')}
    </Link>
  );
}
