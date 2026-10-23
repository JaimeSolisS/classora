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
import { changeRequestPath } from '../change-requests/rules';
import { CancelSessionDialog } from './CancelSessionDialog';
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
  const showPanel = canAssign && session.data?.state === 'unassigned';
  const now = new Date();
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
