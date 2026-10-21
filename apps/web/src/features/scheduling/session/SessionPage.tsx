import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { CALENDAR_KEY } from '@/features/calendar/api';
import { fetchTutorOptions, SCHEDULE_OPTIONS_KEY } from '../create/api';
import type { SchedulingWarning } from '../create/api';
import { toLocalDate } from '@/lib/time';
import {
  fetchClassSeries,
  fetchClassSession,
  fetchReplacementTutors,
  fetchSessionOffers,
  offerClassSeries,
  offerClassSession,
  seriesKey,
  sessionKey,
} from './api';
import { OfferPanel } from './OfferPanel';
import { offerBody, seriesOfferBody } from './rules';
import { SessionDetailView, type SessionState } from './SessionDetailView';

// /schedule/sessions/:id (#52 H2), for schedule.view holders: the session,
// its pending offer and offer history. Holders of schedule.assign get the
// "Offer to a tutor" panel while the session is unassigned (the #51
// replacement suggestions, the #49 override for schedule.override holders).
// Linked from the #48 success panel, the reassignment inbox item and the
// offer notifications. An occurrence of a series (#233 G2) also reads the
// series: its assignment is shown, and while the series has no tutor the
// panel can offer "This and following sessions" (the series from this
// occurrence's local date, with the series version).

export function SessionPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('scheduling.session.title'));
  const { sessionId = '' } = useParams();
  const timeZone = useCenterTimeZone();
  const canAssign = useCan('schedule.assign');
  const canOverride = useCan('schedule.override');
  const client = useQueryClient();
  const [offered, setOffered] = useState<{
    tutorName: string;
    warnings: readonly SchedulingWarning[];
    series?: boolean;
  } | null>(null);
  const session = useQuery({
    queryKey: [...sessionKey(sessionId), 'detail'],
    queryFn: ({ signal }) => fetchClassSession(sessionId, signal),
  });
  const offers = useQuery({
    queryKey: [...sessionKey(sessionId), 'offers'],
    queryFn: ({ signal }) => fetchSessionOffers(sessionId, signal),
  });
  const seriesId = session.data?.series_id ?? null;
  const series = useQuery({
    queryKey: seriesKey(seriesId ?? ''),
    queryFn: ({ signal }) => fetchClassSeries(seriesId ?? '', signal),
    enabled: seriesId !== null,
  });
  const showPanel = canAssign && session.data?.state === 'unassigned';
  const tutors = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'tutors'],
    queryFn: ({ signal }) => fetchTutorOptions(signal),
    enabled: showPanel,
  });

  let state: SessionState;
  const failed = [session, offers, series].find((query) => query.isError);
  if (failed) {
    state = {
      status: 'error',
      error: failed.error,
      onRetry: () => {
        void session.refetch();
        void offers.refetch();
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
        offerPanel={offerPanel}
        offered={offered}
        series={seriesDetail ?? null}
      />
    </div>
  );
}
