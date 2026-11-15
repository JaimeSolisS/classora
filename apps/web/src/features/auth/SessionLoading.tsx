import { useTranslation } from 'react-i18next';
import { LoadingState } from '@/components/feedback/LoadingState';

// What a page under the guard shows while /auth/me is pending (#28 F2): busy,
// with a visually hidden "loading your session" message. Never the page and
// never a redirect.
export function SessionLoading() {
  const { t } = useTranslation();
  return <LoadingState label={t('auth.session.loading')} />;
}
