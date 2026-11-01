import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { ClassSessionDetail } from '../session/api';
import { MAKEUP_LINK_CLASS, sessionPath, type MakeupProblem } from './rules';

// Makeup mode's two notices on "Schedule a session" (#436): the session being
// made up with its deadline (B4), and why it can't be made up here, in place
// of the form (B2).

export function MakeupTargetNotice({
  target,
  timeZone,
}: {
  target: ClassSessionDetail;
  timeZone: string;
}) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const when = (instant: string) =>
    formatInstant(instant, { timeZone, language, style: 'dateTime' });
  const until = target.makeup?.until;
  return (
    <div className="flex flex-col gap-1 rounded-md border border-border p-3">
      <p className="font-medium break-words">
        {t('scheduling.makeup.notice', {
          subject: t('scheduling.success.subject', {
            subject: target.subject.name,
            level: target.subject.education_level_name,
          }),
          when: when(target.starts_at),
        })}
      </p>
      {until ? (
        <p className="text-sm">
          {t('scheduling.makeup.mustStartBy', {
            when: when(until),
            zone: t('scheduling.session.zone', { zone: timeZone }),
          })}
        </p>
      ) : null}
      <Link to={sessionPath(target.id)} className={MAKEUP_LINK_CLASS}>
        {t('scheduling.makeup.backToCancelled')}
      </Link>
    </div>
  );
}

export function MakeupUnusableNotice({ problem }: { problem: MakeupProblem }) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      className="flex w-full max-w-2xl flex-col gap-1 rounded-md border border-border p-3"
    >
      <p className="font-medium">
        {t(`scheduling.makeup.unusable.${problem}`)}
      </p>
      <Link to="/schedule/new" className={MAKEUP_LINK_CLASS}>
        {t('scheduling.makeup.plainForm')}
      </Link>
    </div>
  );
}
