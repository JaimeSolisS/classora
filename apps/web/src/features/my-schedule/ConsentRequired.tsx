import { LockKeyhole } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// An adult child who hasn't shared their schedule with this guardian (#67
// E3; the #35 consent, ADR-0007 D17): the message replaces the calendar and
// no schedule is requested. The same message answers a 403
// student_consent_required (the consent was revoked meanwhile).

export function ConsentRequired({ name }: { name: string }) {
  const { t } = useTranslation();
  return (
    <section
      aria-labelledby="my-schedule-consent"
      className="flex max-w-3xl gap-3 rounded-md border border-border bg-muted p-4 text-sm"
    >
      <LockKeyhole aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
      <div className="flex flex-col gap-2">
        <h2 id="my-schedule-consent" className="text-base font-semibold">
          {t('mySchedule.consent.title', { name })}
        </h2>
        <p>{t('consent.summary.adult')}</p>
        <p>{t('mySchedule.consent.how')}</p>
      </div>
    </section>
  );
}
