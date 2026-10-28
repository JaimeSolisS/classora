import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
import type { PriceVersion } from './api';
import { usePriceLabels } from './labels';

// An item's versions (#363 D2), newest first as the API sends them: the
// local date it takes effect, the price or "Not offered", a status badge
// (the API's status against the center's today) and "Delete" on upcoming
// rows only. Presentational: the page confirms and deletes.

export function PriceStatusBadge({
  status,
}: {
  status: PriceVersion['status'];
}) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="price-status-badge"
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground',
        status === 'current'
          ? 'border-foreground bg-background'
          : status === 'upcoming'
            ? 'border-dashed border-border bg-background'
            : 'border-border bg-muted',
      )}
    >
      {t(`billing.prices.status.${status}`)}
    </span>
  );
}

interface PriceVersionsTableProps {
  versions: readonly PriceVersion[];
  /** The id of the heading that names the table. */
  labelledBy: string;
  onDelete: (version: PriceVersion) => void;
}

export function PriceVersionsTable({
  versions,
  labelledBy,
  onDelete,
}: PriceVersionsTableProps) {
  const { t } = useTranslation();
  const labels = usePriceLabels();
  if (versions.length === 0) {
    return <p>{t('billing.prices.versions.empty')}</p>;
  }
  return (
    <Table aria-labelledby={labelledBy}>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">
            {t('billing.prices.versions.effectiveFrom')}
          </TableHead>
          <TableHead scope="col">
            {t('billing.prices.versions.price')}
          </TableHead>
          <TableHead scope="col">
            {t('billing.prices.versions.status')}
          </TableHead>
          <TableHead scope="col">
            <span className="sr-only">
              {t('billing.prices.versions.actions')}
            </span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {versions.map((version) => {
          const date = labels.date(version);
          return (
            <TableRow key={version.id} data-version-id={version.id}>
              <TableCell>{date}</TableCell>
              <TableCell>{labels.amount(version)}</TableCell>
              <TableCell>
                <PriceStatusBadge status={version.status} />
              </TableCell>
              <TableCell className="text-right">
                {version.status === 'upcoming' ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={t('billing.prices.versions.deleteFor', {
                      date,
                    })}
                    onClick={() => onDelete(version)}
                  >
                    {t('billing.prices.versions.delete')}
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
