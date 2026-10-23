import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { todayInZone } from '@/lib/time';
import {
  createSessionRequest,
  fetchSlots,
  fetchStudentChoices,
  fetchSubjectChoices,
  OPTIONS_KEY,
  REQUESTS_KEY,
  SLOTS_KEY,
  type AvailableSlot,
  type RequestDuration,
} from './api';
import {
  RequestSessionView,
  type LoadState,
  type SlotsState,
} from './RequestSessionView';
import { weekRange } from './rules';

// /requests/new, "Request a session" (#59 F2), for guardians and students
// (the route requires one of those roles; the API decides which students).
// The week starts today in the center zone. A sent request refreshes the
// slots (the time now says "Requested") and the request list; a `409
// slot_unavailable` refreshes the slots too.

function loadState<T>(query: {
  isError: boolean;
  error: unknown;
  data: T | undefined;
  refetch: () => unknown;
}): LoadState<T> {
  if (query.isError) {
    return {
      status: 'error',
      error: query.error,
      onRetry: () => void query.refetch(),
    };
  }
  return query.data === undefined
    ? { status: 'loading' }
    : { status: 'ready', data: query.data };
}

export function RequestSessionPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('sessionRequests.new.title'));
  const timeZone = useCenterTimeZone();
  const today = todayInZone(timeZone);
  const client = useQueryClient();
  const [chosenStudent, setChosenStudent] = useState<string | null>(null);
  const [subjectId, setSubjectId] = useState<string | null>(null);
  const [duration, setDuration] = useState<RequestDuration>(60);
  const [weekStart, setWeekStart] = useState<string>(today);
  const [sent, setSent] = useState(false);

  const students = useQuery({
    queryKey: [...OPTIONS_KEY, 'students'],
    queryFn: ({ signal }) => fetchStudentChoices(signal),
  });
  const subjects = useQuery({
    queryKey: [...OPTIONS_KEY, 'subjects'],
    queryFn: ({ signal }) => fetchSubjectChoices(signal),
  });
  const studentId =
    chosenStudent ??
    (students.data?.length === 1 ? students.data[0]!.id : null);
  const range = weekRange(weekStart, today);
  const slotsQuery = {
    studentId: studentId ?? '',
    subjectId: subjectId ?? '',
    duration,
    from: range.from,
    to: range.to,
  };
  const slots = useQuery({
    queryKey: [...SLOTS_KEY, slotsQuery],
    queryFn: ({ signal }) => fetchSlots(slotsQuery, signal),
    enabled: Boolean(studentId && subjectId),
    retry: false,
  });

  const slotsState: SlotsState =
    studentId && subjectId ? loadState(slots) : { status: 'idle' };

  async function request(slot: AvailableSlot, note: string) {
    setSent(false);
    try {
      await createSessionRequest({
        studentId: studentId!,
        subjectId: subjectId!,
        startsAt: slot.starts_at,
        duration,
        note,
      });
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === 'slot_unavailable') {
        void client.invalidateQueries({ queryKey: SLOTS_KEY });
      }
      throw caught;
    }
    setSent(true);
    void client.invalidateQueries({ queryKey: SLOTS_KEY });
    void client.invalidateQueries({ queryKey: REQUESTS_KEY });
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('sessionRequests.new.title')}
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('sessionRequests.new.intro')}
        </p>
        <Link
          to="/requests"
          className="w-fit text-sm font-medium underline underline-offset-4"
        >
          {t('sessionRequests.new.viewRequests')}
        </Link>
      </div>
      <RequestSessionView
        students={loadState(students)}
        subjects={loadState(subjects)}
        studentId={studentId}
        onStudent={(id) => {
          setChosenStudent(id);
          setSent(false);
        }}
        subjectId={subjectId}
        onSubject={(id) => {
          setSubjectId(id);
          setSent(false);
        }}
        duration={duration}
        onDuration={(next) => {
          setDuration(next);
          setSent(false);
        }}
        weekStart={weekStart}
        today={today}
        onWeek={setWeekStart}
        slots={slotsState}
        timeZone={timeZone}
        onRequest={request}
        sent={sent}
      />
    </div>
  );
}
