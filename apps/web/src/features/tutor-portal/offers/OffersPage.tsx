import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  acceptOffer,
  declineOffer,
  fetchOffers,
  OFFERS_KEY,
  type OfferTab,
} from './api';
import { OffersView, type OffersState } from './OffersView';

// /offers, "My offers" (#52 H1), for tutors (the route requires the tutor
// role; the API lists only the caller's own offers). The tab is in the URL
// (`?tab=answered`). After an answer, or a stale one, both tabs refresh.

export function OffersPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('offers.title'));
  const titleId = useId();
  const timeZone = useCenterTimeZone();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab: OfferTab =
    params.get('tab') === 'answered' ? 'answered' : 'pending';
  const list = useQuery({
    queryKey: [...OFFERS_KEY, tab],
    queryFn: ({ signal }) => fetchOffers(tab, signal),
  });

  function refresh() {
    void client.invalidateQueries({ queryKey: OFFERS_KEY });
  }

  let state: OffersState;
  if (list.isError) {
    state = {
      status: 'error',
      error: list.error,
      onRetry: () => void list.refetch(),
    };
  } else if (!list.data) {
    state = { status: 'loading' };
  } else {
    state = { status: 'ready', items: list.data.items };
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 id={titleId} className="text-2xl font-semibold">
        {t('offers.title')}
      </h1>
      <p className="max-w-3xl text-sm text-muted-foreground">
        {t('offers.intro')}
      </p>
      <OffersView
        tab={tab}
        onTab={(next) =>
          setParams(next === 'pending' ? {} : { tab: next }, { replace: true })
        }
        state={state}
        timeZone={timeZone}
        onAccept={async (offer) => {
          await acceptOffer(offer.id);
          refresh();
        }}
        onDecline={async (offer, note) => {
          await declineOffer(offer.id, note);
          refresh();
        }}
        onStale={refresh}
      />
    </div>
  );
}
