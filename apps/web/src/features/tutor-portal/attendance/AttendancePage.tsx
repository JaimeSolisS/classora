import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { MY_SCHEDULE_KEY } from '@/features/my-schedule/api';
import { toLocalDate } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import {
  attendanceKey,
  fetchAttendance,
  markAttendance,
  type AttendanceMark,
} from './api';
import { AttendanceView, type AttendanceViewState } from './AttendanceView';

// /my-schedule/sessions/:sessionId/attendance (#76 G1), for tutors (the
// route requires the tutor role; the API's record policy decides: only the
// session's current tutor, anyone else 404). Code-split by the router.
// After a save the attendance reloads; a 404 shows the not-found page.

export function AttendancePage() {
  const { t } = useTranslation();
  useDocumentTitle(t('attendance.title'));
  const { sessionId = '' } = useParams();
  const timeZone = useCenterTimeZone();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: attendanceKey(sessionId),
    queryFn: ({ signal }) => fetchAttendance(sessionId, signal),
  });

  if (query.error instanceof ApiError && query.error.status === 404) {
    return <NotFoundPage />;
  }

  function reload(): Promise<unknown> {
    return client.invalidateQueries({ queryKey: attendanceKey(sessionId) });
  }

  let state: AttendanceViewState;
  if (query.isError) {
    state = {
      status: 'error',
      error: query.error,
      onRetry: () => void query.refetch(),
    };
  } else if (!query.data) {
    state = { status: 'loading' };
  } else {
    state = { status: 'ready', attendance: query.data };
  }
  const back = query.data
    ? `/my-schedule?view=list&date=${toLocalDate(query.data.starts_at, timeZone)}`
    : '/my-schedule';

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('attendance.title')}</h1>
        <Link
          to={back}
          className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('attendance.back')}
        </Link>
      </div>
      <AttendanceView
        state={state}
        timeZone={timeZone}
        onSave={async (marks: AttendanceMark[]) => {
          const version = query.data?.version ?? 0;
          await markAttendance(sessionId, { version, marks });
          void client.invalidateQueries({ queryKey: MY_SCHEDULE_KEY });
          await reload();
        }}
        onReload={() => void reload()}
      />
    </div>
  );
}

export default AttendancePage;
