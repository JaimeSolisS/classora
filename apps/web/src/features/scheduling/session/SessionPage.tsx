import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { errorMessageKey } from '@/app/errors';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { Button } from '@/components/ui/button';
import { CALENDAR_KEY } from '@/features/calendar/api';
import { fetchTutorOptions, SCHEDULE_OPTIONS_KEY } from '../create/api';
import type { SchedulingWarning } from '../create/api';
import { toLocalDate } from '@/lib/time';
import {
  cancelClassSession,
  fetchClassSeries,
  fetchClassSession,
  fetchReplacementTutors,
  fetchSessionOffers,
  fetchSessionSubstitutions,
  offerClassSeries,
  offerClassSession,
  seriesKey,
  sessionKey,
  substituteClassSession,
  withdrawSubstitution,
  type SubstitutionCreated,
} from './api';
import {
  attendanceKey,
  correctAttendance,
  fetchAttendance,
} from '@/features/tutor-portal/attendance/api';
import {
  approveCloseout,
  closeoutKey,
  correctCloseout,
  fetchCloseout,
  requestCloseoutChanges,
  submitCloseoutOnBehalf,
  takeOverCloseout,
} from '@/features/tutor-portal/closeout/api';
import {
  fetchSessionNotes,
  sessionNotesKey,
  unpublishNote,
} from '@/features/tutor-portal/notes/api';
import { OVERDUE_CLOSEOUTS_KEY } from '../overdue/api';
import { APPROVALS_QUERY_KEY } from '@/features/approvals/api';
import { changeRequestPath } from '../change-requests/rules';
import {
  AttendanceSection,
  type AttendanceSectionState,
} from './AttendanceSection';
import {
  CloseoutSection,
  type CloseoutReviewActions,
  type CloseoutSectionState,
} from './CloseoutSection';
import { CancelSessionDialog } from './CancelSessionDialog';
import { NotesSection, type NotesSectionState } from './NotesSection';
import { OfferPanel } from './OfferPanel';
import {
  canCancel,
  canSubstitute,
  isStaleSessionError,
  offerBody,
  seriesOfferBody,
  substitutionBody,
} from './rules';
import {
  SessionDetailView,
  type SessionNotice,
  type SessionState,
} from './SessionDetailView';
import { SubstitutionSection } from './SubstitutionSection';

// /schedule/sessions/:id (#52 H2), for schedule.view holders: the session,
// its pending offer and offer history. Holders of schedule.assign get the
// "Offer to a tutor" panel while the session is unassigned (the #51
// replacement suggestions, the #49 override for schedule.override holders).
// Linked from the #48 success panel, the reassignment inbox item and the
// offer notifications. An occurrence of a series (#233 G2) also reads the
// series: its assignment is shown, and while the series has no tutor the
// panel can offer "This and following sessions" (the series from this
// occurrence's local date, with the series version).
//
// #55 H1: holders of schedule.assign get "Find a substitute" for a confirmed
// session that hasn't started (no pending substitution) and can withdraw a
// pending one; holders of schedule.manage get "Cancel session". A write
// refused because the session changed (409 class_session_not_substitutable
// / _not_cancellable / version_conflict) shows its message and reloads the
// session.
//
// #76 G3: a confirmed or completed session shows its attendance; holders of
// attendance.correct correct a mark with a reason (the session and its
// attendance reload; the session's version changes).
// #77 G3: a completed or started confirmed session shows its closeout
// (read-only). #301b: reviewers close it out on the tutor's behalf or take
// over one sent back to the tutor (the overdue list and the calendar
// reload too).
// #79 H3: holders of academic_note.review see the session's notes and stop
// sharing a family note with a reason (Finance never sees the section).

export function SessionPage() {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const [notice, setNotice] = useState<SessionNotice | null>(null);
  useDocumentTitle(t('scheduling.session.title'));
  const { sessionId = '' } = useParams();
  const timeZone = useCenterTimeZone();
  const canAssign = useCan('schedule.assign');
  const canOverride = useCan('schedule.override');
  const canManage = useCan('schedule.manage');
  const canCorrectAttendance = useCan('attendance.correct');
  const canReviewNotes = useCan('academic_note.review');
  const [cancelling, setCancelling] = useState(false);
  const client = useQueryClient();
  const [offered, setOffered] = useState<{
    tutorName: string;
    warnings: readonly SchedulingWarning[];
    series?: boolean;
    substitution?: boolean;
  } | null>(null);
  const session = useQuery({
    queryKey: [...sessionKey(sessionId), 'detail'],
    queryFn: ({ signal }) => fetchClassSession(sessionId, signal),
  });
  const offers = useQuery({
    queryKey: [...sessionKey(sessionId), 'offers'],
    queryFn: ({ signal }) => fetchSessionOffers(sessionId, signal),
  });
  const substitutions = useQuery({
    queryKey: [...sessionKey(sessionId), 'substitutions'],
    queryFn: ({ signal }) => fetchSessionSubstitutions(sessionId, signal),
  });
  const seriesId = session.data?.series_id ?? null;
  const series = useQuery({
    queryKey: seriesKey(seriesId ?? ''),
    queryFn: ({ signal }) => fetchClassSeries(seriesId ?? '', signal),
    enabled: seriesId !== null,
  });
  const showAttendance =
    session.data?.state === 'confirmed' || session.data?.state === 'completed';
  const attendance = useQuery({
    queryKey: attendanceKey(sessionId),
    queryFn: ({ signal }) => fetchAttendance(sessionId, signal),
    enabled: showAttendance,
  });
  const now = new Date();
  // #77 G3: a completed session, or a confirmed one that has started.
  const showCloseout =
    session.data?.state === 'completed' ||
    (session.data?.state === 'confirmed' &&
      Date.parse(session.data.starts_at) <= now.getTime());
  const closeout = useQuery({
    queryKey: closeoutKey(sessionId),
    queryFn: ({ signal }) => fetchCloseout(sessionId, signal),
    enabled: showCloseout,
  });
  const notes = useQuery({
    queryKey: sessionNotesKey(sessionId),
    queryFn: ({ signal }) => fetchSessionNotes(sessionId, signal),
    enabled: canReviewNotes,
  });
  const showPanel = canAssign && session.data?.state === 'unassigned';
  const showSubstitute = session.data
    ? canSubstitute(session.data, now, canAssign)
    : false;
  const showCancel = session.data
    ? canCancel(session.data, now, canManage)
    : false;
  const tutors = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'tutors'],
    queryFn: ({ signal }) => fetchTutorOptions(signal),
    enabled: showPanel || showSubstitute,
  });

  function refresh(): Promise<unknown> {
    return Promise.all([
      client.invalidateQueries({ queryKey: sessionKey(sessionId) }),
      seriesId
        ? client.invalidateQueries({ queryKey: seriesKey(seriesId) })
        : null,
      client.invalidateQueries({ queryKey: CALENDAR_KEY }),
    ]);
  }

  function announce(text: string, tone: SessionNotice['tone']) {
    setNotice((current) => ({ id: (current?.id ?? 0) + 1, text, tone }));
  }

  /**
   * Reloads the session after a refused write, then rethrows. The message
   * is also kept at page level (QA D1): the reload remounts or hides the
   * panel that showed it.
   */
  async function reloadingOnStale<T>(call: Promise<T>): Promise<T> {
    try {
      const result = await call;
      setNotice(null);
      return result;
    } catch (caught) {
      if (isStaleSessionError(caught)) {
        announce(tErrors(errorMessageKey(caught)), 'alert');
        void refresh();
      }
      throw caught;
    }
  }

  let state: SessionState;
  const failed = [session, offers, substitutions, series].find(
    (query) => query.isError,
  );
  if (failed) {
    state = {
      status: 'error',
      error: failed.error,
      onRetry: () => {
        void session.refetch();
        void offers.refetch();
        void substitutions.refetch();
        if (seriesId) {
          void series.refetch();
        }
      },
    };
  } else if (!session.data || !offers.data) {
    state = { status: 'loading' };
  } else {
    state = {
      status: 'ready',
      session: session.data,
      offers: offers.data.items,
    };
  }

  const detail = session.data;
  const seriesDetail = seriesId ? series.data : undefined;
  const seriesChoice =
    detail && seriesDetail && seriesDetail.assignment === null
      ? {
          onSubmit: (values: Parameters<typeof offerBody>[0]) =>
            offerClassSeries(
              seriesDetail.id,
              seriesOfferBody(
                values,
                seriesDetail.version,
                toLocalDate(
                  detail.original_start ?? detail.starts_at,
                  timeZone,
                ),
                timeZone,
              ),
            ),
        }
      : undefined;
  const offerPanel =
    showPanel && detail && tutors.data && (!seriesId || seriesDetail) ? (
      <OfferPanel
        key={`${detail.version}-${seriesDetail?.version ?? ''}`}
        tutors={tutors.data}
        timeZone={timeZone}
        canOverride={canOverride}
        onSuggest={() => fetchReplacementTutors(sessionId)}
        onSubmit={(values, override) =>
          offerClassSession(
            sessionId,
            offerBody(values, detail.version, timeZone, override),
          )
        }
        series={seriesChoice}
        onOffered={(created) => {
          setOffered({
            tutorName: created.offer.tutor.display_name,
            warnings: created.warnings,
            series: created.offer.scope === 'series',
          });
          void client.invalidateQueries({ queryKey: sessionKey(sessionId) });
          if (seriesId) {
            void client.invalidateQueries({ queryKey: seriesKey(seriesId) });
          }
          void client.invalidateQueries({ queryKey: CALENDAR_KEY });
        }}
      />
    ) : null;
  const substitutePanel =
    showSubstitute && detail && tutors.data ? (
      <OfferPanel<SubstitutionCreated>
        key={`substitute-${detail.version}`}
        variant="substitute"
        tutors={tutors.data.filter((tutor) => tutor.id !== detail.tutor?.id)}
        timeZone={timeZone}
        canOverride={canOverride}
        onSuggest={() => fetchReplacementTutors(sessionId)}
        onSubmit={(values, override) =>
          reloadingOnStale(
            substituteClassSession(
              sessionId,
              substitutionBody(values, detail.version, timeZone, override),
            ),
          )
        }
        onOffered={(created) => {
          setOffered({
            tutorName: created.substitution.substitute_tutor.display_name,
            warnings: created.warnings,
            substitution: true,
          });
          void refresh();
        }}
      />
    ) : null;
  const substitutionSection = substitutions.data ? (
    <SubstitutionSection
      items={substitutions.data.items}
      timeZone={timeZone}
      canWithdraw={canAssign}
      onWithdraw={async (item) => {
        await reloadingOnStale(withdrawSubstitution(sessionId, item.id));
        setOffered(null);
        await refresh();
        // Once the confirmation has closed (the withdraw button it would
        // return focus to is gone after the reload), focus the notice.
        window.setTimeout(
          () => announce(t('scheduling.substitution.withdrawn'), 'status'),
          0,
        );
      }}
    />
  ) : null;
  let attendanceState: AttendanceSectionState;
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
  const attendanceSection = showAttendance ? (
    <AttendanceSection
      state={attendanceState}
      timeZone={timeZone}
      canCorrect={canCorrectAttendance}
      onCorrect={async (item, status, reason) => {
        await correctAttendance(sessionId, {
          version: attendance.data?.version ?? 0,
          student_id: item.student.id,
          status,
          reason,
        });
        await Promise.all([
          refresh(),
          client.invalidateQueries({ queryKey: attendanceKey(sessionId) }),
          // #78 E6: the closeout's "attendance changed since approval".
          client.invalidateQueries({ queryKey: closeoutKey(sessionId) }),
        ]);
        window.setTimeout(
          () => announce(t('attendance.section.correctedNotice'), 'status'),
          0,
        );
      }}
      onReload={() => {
        void refresh();
        void client.invalidateQueries({ queryKey: attendanceKey(sessionId) });
      }}
    />
  ) : null;
  let closeoutState: CloseoutSectionState;
  if (closeout.isError) {
    closeoutState = {
      status: 'error',
      error: closeout.error,
      onRetry: () => void closeout.refetch(),
    };
  } else if (!closeout.data) {
    closeoutState = { status: 'loading' };
  } else {
    closeoutState = { status: 'ready', closeout: closeout.data };
  }
  // #78 G1: the review decisions, each with the closeout's version.
  async function afterDecision(notice: string) {
    await Promise.all([
      refresh(),
      client.invalidateQueries({ queryKey: closeoutKey(sessionId) }),
      client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY }),
    ]);
    window.setTimeout(() => announce(notice, 'status'), 0);
  }
  async function afterOnBehalf(notice: string) {
    await Promise.all([
      refresh(),
      client.invalidateQueries({ queryKey: attendanceKey(sessionId) }),
      client.invalidateQueries({ queryKey: closeoutKey(sessionId) }),
      client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY }),
      client.invalidateQueries({ queryKey: OVERDUE_CLOSEOUTS_KEY }),
    ]);
    window.setTimeout(() => announce(notice, 'status'), 0);
  }
  const closeoutReview: CloseoutReviewActions = {
    onApprove: async (version) => {
      await approveCloseout(sessionId, version);
      await afterDecision(t('closeout.review.notices.approved'));
    },
    onRequestChanges: async (version, reason) => {
      await requestCloseoutChanges(sessionId, version, reason);
      await afterDecision(t('closeout.review.notices.changesRequested'));
    },
    onCorrect: async (body) => {
      await correctCloseout(sessionId, body);
      await afterDecision(t('closeout.review.notices.corrected'));
    },
    onSubmitOnBehalf: async (version, values) => {
      await submitCloseoutOnBehalf(sessionId, { version, ...values });
      await afterOnBehalf(
        t('closeout.onBehalf.notices.submitted', {
          tutor: closeout.data?.tutor?.display_name ?? '',
        }),
      );
    },
    onTakeOver: async (version, values) => {
      await takeOverCloseout(sessionId, { version, ...values });
      await afterOnBehalf(t('closeout.onBehalf.notices.takenOver'));
    },
    onReload: () => {
      void refresh();
      void client.invalidateQueries({ queryKey: closeoutKey(sessionId) });
    },
  };
  const closeoutSection = showCloseout ? (
    <CloseoutSection
      state={closeoutState}
      timeZone={timeZone}
      review={closeoutReview}
      students={attendance.data?.items ?? []}
    />
  ) : null;
  let notesState: NotesSectionState;
  if (notes.isError) {
    notesState = {
      status: 'error',
      error: notes.error,
      onRetry: () => void notes.refetch(),
    };
  } else if (!notes.data) {
    notesState = { status: 'loading' };
  } else {
    notesState = { status: 'ready', notes: notes.data.items };
  }
  const notesSection = canReviewNotes ? (
    <NotesSection
      state={notesState}
      timeZone={timeZone}
      onStopSharing={async (note, reason) => {
        await unpublishNote(note, reason);
        await client.invalidateQueries({
          queryKey: sessionNotesKey(sessionId),
        });
      }}
      onReload={() =>
        void client.invalidateQueries({ queryKey: sessionNotesKey(sessionId) })
      }
    />
  ) : null;
  const actions =
    showCancel && detail ? (
      <div>
        <Button
          type="button"
          variant="outline"
          onClick={() => setCancelling(true)}
        >
          {t('scheduling.cancel.action')}
        </Button>
        <CancelSessionDialog
          open={cancelling}
          onCancel={() => setCancelling(false)}
          onConfirm={async (reason) => {
            await reloadingOnStale(
              cancelClassSession(sessionId, {
                version: detail.version,
                reason,
              }),
            );
            setCancelling(false);
            setOffered(null);
            void refresh();
          }}
        />
      </div>
    ) : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('scheduling.session.title')}
        </h1>
        <Link
          to="/schedule"
          className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('scheduling.session.backToCalendar')}
        </Link>
      </div>
      <SessionDetailView
        state={state}
        timeZone={timeZone}
        offerPanel={offerPanel ?? substitutePanel}
        offered={offered}
        series={seriesDetail ?? null}
        actions={actions}
        substitutions={substitutionSection}
        attendance={attendanceSection}
        closeout={closeoutSection}
        notes={notesSection}
        notice={notice}
        changeRequestLink={
          canManage
            ? (changeRequestId) => (
                <Link
                  to={changeRequestPath(changeRequestId)}
                  className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {t('scheduling.changeRequest.pendingLink')}
                </Link>
              )
            : undefined
        }
      />
    </div>
  );
}
