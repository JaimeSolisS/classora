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
import type {
  Decision,
  RegistrationRequestItem,
  RegistrationRequestPage,
} from './api';

// The registration requests of one status (#169 G2): display name, username,
// requested role, submitted at and, for decided rows, decided at. Pending
// rows have Approve and Reject buttons. Previous/next pages and the empty,
// loading and error states. Presentational: the page passes the query state
// and the actions.

export type RegistrationRequestsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: RegistrationRequestPage };

interface RegistrationRequestsTableProps {
  state: RegistrationRequestsState;
  /** Opens the confirmation dialog for a pending row. */
  onDecide: (item: RegistrationRequestItem, decision: Decision) => void;
  onPage: (offset: number) => void;
}

export function RegistrationRequestsTable({
  state,
  onDecide,
  onPage,
}: RegistrationRequestsTableProps) {
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
    content = <p>{t('registrations.list.empty')}</p>;
  } else {
    const { items, total, limit, offset } = state.page;
    const decided = items.some((item) => item.status !== 'pending');
    const pending = items.some((item) => item.status === 'pending');
    content = (
      <>
        <Table aria-labelledby={titleId}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('registrations.list.displayName')}
              </TableHead>
              <TableHead scope="col">
                {t('registrations.list.username')}
              </TableHead>
              <TableHead scope="col">{t('registrations.list.role')}</TableHead>
              <TableHead scope="col">
                {t('registrations.list.submittedAt')}
              </TableHead>
              {decided ? (
                <TableHead scope="col">
                  {t('registrations.list.decidedAt')}
                </TableHead>
              ) : null}
              {pending ? (
                <TableHead scope="col">
                  {t('registrations.list.actions')}
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>{item.display_name}</TableCell>
                <TableCell>{item.username}</TableCell>
                <TableCell>{t(roleNameKey(item.requested_role))}</TableCell>
                <TableCell>
                  {formatInstant(item.submitted_at, 'dateTime')}
                </TableCell>
                {decided ? (
                  <TableCell>
                    {item.decided_at
                      ? formatInstant(item.decided_at, 'dateTime')
                      : null}
                  </TableCell>
                ) : null}
                {pending ? (
                  <TableCell>
                    {item.status === 'pending' ? (
                      <DecisionButtons item={item} onDecide={onDecide} />
                    ) : null}
                  </TableCell>
                ) : null}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <nav
          aria-label={t('registrations.list.pagination')}
          className="flex flex-wrap items-center gap-3"
        >
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={offset === 0}
            onClick={() => onPage(Math.max(0, offset - limit))}
          >
            {t('registrations.list.previous')}
          </Button>
          <p className="text-sm">
            {t('registrations.list.range', {
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
            {t('registrations.list.next')}
          </Button>
        </nav>
      </>
    );
  }

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-4">
      <h2 id={titleId} className="text-lg font-semibold">
        {t('registrations.list.title')}
      </h2>
      {content}
    </section>
  );
}

function DecisionButtons({
  item,
  onDecide,
}: {
  item: RegistrationRequestItem;
  onDecide: (item: RegistrationRequestItem, decision: Decision) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        type="button"
        size="sm"
        aria-label={t('registrations.approveFor', { username: item.username })}
        onClick={() => onDecide(item, 'approve')}
      >
        {t('registrations.approve')}
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-label={t('registrations.rejectFor', { username: item.username })}
        onClick={() => onDecide(item, 'reject')}
      >
        {t('registrations.reject')}
      </Button>
    </div>
  );
}
