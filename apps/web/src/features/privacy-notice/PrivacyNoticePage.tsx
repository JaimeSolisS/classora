import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { safeReturnPath } from '@/features/auth/returnPath';
import { SESSION_QUERY_KEY } from '@/features/auth/session';
import { useSession } from '@/features/auth/useSession';
import {
  acceptPrivacyNotice,
  fetchPrivacyNotice,
  PRIVACY_NOTICE_QUERY_KEY,
} from './api';
import {
  PrivacyNoticeView,
  type PrivacyNoticeState,
} from './PrivacyNoticeView';

// /privacy-notice (#174 G1, G2). Under the guard with no permission: the
// guard sends every other page here while `privacy_notice_required` is true,
// with the page to return to in `next` (validated like /login's). "I accept"
// sends the version shown, refetches the session and, once the session shows
// the acceptance, goes back to `next` (replace).
// A 409 (a newer version was published meanwhile) refetches the notice and
// says so. Reached from the user menu once accepted, it shows the text and
// when it was accepted.
export function PrivacyNoticePage() {
  const { t } = useTranslation();
  useDocumentTitle(t('privacyNotice.title'));
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [changed, setChanged] = useState(false);
  const [returnTo, setReturnTo] = useState<string | null>(null);
  const session = useSession();
  const required =
    session.status === 'signedIn' && session.user.privacy_notice_required;

  // Leave only once the session shows the acceptance, so the guard does not
  // send the user straight back here.
  useEffect(() => {
    if (returnTo !== null && !required) {
      void navigate(returnTo, { replace: true });
    }
  }, [returnTo, required, navigate]);

  const query = useQuery({
    queryKey: PRIVACY_NOTICE_QUERY_KEY,
    queryFn: ({ signal }) => fetchPrivacyNotice(signal),
    staleTime: 0,
  });

  const mutation = useMutation({
    mutationFn: acceptPrivacyNotice,
    onMutate: () => setChanged(false),
    onSuccess: async () => {
      // The guard reads the session: refresh it before leaving.
      await queryClient.refetchQueries({
        queryKey: SESSION_QUERY_KEY,
        exact: true,
      });
      void queryClient.invalidateQueries({
        queryKey: PRIVACY_NOTICE_QUERY_KEY,
      });
      setReturnTo(safeReturnPath(searchParams.get('next')));
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.code === 'privacy_notice_outdated'
      ) {
        setChanged(true);
        void query.refetch();
      }
    },
  });

  let state: PrivacyNoticeState;
  if (query.data) {
    state = { status: 'ready', notice: query.data };
  } else if (query.isError) {
    state = {
      status: 'error',
      error: query.error,
      onRetry: () => void query.refetch(),
    };
  } else {
    state = { status: 'loading' };
  }

  const outdated =
    mutation.error instanceof ApiError &&
    mutation.error.code === 'privacy_notice_outdated';

  return (
    <PrivacyNoticeView
      state={state}
      accepting={mutation.isPending}
      changed={changed}
      acceptError={mutation.isError && !outdated ? mutation.error : null}
      onAccept={(version) => mutation.mutate(version)}
    />
  );
}
