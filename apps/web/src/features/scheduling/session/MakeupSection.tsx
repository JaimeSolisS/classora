import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import { LINK_BUTTON_CLASS } from '@/features/students/styles';
import { cn } from '@/lib/utils';
import { MAKEUP_LINK_CLASS, MAKEUP_PARAM, sessionPath } from '../create/rules';
import type { ClassSessionDetail } from './api';
import { makeupStatus } from './rules';

// The makeup block of the staff session page (#436 A): what the session makes
// up, in every state (A3), and for a cancelled individual session whether it
// has or can have a makeup (A1, A2). The action needs `canSchedule`
// (schedule.manage, A4); the text and the links don't. Nothing to say: no
// section.

export interface MakeupSectionProps {
  session: ClassSessionDetail;
  timeZone: string;
  now: Date;
  canSchedule: boolean;
}

export function MakeupSection({
  session,
  timeZone,
  now,
  canSchedule,
}: MakeupSectionProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const status = makeupStatus(session, now);
  const makesUp = session.makeup_of_session_id;
  if (!status && !makesUp) {
    return null;
  }
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-1 rounded-md border border-border p-3"
    >
      <h2 id={id} className="font-semibold">
        {t('scheduling.makeup.heading')}
      </h2>
      {makesUp ? (
        <div className="flex flex-wrap items-center gap-x-2 text-sm">
          <span>{t('scheduling.makeup.of')}</span>
          <Link to={sessionPath(makesUp)} className={MAKEUP_LINK_CLASS}>
            {t('scheduling.makeup.openCancelled')}
          </Link>
        </div>
      ) : null}
      {status?.kind === 'scheduled' ? (
        <div className="flex flex-wrap items-center gap-x-2 text-sm">
          <span>{t('scheduling.makeup.scheduled')}</span>
          <Link
            to={sessionPath(status.sessionId)}
            className={MAKEUP_LINK_CLASS}
          >
            {t('scheduling.makeup.openMakeup')}
          </Link>
        </div>
      ) : null}
      {status?.kind === 'available' ? (
        <>
          <p className="text-sm">
            {t('scheduling.makeup.deadline', {
              when: formatInstant(status.until, {
                timeZone,
                language,
                style: 'dateTime',
              }),
              zone: t('scheduling.session.zone', { zone: timeZone }),
            })}
          </p>
          {canSchedule ? (
            <Link
              to={`/schedule/new?${MAKEUP_PARAM}=${encodeURIComponent(session.id)}`}
              className={cn(
                LINK_BUTTON_CLASS,
                'mt-1 h-auto min-h-11 w-fit md:min-h-9',
              )}
            >
              {t('scheduling.makeup.action')}
            </Link>
          ) : null}
        </>
      ) : null}
      {status?.kind === 'ended' ? (
        <p className="text-sm">{t('scheduling.makeup.ended')}</p>
      ) : null}
      {status?.kind === 'notAllowed' ? (
        <p className="text-sm">{t('scheduling.makeup.notAllowed')}</p>
      ) : null}
    </section>
  );
}
