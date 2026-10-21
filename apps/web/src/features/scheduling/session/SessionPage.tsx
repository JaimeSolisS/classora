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
import {
  fetchClassSession,
  fetchReplacementTutors,
  fetchSessionOffers,
  offerClassSession,
  sessionKey,
} from './api';
import { OfferPanel } from './OfferPanel';
import { offerBody } from './rules';
import { SessionDetailView, type SessionState } from './SessionDetailView';

// /schedule/sessions/:id (#52 H2), for schedule.view holders: the session,
// its pending offer and offer history. Holders of schedule.assign get the
// "Offer to a tutor" panel while the session is unassigned (the #51
// replacement suggestions, the #49 override for schedule.override holders).
// Linked from the #48 success panel, the reassignment inbox item and the
// offer notifications.

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
  } | null>(null);
  const session = useQuery({
    queryKey: [...sessionKey(sessionId), 'detail'],
    queryFn: ({ signal }) => fetchClassSession(sessionId, signal),
  });
  const offers = useQuery({
    queryKey: [...sessionKey(sessionId), 'offers'],
    queryFn: ({ signal }) => fetchSessionOffers(sessionId, signal),
  });
  const showPanel = canAssign && session.data?.state === 'unassigned';
  const tutors = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'tutors'],
    queryFn: ({ signal }) => fetchTutorOptions(signal),
    enabled: showPanel,
  });

  let state: SessionState;
  const failed = [session, offers].find((query) => query.isError);
  if (failed) {
    state = {
      status: 'error',
      error: failed.error,
      onRetry: () => {
        void session.refetch();
        void offers.refetch();
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
  const offerPanel =
    showPanel && detail && tutors.data ? (
      <OfferPanel
        key={detail.version}
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
        onOffered={(created) => {
          setOffered({
            tutorName: created.offer.tutor.display_name,
            warnings: created.warnings,
          });
          void client.invalidateQueries({ queryKey: sessionKey(sessionId) });
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
      />
    </div>
  );
}
