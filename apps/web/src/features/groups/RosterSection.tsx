import { ShieldCheck } from 'lucide-react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { StudentStatusBadge } from '@/features/students/StudentBadges';
import { useFormatters } from '@/lib/formatters';
import { toLocalDate } from '@/lib/time';
import type { GroupEnrollmentItem } from './api';
import { rosterParts } from './rules';

// The roster (#234 C6), staff only: the active enrollments by student name
// (enrolled on, by whom, "Overridden" as text and an icon), then the
// withdrawn ones, newest first, with the reason (or "No reason given").
// An inactive student shows the #33 "Inactive" badge. "Withdraw" on each
// active row for schedule.manage holders (`onWithdraw`). #387 B1: a
// withdrawn row with no withdrawal settlement yet gets "Settle billing"
// (`settleIds`, `onSettle`) for billing users.

export interface RosterSectionProps {
  roster: readonly GroupEnrollmentItem[];
  timeZone: string;
  /** Holders of schedule.manage; none: no "Withdraw". */
  onWithdraw?: (item: GroupEnrollmentItem) => void;
  /** #387 B1: the withdrawn enrollments that can be settled. */
  settleIds?: ReadonlySet<string>;
  onSettle?: (item: GroupEnrollmentItem) => void;
}

export function RosterSection({
  roster,
  timeZone,
  onWithdraw,
  settleIds,
  onSettle,
}: RosterSectionProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const { active, withdrawn } = rosterParts(roster);
  const day = (instant: string) =>
    formatLocalDate(toLocalDate(instant, timeZone));

  function student(item: GroupEnrollmentItem) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium break-words">
          {item.student.display_name}
        </span>
        {item.student.is_active ? null : <StudentStatusBadge active={false} />}
      </div>
    );
  }

  return (
    <section aria-labelledby={`${id}-roster`} className="flex flex-col gap-4">
      <h2 id={`${id}-roster`} className="text-lg font-semibold">
        {t('groups.roster.heading')}
      </h2>
      <div className="flex flex-col gap-3">
        <h3 id={`${id}-active`} className="font-medium">
          {t('groups.roster.active', { count: active.length })}
        </h3>
        {active.length === 0 ? (
          <p className="text-sm">{t('groups.roster.none')}</p>
        ) : (
          <Table aria-labelledby={`${id}-active`}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">{t('groups.roster.student')}</TableHead>
                <TableHead scope="col">
                  {t('groups.roster.enrolledAt')}
                </TableHead>
                <TableHead scope="col">
                  {t('groups.roster.enrolledBy')}
                </TableHead>
                {onWithdraw ? (
                  <TableHead scope="col">
                    <span className="sr-only">
                      {t('groups.roster.actions')}
                    </span>
                  </TableHead>
                ) : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {active.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-2">
                      {student(item)}
                      {item.overridden ? (
                        <span className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 text-xs font-medium">
                          <ShieldCheck
                            aria-hidden="true"
                            className="size-3.5 shrink-0"
                          />
                          {t('groups.roster.overridden')}
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell>{day(item.enrolled_at)}</TableCell>
                  <TableCell>{item.enrolled_by.display_name}</TableCell>
                  {onWithdraw ? (
                    <TableCell>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('groups.roster.withdrawOf', {
                          name: item.student.display_name,
                        })}
                        onClick={() => onWithdraw(item)}
                      >
                        {t('groups.roster.withdraw')}
                      </Button>
                    </TableCell>
                  ) : null}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      {withdrawn.length > 0 ? (
        <div className="flex flex-col gap-3">
          <h3 id={`${id}-withdrawn`} className="font-medium">
            {t('groups.roster.withdrawn', { count: withdrawn.length })}
          </h3>
          <Table aria-labelledby={`${id}-withdrawn`}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">{t('groups.roster.student')}</TableHead>
                <TableHead scope="col">
                  {t('groups.roster.withdrawnAt')}
                </TableHead>
                <TableHead scope="col">
                  {t('groups.roster.withdrawnBy')}
                </TableHead>
                <TableHead scope="col">{t('groups.roster.reason')}</TableHead>
                {onSettle ? (
                  <TableHead scope="col">
                    <span className="sr-only">
                      {t('groups.roster.actions')}
                    </span>
                  </TableHead>
                ) : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {withdrawn.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>{student(item)}</TableCell>
                  <TableCell>
                    {item.withdrawn_at ? day(item.withdrawn_at) : ''}
                  </TableCell>
                  <TableCell>{item.withdrawn_by?.display_name ?? ''}</TableCell>
                  <TableCell className="break-words whitespace-normal">
                    {item.withdrawal_reason ?? t('groups.roster.noReason')}
                  </TableCell>
                  {onSettle ? (
                    <TableCell>
                      {settleIds?.has(item.id) ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={t(
                            'billing.withdrawals.settle.actionFor',
                            {
                              name: item.student.display_name,
                            },
                          )}
                          onClick={() => onSettle(item)}
                        >
                          {t('billing.withdrawals.settle.action')}
                        </Button>
                      ) : null}
                    </TableCell>
                  ) : null}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </section>
  );
}
