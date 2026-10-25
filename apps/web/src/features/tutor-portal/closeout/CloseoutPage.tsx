import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useBlocker, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { MY_SCHEDULE_KEY } from '@/features/my-schedule/api';
import {
  attendanceKey,
  fetchAttendance,
  markAttendance,
} from '@/features/tutor-portal/attendance/api';
import type { AttendanceViewState } from '@/features/tutor-portal/attendance/AttendanceView';
import { toLocalDate } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { closeoutKey, fetchCloseout, submitCloseout } from './api';
import { CloseoutFlow, type CloseoutFlowState } from './CloseoutFlow';

// /my-schedule/sessions/:sessionId/closeout (#77 G1), for tutors (the
// route requires the tutor role; the API's record policy decides: only the
// session's current tutor, anyone else 404). Code-split by the router.
// Step 1 saves attendance through #76's PUT (both the attendance and the
// closeout reload: the session's version changes). On success a status
// message and the read-only view; leaving with typed text asks first (the
// React Router blocker); a 404 shows the not-found page.

export function CloseoutPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('closeout.title'));
  const { sessionId = '' } = useParams();
  const timeZone = useCenterTimeZone();
  const client = useQueryClient();
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const closeout = useQuery({
    queryKey: closeoutKey(sessionId),
    queryFn: ({ signal }) => fetchCloseout(sessionId, signal),
  });
  const attendance = useQuery({
    queryKey: attendanceKey(sessionId),
    queryFn: ({ signal }) => fetchAttendance(sessionId, signal),
    enabled: closeout.data !== undefined && closeout.data.closeout === null,
  });
  const submitted = closeout.data?.closeout != null;
  const blocker = useBlocker(dirty && !submitted);
  const onDirtyChange = useCallback((value: boolean) => setDirty(value), []);

  if (
    notFound ||
    (closeout.error instanceof ApiError && closeout.error.status === 404)
  ) {
    return <NotFoundPage />;
  }

  function reload(): Promise<unknown> {
    return Promise.all([
      client.invalidateQueries({ queryKey: closeoutKey(sessionId) }),
      client.invalidateQueries({ queryKey: attendanceKey(sessionId) }),
    ]);
  }

  let state: CloseoutFlowState;
  if (closeout.isError) {
    state = {
      status: 'error',
      error: closeout.error,
      onRetry: () => void closeout.refetch(),
    };
  } else if (!closeout.data) {
    state = { status: 'loading' };
  } else {
    state = { status: 'ready', closeout: closeout.data };
  }
  let attendanceState: AttendanceViewState;
  if (attendance.isError) {
    attendanceState = {
      status: 'error',
      error: attendance.error,
      onRetry: () => void attendance.refetch(),
    };
  } else if (!attendance.data) {
    attendanceState = { status: 'loading' };
  } else {
    attendanceState = { status: 'ready', attendance: attendance.data };
  }
  const back = closeout.data
    ? `/my-schedule?view=list&date=${toLocalDate(closeout.data.starts_at, timeZone)}`
    : '/my-schedule';

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('closeout.title')}</h1>
        <Link
          to={back}
          className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('attendance.back')}
        </Link>
      </div>
      {notice ? (
        <p role="status" className="text-sm font-medium">
          {notice}
        </p>
      ) : null}
      <CloseoutFlow
        state={state}
        attendance={attendanceState}
        timeZone={timeZone}
        onDirtyChange={onDirtyChange}
        onSaveAttendance={async (marks) => {
          await markAttendance(sessionId, {
            version: attendance.data?.version ?? 0,
            marks,
          });
          void client.invalidateQueries({ queryKey: MY_SCHEDULE_KEY });
          await reload();
        }}
        onReload={() => void reload()}
        onSubmit={async (submission) => {
          try {
            await submitCloseout(sessionId, {
              version: closeout.data?.version ?? 0,
              ...submission,
            });
          } catch (caught) {
            if (caught instanceof ApiError && caught.status === 404) {
              setNotFound(true);
              return;
            }
            throw caught;
          }
          setDirty(false);
          setNotice(t('closeout.submittedNotice'));
          void client.invalidateQueries({ queryKey: MY_SCHEDULE_KEY });
          await reload();
        }}
      />
      <Dialog
        open={blocker.state === 'blocked'}
        onOpenChange={(open) => {
          if (!open && blocker.state === 'blocked') {
            blocker.reset();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('closeout.leave.title')}</DialogTitle>
            <DialogDescription>
              {t('closeout.leave.description')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => blocker.reset?.()}
            >
              {t('closeout.leave.stay')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => blocker.proceed?.()}
            >
              {t('closeout.leave.leave')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default CloseoutPage;
