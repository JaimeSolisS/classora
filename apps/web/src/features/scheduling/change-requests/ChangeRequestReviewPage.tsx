import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import type { SchedulingWarning } from '../create/api';
import { fetchClassroomOptions, SCHEDULE_OPTIONS_KEY } from '../create/api';
import {
  approveChangeRequest,
  changeRequestReviewKey,
  fetchChangeRequestReview,
  rejectChangeRequest,
} from './api';
import { ChangeRequestDecisionPanel } from './ChangeRequestDecisionPanel';
import {
  ChangeRequestReviewView,
  type ChangeRequestNotice,
  type ChangeRequestReviewState,
} from './ChangeRequestReviewView';
import {
  approveBody,
  isStaleChangeRequestError,
  staleMessageKey,
} from './rules';

// /schedule/change-requests/:id, "Review change request" (#62 G1), for
// holders of schedule.manage; linked from the inbox's "Review" and from the
// session page's "Change request pending" notice. Loads the request and,
// for a reschedule that can be approved, the active classrooms. A refused
// decision because the request or its session changed (409
// change_request_not_pending / _session_started / _session_changed /
// version_conflict) keeps a page-level message and reloads the request.
// After a decision the focus moves to the outcome heading.

export function ChangeRequestReviewPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('scheduling.changeRequest.title'));
  const { requestId = '' } = useParams();
  const timeZone = useCenterTimeZone();
  const canOverride = useCan('schedule.override');
  const client = useQueryClient();
  const [notice, setNotice] = useState<ChangeRequestNotice | null>(null);
  const [approved, setApproved] = useState<{
    warnings: readonly SchedulingWarning[];
  } | null>(null);
  const [focusOutcome, setFocusOutcome] = useState(0);
  const outcomeRef = useRef<HTMLHeadingElement>(null);
  const review = useQuery({
    queryKey: changeRequestReviewKey(requestId),
    queryFn: ({ signal }) => fetchChangeRequestReview(requestId, signal),
  });
  const detail = review.data;
  const needsRooms =
    detail?.status === 'pending' &&
    detail.can_decide &&
    detail.kind === 'reschedule';
  const classrooms = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomOptions(signal),
    enabled: needsRooms,
  });

  const decidedStatus = detail?.status;
  useEffect(() => {
    if (focusOutcome === 0 || decidedStatus === 'pending') {
      return undefined;
    }
    // After a dialog has returned the focus (its deferred step).
    const timer = window.setTimeout(() => outcomeRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [focusOutcome, decidedStatus]);

  function refresh(): Promise<unknown> {
    return client.invalidateQueries({
      queryKey: changeRequestReviewKey(requestId),
    });
  }

  /** Reloads the request after a refused decision, then rethrows; the
   * message is kept at page level (the reload hides the panel). */
  async function reloadingOnStale<T>(call: Promise<T>): Promise<T> {
    try {
      return await call;
    } catch (caught) {
      if (isStaleChangeRequestError(caught)) {
        setNotice((current) => ({
          id: (current?.id ?? 0) + 1,
          text: t(staleMessageKey(caught)),
        }));
        void refresh();
      }
      throw caught;
    }
  }

  let state: ChangeRequestReviewState;
  if (review.isError) {
    state = {
      status: 'error',
      error: review.error,
      onRetry: () => void review.refetch(),
    };
  } else if (!detail) {
    state = { status: 'loading' };
  } else {
    state = { status: 'ready', review: detail };
  }

  const decisionPanel =
    detail &&
    detail.status === 'pending' &&
    (!needsRooms || classrooms.data) ? (
      <ChangeRequestDecisionPanel
        key={detail.version}
        review={detail}
        classrooms={classrooms.data}
        canOverride={canOverride}
        canApprove={detail.can_decide}
        onApprove={(choice) =>
          reloadingOnStale(
            approveChangeRequest(
              requestId,
              approveBody(detail.version, detail.kind, choice),
            ),
          )
        }
        onApproved={(result) => {
          setNotice(null);
          setApproved({ warnings: result.warnings });
          client.setQueryData(
            changeRequestReviewKey(requestId),
            result.change_request,
          );
          setFocusOutcome((count) => count + 1);
        }}
        onReject={async (reason) => {
          try {
            const decided = await reloadingOnStale(
              rejectChangeRequest(requestId, {
                version: detail.version,
                reason,
              }),
            );
            setNotice(null);
            client.setQueryData(changeRequestReviewKey(requestId), decided);
            setFocusOutcome((count) => count + 1);
          } catch (caught) {
            if (isStaleChangeRequestError(caught)) {
              // The page-level message explains it; the reload shows the
              // request as it is now.
              return;
            }
            throw caught;
          }
        }}
      />
    ) : null;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">
        {t('scheduling.changeRequest.title')}
      </h1>
      <ChangeRequestReviewView
        state={state}
        timeZone={timeZone}
        decisionPanel={decisionPanel}
        notice={notice}
        approved={approved}
        outcomeRef={outcomeRef}
        sessionLink={(sessionId) => (
          <Link
            to={`/schedule/sessions/${encodeURIComponent(sessionId)}`}
            className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('scheduling.changeRequest.openSession')}
          </Link>
        )}
      />
    </div>
  );
}
