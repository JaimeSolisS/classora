import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import {
  decideRegistration,
  fetchRegistrationRequests,
  REGISTRATION_REQUESTS_QUERY_KEY,
  REGISTRATION_STATUSES,
  registrationRequestsKey,
  type RegistrationStatus,
} from './api';
import { DecisionDialog, type PendingDecision } from './DecisionDialog';
import {
  RegistrationRequestsTable,
  type RegistrationRequestsState,
} from './RegistrationRequestsTable';

// /registration-requests (#169 G): the account requests of one status
// (Pending by default), oldest first, with Approve and Reject behind a
// confirmation dialog. A simple list until the approval inbox (M3-04).
export function RegistrationRequestsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('registrations.title'));
  const queryClient = useQueryClient();
  const id = useId();
  const statusId = `${id}-status`;
  const statusField = useRef<HTMLSelectElement>(null);
  const [status, setStatus] = useState<RegistrationStatus>('pending');
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<PendingDecision | null>(null);
  const [done, setDone] = useState<'approved' | 'rejected' | null>(null);
  const [conflict, setConflict] = useState<unknown>(null);

  const list = useQuery({
    queryKey: registrationRequestsKey(status, offset),
    queryFn: ({ signal }) => fetchRegistrationRequests(status, offset, signal),
    placeholderData: keepPreviousData,
  });

  function refreshList() {
    void queryClient.invalidateQueries({
      queryKey: REGISTRATION_REQUESTS_QUERY_KEY,
    });
  }

  async function confirm({ item, decision }: PendingDecision) {
    setDone(null);
    setConflict(null);
    try {
      await decideRegistration(item.id, decision);
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.code === 'registration_already_decided'
      ) {
        // Someone else decided first: say so and show the current list.
        setConflict(caught);
        setPending(null);
        refreshList();
        return;
      }
      throw caught;
    }
    setPending(null);
    setDone(decision === 'approve' ? 'approved' : 'rejected');
    refreshList();
  }

  const listState: RegistrationRequestsState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('registrations.title')}</h1>
        <p>{t('registrations.intro')}</p>
      </div>
      <div className="flex w-full max-w-xs flex-col gap-2">
        <label htmlFor={statusId} className="text-sm font-medium">
          {t('registrations.filter.label')}
        </label>
        <select
          id={statusId}
          ref={statusField}
          value={status}
          className="h-9 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring md:text-sm"
          onChange={(event) => {
            setStatus(event.target.value as RegistrationStatus);
            setOffset(0);
            setDone(null);
            setConflict(null);
          }}
        >
          {REGISTRATION_STATUSES.map((value) => (
            <option key={value} value={value}>
              {t(`registrations.filter.${value}`)}
            </option>
          ))}
        </select>
      </div>
      <div role="status">
        {done ? <p>{t(`registrations.${done}`)}</p> : null}
      </div>
      {conflict ? <ErrorAlert error={conflict} /> : null}
      <RegistrationRequestsTable
        state={listState}
        onDecide={(item, decision) => {
          setDone(null);
          setConflict(null);
          setPending({ item, decision });
        }}
        onPage={setOffset}
      />
      <DecisionDialog
        pending={pending}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
        fallbackFocus={statusField}
      />
    </div>
  );
}
