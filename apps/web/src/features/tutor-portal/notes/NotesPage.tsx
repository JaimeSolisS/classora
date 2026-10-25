import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  attendanceKey,
  fetchAttendance,
} from '@/features/tutor-portal/attendance/api';
import { toLocalDate } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import {
  createNote,
  deleteNote,
  editNote,
  fetchSessionNotes,
  fetchStudentNotes,
  NOTES_KEY,
  publishNote,
  sessionNotesKey,
  studentNotesKey,
  unpublishNote,
  type SessionNote,
} from './api';
import { NotesView, type NoteActions, type NotesListState } from './NotesView';
import { showsNotFound } from './rules';

// /my-schedule/sessions/:sessionId/notes (#79 H1), for tutors (the route
// requires the tutor role; the API's record policies decide: the session's
// current tutor reads and writes, anyone else 404). Code-split by the
// router. The class header and the participants come from the session's
// attendance (the current tutor may read it in any state); the notes from
// GET …/notes (filtered to the selected student) and the earlier notes
// from GET /students/{id}/notes (other classes). A 404 on the session, or a
// write refused with note_published, note_read_only, session_not_notable or
// student_not_participant, shows the not-found page.

export function NotesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('notes.title'));
  const { sessionId = '' } = useParams();
  const timeZone = useCenterTimeZone();
  const client = useQueryClient();
  const [chosen, setChosen] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const session = useQuery({
    queryKey: attendanceKey(sessionId),
    queryFn: ({ signal }) => fetchAttendance(sessionId, signal),
  });
  const notes = useQuery({
    queryKey: sessionNotesKey(sessionId),
    queryFn: ({ signal }) => fetchSessionNotes(sessionId, signal),
  });
  const studentId = chosen ?? session.data?.items[0]?.student.id ?? '';
  const history = useInfiniteQuery({
    queryKey: studentNotesKey(studentId),
    queryFn: ({ pageParam, signal }) =>
      fetchStudentNotes(studentId, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: studentId !== '',
    retry: false,
  });

  const missing = [session.error, notes.error].some(
    (error) => error instanceof ApiError && error.status === 404,
  );
  if (notFound || missing) {
    return <NotFoundPage />;
  }

  function reload(): Promise<unknown> {
    return client.invalidateQueries({ queryKey: NOTES_KEY });
  }

  /** A write; the not-found codes show the not-found page (H1). */
  async function write(call: () => Promise<unknown>): Promise<void> {
    try {
      await call();
    } catch (caught) {
      if (showsNotFound(caught)) {
        setNotFound(true);
        return;
      }
      throw caught;
    }
    await reload();
  }

  const actions: NoteActions = {
    onCreate: (body) => write(() => createNote(sessionId, studentId, body)),
    onEdit: (note, body) => write(() => editNote(note, body)),
    onShare: (note) => write(() => publishNote(note)),
    onStopSharing: (note) => write(() => unpublishNote(note)),
    onDelete: (note) => write(() => deleteNote(note)),
    onReload: () => void reload(),
  };

  let notesState: NotesListState<SessionNote>;
  if (notes.isError) {
    notesState = {
      status: 'error',
      error: notes.error,
      onRetry: () => void notes.refetch(),
    };
  } else if (!notes.data) {
    notesState = { status: 'loading' };
  } else {
    notesState = {
      status: 'ready',
      items: notes.data.items.filter((note) => note.student.id === studentId),
    };
  }

  let historyState: NotesListState<SessionNote>;
  const historyMissing =
    history.error instanceof ApiError && history.error.status === 404;
  if (historyMissing) {
    historyState = { status: 'ready', items: [] };
  } else if (history.isError) {
    historyState = {
      status: 'error',
      error: history.error,
      onRetry: () => void history.refetch(),
    };
  } else if (!history.data) {
    historyState = { status: 'loading' };
  } else {
    historyState = {
      status: 'ready',
      items: history.data.pages
        .flatMap((page) => page.items)
        .filter((note) => note.class_session_id !== sessionId),
    };
  }

  const back = session.data
    ? `/my-schedule?view=list&date=${toLocalDate(session.data.starts_at, timeZone)}`
    : '/my-schedule';

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('notes.title')}</h1>
        <Link
          to={back}
          className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('notes.back')}
        </Link>
      </div>
      {session.isError ? (
        <ErrorAlert
          error={session.error}
          onRetry={() => void session.refetch()}
        />
      ) : !session.data ? (
        <LoadingState rows={4} />
      ) : (
        <NotesView
          session={session.data}
          timeZone={timeZone}
          studentId={studentId}
          onStudentChange={setChosen}
          notes={notesState}
          history={{
            state: historyState,
            hasMore: !historyMissing && history.hasNextPage,
            loadingMore: history.isFetchingNextPage,
            onLoadMore: () => void history.fetchNextPage(),
          }}
          actions={actions}
        />
      )}
    </div>
  );
}

export default NotesPage;
