import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Button } from '@/components/ui/button';

// A page the signed-in user may not open (#28 F2): shown inside the layout at
// the same URL (no redirect, not a 404). UX only: the API refuses the data
// anyway (spec §16).
export function ForbiddenPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('auth.forbidden.title'));
  return (
    <div className="flex flex-col items-start gap-4">
      <h1 className="text-2xl font-semibold">{t('auth.forbidden.title')}</h1>
      <p className="text-muted-foreground">{t('auth.forbidden.message')}</p>
      <Button asChild variant="outline">
        <Link to="/">{t('auth.forbidden.backHome')}</Link>
      </Button>
    </div>
  );
}
