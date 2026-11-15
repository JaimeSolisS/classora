import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import type { ScheduleEntry } from '@/features/my-schedule/api';
import {
  CHANGE_REQUEST_LOADERS,
  CHANGE_REQUESTS_KEY,
  pendingRequestKey,
  type ChangeRequestDetail,
  type ChangeRequestLoaders,
} from './api';
import { RequestChangeDialog } from './RequestChangeDialog';
import { canRequestChange, type WhoseKind } from './rules';

// The "Request a change" part of the My schedule details dialog (#62b A1,
// A4). Shown for a confirmed individual session that hasn't started and is
// the caller's to request (canRequestChange, a UX filter: the server
// decides). When the dialog opens it asks for the session's pending
// request (GET /change-requests?class_session_id&status=pending); with one,
// it says "Change requested — pending" and links to My change requests.
// After a request is sent the same text replaces the button and a status
// message, which takes the focus, says what happens next.

export interface RequestChangeActionProps {
  entry: ScheduleEntry;
  whose: WhoseKind;
  /** The family student whose schedule this is (null for "My teaching"). */
  studentId: string | null;
  timeZone: string;
  loaders?: ChangeRequestLoaders;
  /** The clock (tests and stories). */
  now?: () => number;
  /** The session is no longer visible (404): refresh the schedule. */
  onScheduleStale?: () => void;
}

export function RequestChangeAction({
  entry,
  whose,
  studentId,
  timeZone,
  loaders = CHANGE_REQUEST_LOADERS,
  now = Date.now,
  onScheduleStale,
}: RequestChangeActionProps) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [openedAt, setOpenedAt] = useState<number | null>(null);
  const [sent, setSent] = useState(false);
  const notice = useRef<HTMLParagraphElement>(null);
  const [eligible] = useState(() => canRequestChange(entry, whose, now()));
  const sessionId = entry.item.id;

  const pending = useQuery({
    queryKey: pendingRequestKey(sessionId),
    queryFn: ({ signal }) => loaders.fetchPendingRequest(sessionId, signal),
    enabled: eligible,
    retry: false,
  });

  if (!eligible) {
    return null;
  }

  function onSent(created: ChangeRequestDetail) {
    client.setQueryData(pendingRequestKey(sessionId), created);
    void client.invalidateQueries({
      queryKey: [...CHANGE_REQUESTS_KEY, 'list'],
    });
    setOpenedAt(null);
    setSent(true);
    // The button is gone: the status message takes the focus.
    window.setTimeout(() => notice.current?.focus(), 0);
  }

  let body;
  if (pending.isPending) {
    body = (
      <p className="flex items-center gap-2 text-sm">
        <Spinner />
        {t('changeRequests.action.checking')}
      </p>
    );
  } else if (pending.data) {
    body = (
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span className="cl-badge font-medium">
          {t('changeRequests.action.pending')}
        </span>
        <Link
          to="/change-requests?status=pending"
          className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('changeRequests.action.pendingLink')}
        </Link>
      </p>
    );
  } else {
    // A failed check still offers the button: the server decides.
    body = (
      <Button
        type="button"
        variant="outline"
        className="w-fit"
        onClick={() => setOpenedAt(now())}
      >
        {t('changeRequests.action.request')}
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <p
        ref={notice}
        tabIndex={-1}
        role="status"
        className="text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring empty:hidden"
      >
        {sent ? t('changeRequests.action.sent') : ''}
      </p>
      {body}
      <RequestChangeDialog
        open={openedAt !== null}
        entry={entry}
        studentId={studentId}
        timeZone={timeZone}
        now={openedAt ?? now()}
        clock={now}
        loaders={loaders}
        onClose={() => setOpenedAt(null)}
        onSent={onSent}
        onScheduleStale={onScheduleStale}
      />
    </div>
  );
}
