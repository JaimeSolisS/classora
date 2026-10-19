import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

// Previous / Next and "{{from}}–{{to}} of {{total}}" under a catalog table
// (#32 H2), with the #31 strings.

interface PaginationProps {
  offset: number;
  limit: number;
  shown: number;
  total: number;
  /** Goes to another 1-based page. */
  onPage: (page: number) => void;
}

export function Pagination({
  offset,
  limit,
  shown,
  total,
  onPage,
}: PaginationProps) {
  const { t } = useTranslation();
  const page = Math.floor(offset / limit) + 1;
  return (
    <nav
      aria-label={t('users.list.pagination')}
      className="flex flex-wrap items-center gap-3"
    >
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={offset === 0}
        onClick={() => onPage(page - 1)}
      >
        {t('users.list.previous')}
      </Button>
      <p className="text-sm">
        {t('users.list.range', {
          from: offset + 1,
          to: offset + shown,
          total,
        })}
      </p>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={offset + shown >= total}
        onClick={() => onPage(page + 1)}
      >
        {t('users.list.next')}
      </Button>
    </nav>
  );
}
