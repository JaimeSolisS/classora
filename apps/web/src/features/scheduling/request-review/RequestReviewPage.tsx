import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import {
  fetchClassroomOptions,
  fetchTutorOptions,
  SCHEDULE_OPTIONS_KEY,
  suggestTutors,
} from '../create/api';
import { OfferPanel } from '../session/OfferPanel';
import {
  confirmSessionRequest,
  fetchRequestReview,
  rejectSessionRequest,
  requestReviewKey,
  type SessionRequestConfirmed,
} from './api';
import { RejectRequestDialog } from './RejectRequestDialog';
import {
  RequestReviewView,
  type RequestReviewNotice,
  type RequestReviewState,
} from './RequestReviewView';
import {
  confirmBody,
  isStaleRequestError,
  requestSuggestionParams,
  staleMessageKey,
} from './rules';

// /schedule/requests/:id, "Review session request" (#60 H1), for holders of
// schedule.manage; linked from the inbox's "Review". Loads the request and,
// while it can be decided, the active tutors and classrooms. "Suggested
// tutors" asks #51 for the request's slot; the confirm panel is the #52
// OfferPanel ("confirm" variant: a 409 scheduling_conflict shows the #48
// panel and, for holders of schedule.override, the #49 override dialog).
// A refused decision because the request changed or started (409
// session_request_not_pending / _started / version_conflict) keeps a
// page-level message and reloads the request. After a decision the focus
// moves to the outcome (the panel and the Reject button are gone).

export function RequestReviewPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('scheduling.requestReview.title'));
  const { requestId = '' } = useParams();
  const timeZone = useCenterTimeZone();
  const canOverride = useCan('schedule.override');
  const client = useQueryClient();
  const [notice, setNotice] = useState<RequestReviewNotice | null>(null);
  const [confirmed, setConfirmed] = useState<{
    sessionId: string;
    tutorName: string;
    warnings: SessionRequestConfirmed['class_session']['warnings'];
  } | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [focusOutcome, setFocusOutcome] = useState(0);
  const outcomeRef = useRef<HTMLHeadingElement>(null);
  const review = useQuery({
    queryKey: requestReviewKey(requestId),
    queryFn: ({ signal }) => fetchRequestReview(requestId, signal),
  });
  const decidable = review.data?.status === 'pending' && review.data.can_decide;
  const tutors = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'tutors'],
    queryFn: ({ signal }) => fetchTutorOptions(signal),
    enabled: decidable,
  });
  const classrooms = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomOptions(signal),
    enabled: decidable,
  });

  const decidedStatus = review.data?.status;
  useEffect(() => {
    if (focusOutcome === 0 || decidedStatus === 'pending') {
      return undefined;
    }
    // After the dialog has returned the focus (its deferred step).
    const timer = window.setTimeout(() => outcomeRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [focusOutcome, decidedStatus]);

  function refresh(): Promise<unknown> {
    return client.invalidateQueries({ queryKey: requestReviewKey(requestId) });
  }

  /** Reloads the request after a refused decision, then rethrows; the
   * message is kept at page level (the reload hides the panel). */
  async function reloadingOnStale<T>(call: Promise<T>): Promise<T> {
    try {
      return await call;
    } catch (caught) {
      if (isStaleRequestError(caught)) {
        setNotice((current) => ({
          id: (current?.id ?? 0) + 1,
          text: t(staleMessageKey(caught)),
        }));
        void refresh();
      }
      throw caught;
    }
  }

  let state: RequestReviewState;
  if (review.isError) {
    state = {
      status: 'error',
      error: review.error,
      onRetry: () => void review.refetch(),
    };
  } else if (!review.data) {
    state = { status: 'loading' };
  } else {
    state = { status: 'ready', review: review.data };
  }

  const detail = review.data;
  const confirmPanel =
    detail && decidable && tutors.data && classrooms.data ? (
      <OfferPanel<SessionRequestConfirmed>
        key={detail.version}
        variant="confirm"
        tutors={tutors.data}
        classrooms={classrooms.data}
        timeZone={timeZone}
        canOverride={canOverride}
        onSuggest={() => suggestTutors(requestSuggestionParams(detail))}
        onSubmit={(values, override) =>
          reloadingOnStale(
            confirmSessionRequest(
              requestId,
              confirmBody(values, detail.version, timeZone, override),
            ),
          )
        }
        onOffered={(result) => {
          setNotice(null);
          setConfirmed({
            sessionId: result.class_session.id,
            tutorName: result.class_session.tutor?.display_name ?? '',
            warnings: result.class_session.warnings,
          });
          client.setQueryData(requestReviewKey(requestId), result.request);
          setFocusOutcome((count) => count + 1);
        }}
      />
    ) : null;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">
        {t('scheduling.requestReview.title')}
      </h1>
      <RequestReviewView
        state={state}
        timeZone={timeZone}
        confirmPanel={confirmPanel}
        onReject={() => setRejecting(true)}
        notice={notice}
        confirmed={confirmed}
        outcomeRef={outcomeRef}
        sessionLink={(sessionId) => (
          <Link
            to={`/schedule/sessions/${encodeURIComponent(sessionId)}`}
            className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('scheduling.requestReview.openSession')}
          </Link>
        )}
      />
      {detail ? (
        <RejectRequestDialog
          open={rejecting}
          onCancel={() => setRejecting(false)}
          onConfirm={async (reason) => {
            try {
              const decided = await reloadingOnStale(
                rejectSessionRequest(requestId, {
                  version: detail.version,
                  reason,
                }),
              );
              setNotice(null);
              client.setQueryData(requestReviewKey(requestId), decided);
              setRejecting(false);
              setFocusOutcome((count) => count + 1);
            } catch (caught) {
              if (isStaleRequestError(caught)) {
                // The page-level message explains it; the reload shows the
                // request as it is now.
                setRejecting(false);
                return;
              }
              throw caught;
            }
          }}
        />
      ) : null}
    </div>
  );
}
