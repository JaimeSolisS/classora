import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useFormatters } from '@/lib/formatters';
import { roleNameKey } from '../roles';
import type { InvitationListItem, InvitationPage } from './api';

// The open invitations (#29 I5): username, display name, role, status and a
// "New link" button per row, with previous/next pages and the empty,
// loading and error states. Presentational: the page passes the query state
// and the actions.

export type InvitationsListState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: InvitationPage };

interface InvitationsListProps {
  state: InvitationsListState;
  /** Whether the user may issue a new link for this row (UX only). */
  canReissue: (item: InvitationListItem) => boolean;
  onReissue: (item: InvitationListItem) => void;
  /** The user id of the row whose new link is being requested. */
  reissuing?: string | null;
  onPage: (offset: number) => void;
}

export function InvitationsList({
  state,
  canReissue,
  onReissue,
  reissuing = null,
  onPage,
}: InvitationsListProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const id = useId();
  const titleId = `${id}-title`;

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={3} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0) {
    content = <p>{t('invitations.list.empty')}</p>;
  } else {
    const { items, total, limit, offset } = state.page;
    content = (
      <>
        <Table aria-labelledby={titleId}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('invitations.list.username')}
              </TableHead>
              <TableHead scope="col">
                {t('invitations.list.displayName')}
              </TableHead>
              <TableHead scope="col">{t('invitations.list.role')}</TableHead>
              <TableHead scope="col">{t('invitations.list.status')}</TableHead>
              <TableHead scope="col">{t('invitations.list.actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.user_id}>
                <TableCell>{item.username}</TableCell>
                <TableCell>{item.display_name}</TableCell>
                <TableCell>
                  {item.roles.map((role) => t(roleNameKey(role))).join(', ')}
                </TableCell>
                <TableCell>
                  {item.status === 'active'
                    ? t('invitations.list.activeUntil', {
                        date: formatInstant(item.expires_at, 'dateTime'),
                      })
                    : t('invitations.list.expired')}
                </TableCell>
                <TableCell>
                  {canReissue(item) ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={reissuing !== null}
                      aria-label={t('invitations.list.reissueFor', {
                        username: item.username,
                      })}
                      onClick={() => onReissue(item)}
                    >
                      {t('invitations.list.reissue')}
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <nav
          aria-label={t('invitations.list.pagination')}
          className="flex flex-wrap items-center gap-3"
        >
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={offset === 0}
            onClick={() => onPage(Math.max(0, offset - limit))}
          >
            {t('invitations.list.previous')}
          </Button>
          <p className="text-sm">
            {t('invitations.list.range', {
              from: offset + 1,
              to: offset + items.length,
              total,
            })}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={offset + items.length >= total}
            onClick={() => onPage(offset + limit)}
          >
            {t('invitations.list.next')}
          </Button>
        </nav>
      </>
    );
  }

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-4">
      <h2 id={titleId} className="text-lg font-semibold">
        {t('invitations.list.title')}
      </h2>
      {content}
    </section>
  );
}
