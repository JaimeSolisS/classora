import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan, useSession } from '@/features/auth/useSession';
import { CALENDAR_KEY } from '@/features/calendar/api';
import { searchStudents } from '@/features/scheduling/create/api';
import { todayInZone } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import {
  enrollStudent,
  fetchClassroomCapacityOptions,
  fetchGroup,
  fetchGroupSessions,
  GROUP_OPTIONS_KEY,
  GROUPS_KEY,
  groupKey,
  groupSessionsKey,
  updateGroup,
  withdrawEnrollment,
  type GroupEnrollmentItem,
} from './api';
import { EditGroupDialog } from './EditGroupDialog';
import { EnrollDialog } from './EnrollDialog';
import { GroupView } from './GroupView';
import { MySessionsSection, type MySessionsState } from './MySessionsSection';
import {
  activeStudentIds,
  groupView,
  roomLimited,
  sessionsWindow,
  shiftWindow,
  withdrawBody,
} from './rules';
import { WithdrawDialog } from './WithdrawDialog';

// /groups/:groupId (#234 C): the staff view (schedule.view) with the
// capacity, the next sessions and the roster, and for schedule.manage
// holders the enroll, withdraw and edit dialogs; or the student view with
// "My sessions" (C4). An unknown or hidden group (404) is the shared
// not-found page. Every write refreshes the group (and the list and the
// calendar) and is announced at the top.

interface PageNotice {
  notice?: string;
}

export function GroupPage() {
  const { groupId = '' } = useParams();
  return <GroupPageContent key={groupId} groupId={groupId} />;
}

function GroupPageContent({ groupId }: { groupId: string }) {
  const { t } = useTranslation();
  const location = useLocation();
  const session = useSession();
  const view = groupView(session.status === 'signedIn' ? session.user : null);
  const timeZone = useCenterTimeZone();
  const canManage = useCan('schedule.manage');
  const canOverride = useCan('schedule.override');
  const client = useQueryClient();
  const [notice, setNotice] = useState<string | null>(
    (location.state as PageNotice | null)?.notice ?? null,
  );
  const [noticeFocus, setNoticeFocus] = useState(notice !== null);
  const [enrolling, setEnrolling] = useState(false);
  const [withdrawing, setWithdrawing] = useState<GroupEnrollmentItem | null>(
    null,
  );
  const [editing, setEditing] = useState(false);
  const [windowStart, setWindowStart] = useState<string>(() =>
    todayInZone(timeZone),
  );

  const group = useQuery({
    queryKey: groupKey(groupId),
    queryFn: ({ signal }) => fetchGroup(groupId, signal),
  });
  useDocumentTitle(group.data?.name ?? t('groups.page.title'));
  const range = sessionsWindow(windowStart, timeZone);
  const sessions = useQuery({
    queryKey: groupSessionsKey(groupId, range.from, range.to),
    queryFn: ({ signal }) =>
      fetchGroupSessions(groupId, range.from, range.to, signal),
    enabled: view === 'student' && group.isSuccess,
  });
  const classroomId = group.data?.current_series?.classroom?.id ?? null;
  const needsRoom =
    editing &&
    classroomId !== null &&
    group.data !== undefined &&
    !roomLimited(group.data);
  const classrooms = useQuery({
    queryKey: [...GROUP_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomCapacityOptions(signal),
    enabled: needsRoom,
  });

  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: GROUPS_KEY }),
      client.invalidateQueries({ queryKey: CALENDAR_KEY }),
    ]);
  }

  function announce(text: string, focus: boolean) {
    setNoticeFocus(focus);
    setNotice(text);
  }

  if (group.isError) {
    if (group.error instanceof ApiError && group.error.status === 404) {
      return <NotFoundPage />;
    }
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-2xl font-semibold">{t('groups.page.title')}</h1>
        <ErrorAlert error={group.error} onRetry={() => void group.refetch()} />
      </div>
    );
  }

  const detail = group.data;
  if (!detail) {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-2xl font-semibold">{t('groups.page.title')}</h1>
        <LoadingState />
      </div>
    );
  }

  let roomCapacity: number | null = null;
  if (roomLimited(detail)) {
    roomCapacity = detail.effective_capacity;
  } else if (classroomId) {
    roomCapacity =
      classrooms.data?.find((room) => room.id === classroomId)?.capacity ??
      null;
  }

  const sessionsState: MySessionsState = sessions.data
    ? { status: 'ready', items: sessions.data.items }
    : sessions.isError
      ? {
          status: 'error',
          error: sessions.error,
          onRetry: () => void sessions.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold break-words">{detail.name}</h1>
      <GroupView
        group={detail}
        view={view}
        timeZone={timeZone}
        canManage={canManage}
        notice={notice}
        focusNotice={noticeFocus}
        onEnroll={() => {
          setNotice(null);
          setEnrolling(true);
        }}
        onWithdraw={(item) => {
          setNotice(null);
          setWithdrawing(item);
        }}
        onEdit={() => {
          setNotice(null);
          setEditing(true);
        }}
        mySessions={
          view === 'student' ? (
            <MySessionsSection
              windowStart={windowStart}
              state={sessionsState}
              timeZone={timeZone}
              onPrevious={() => setWindowStart((d) => shiftWindow(d, -1))}
              onNext={() => setWindowStart((d) => shiftWindow(d, 1))}
            />
          ) : undefined
        }
      />
      {view === 'staff' && canManage ? (
        <>
          <EnrollDialog
            open={enrolling}
            groupName={detail.name}
            enrolledIds={activeStudentIds(detail.roster)}
            searchStudents={searchStudents}
            canOverride={canOverride}
            onEnroll={(student, override) =>
              enrollStudent(detail.id, {
                student_id: student.id,
                ...(override ? { override } : {}),
              })
            }
            onDone={(created, student) => {
              setEnrolling(false);
              announce(
                created.overridden
                  ? t('groups.enroll.doneOverride', { name: student.name })
                  : t('groups.enroll.done', { name: student.name }),
                false,
              );
              void refresh();
            }}
            onCancel={() => setEnrolling(false)}
          />
          <WithdrawDialog
            enrollment={withdrawing}
            groupName={detail.name}
            onReload={() => {
              setWithdrawing(null);
              void group.refetch();
            }}
            onCancel={() => setWithdrawing(null)}
            onConfirm={async (reason) => {
              if (!withdrawing) {
                return;
              }
              try {
                await withdrawEnrollment(
                  detail.id,
                  withdrawing.id,
                  withdrawBody(withdrawing.version, reason),
                );
              } catch (caught) {
                if (
                  caught instanceof ApiError &&
                  caught.code === 'enrollment_not_active'
                ) {
                  void refresh();
                }
                throw caught;
              }
              const name = withdrawing.student.display_name;
              setWithdrawing(null);
              // The row's button is gone: the notice takes the focus.
              announce(t('groups.withdraw.done', { name }), true);
              await refresh();
            }}
          />
          <EditGroupDialog
            open={editing}
            group={detail}
            roomCapacity={roomCapacity}
            onReload={() => void group.refetch()}
            onCancel={() => setEditing(false)}
            onSubmit={async (body) => {
              const updated = await updateGroup(detail.id, body);
              client.setQueryData(groupKey(detail.id), updated);
              setEditing(false);
              announce(t('groups.edit.done'), false);
              await refresh();
            }}
          />
        </>
      ) : null}
    </div>
  );
}
