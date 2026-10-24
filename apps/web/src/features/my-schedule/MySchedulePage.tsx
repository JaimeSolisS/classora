import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import { todayInZone, type LocalDate } from '@/lib/time';
import {
  API_LOADERS,
  MY_SCHEDULE_KEY,
  type FamilyStudent,
  type MyScheduleLoaders,
} from './api';
import { ConsentRequired } from './ConsentRequired';
import { MyScheduleCalendar } from './MyScheduleCalendar';
import {
  defaultView,
  myScheduleSearchParams,
  parseMyScheduleState,
  type MyScheduleState,
} from './urlState';
import { entryLabel, resolveWhose, whoseEntries } from './whose';
import { WhoseSchedule } from './WhoseSchedule';

// /my-schedule, "My schedule" (#67 E), for tutors, students and guardians
// (anyRole; the API's record policies decide). Code-split by the router, so
// FullCalendar stays out of the entry chunk. The URL holds whose schedule
// (`?for=`), the view and the date. A guardian chooses a child; an adult
// child without consent shows the consent message instead of the calendar
// (no schedule request).

export interface MyScheduleViewProps {
  userId: string;
  isTutor: boolean;
  hasFamilyRole: boolean;
  timeZone: string;
  today: LocalDate;
  state: MyScheduleState;
  onStateChange: (state: MyScheduleState) => void;
  loaders?: MyScheduleLoaders;
}

function isConsentRequired(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'student_consent_required';
}

export function MyScheduleView({
  userId,
  isTutor,
  hasFamilyRole,
  timeZone,
  today,
  state,
  onStateChange,
  loaders = API_LOADERS,
}: MyScheduleViewProps) {
  const { t } = useTranslation();
  const family = useQuery({
    queryKey: [...MY_SCHEDULE_KEY, 'family-students'],
    queryFn: ({ signal }) => loaders.fetchFamilyStudents(signal),
    enabled: hasFamilyRole,
  });
  const students: readonly FamilyStudent[] = useMemo(
    () => family.data?.items ?? [],
    [family.data],
  );
  const entries = useMemo(
    () => whoseEntries(isTutor, students),
    [isTutor, students],
  );

  if (hasFamilyRole && family.isPending) {
    return <LoadingState rows={3} />;
  }
  const selected = resolveWhose(entries, state.whose);

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {hasFamilyRole && family.isError ? (
        <ErrorAlert
          error={family.error}
          onRetry={() => void family.refetch()}
        />
      ) : null}
      {selected === null ? (
        family.isError ? null : (
          <p className="text-sm">{t('mySchedule.nothing')}</p>
        )
      ) : (
        <>
          <WhoseSchedule
            entries={entries}
            selected={selected}
            onChange={(key) => onStateChange({ ...state, whose: key })}
          />
          {selected.kind === 'student' &&
          selected.student.schedule_access === 'consent_required' ? (
            <ConsentRequired name={selected.student.display_name} />
          ) : (
            <MyScheduleCalendar
              key={selected.key}
              whoseKey={selected.key}
              load={(query, signal) =>
                selected.kind === 'teaching'
                  ? loaders.fetchTutorSchedule(userId, query, signal)
                  : loaders.fetchFamilySchedule(
                      selected.student.student_id,
                      query,
                      signal,
                    )
              }
              position={{ view: state.view, date: state.date }}
              onPositionChange={(position) =>
                onStateChange({ ...state, ...position, whose: selected.key })
              }
              timeZone={timeZone}
              today={today}
              renderError={(error, retry) =>
                isConsentRequired(error) && selected.kind === 'student' ? (
                  <ConsentRequired name={entryLabel(selected, t)} />
                ) : (
                  <ErrorAlert error={error} onRetry={retry} />
                )
              }
            />
          )}
        </>
      )}
    </div>
  );
}

export function MySchedulePage() {
  const { t } = useTranslation();
  useDocumentTitle(t('mySchedule.title'));
  const session = useSession();
  const timeZone = useCenterTimeZone();
  const [params, setParams] = useSearchParams();
  const today = todayInZone(timeZone);
  const state = useMemo(
    () => parseMyScheduleState(params, { view: defaultView(), date: today }),
    [params, today],
  );
  if (session.status !== 'signedIn') {
    return null;
  }
  const roles = session.user.roles as readonly string[];
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">{t('mySchedule.title')}</h1>
      <MyScheduleView
        userId={session.user.id}
        isTutor={roles.includes('tutor')}
        hasFamilyRole={roles.includes('student') || roles.includes('guardian')}
        timeZone={timeZone}
        today={today}
        state={state}
        onStateChange={(next) => setParams(myScheduleSearchParams(next))}
      />
    </div>
  );
}

export default MySchedulePage;
