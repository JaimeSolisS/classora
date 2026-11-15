import { useQuery } from '@tanstack/react-query';
import { TriangleAlert } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { todayInZone } from '@/lib/time';
import {
  createSchedule,
  fetchSchedules,
  reportPath,
  schedulesKey,
  setScheduleActive,
  type ReportSchedule,
  type ScheduleCreate,
} from './api';
import { ReportScheduleDialog } from './ReportScheduleDialog';
import { classifyScheduleError } from './scheduleRules';
import { usePeriodText } from './text';

// "Report schedule" (#316 E1-E5), on /students/:studentId/progress-reports
// for progress_report.review holders (the page decides): which current tutor
// gets an empty weekly or monthly draft after each period ends. Each active
// row shows the tutor, the cadence, the subject (or "All subjects"), "Since
// {date}", what exists for the latest ended period (a link to the report
// once a reviewer may open it; drafts are the tutor's) and, as text, why no
// draft would be created now. Inactive rows are listed under "Inactive
// schedules" with "Reactivate". "Add schedule" opens the add dialog (an
// aria-disabled button with its reason when nobody teaches the student).
//
// Focus: Cancel and Escape return it to the button that opened the dialog;
// after an add (once S1 is refetched) it returns to "Add schedule"; after a
// deactivation or reactivation it goes to the status message; after
// "Reload" on a conflict, to the panel heading.

type Change = { schedule: ReportSchedule; activate: boolean };
type FocusTarget = 'add' | 'notice' | 'heading' | 'trigger';

export interface ReportSchedulePanelProps {
  studentId: string;
  /** The student's name, once known. */
  studentName: string | null;
  /** Tests and stories: the instant whose center-local date is "today". */
  now?: Date;
}

export function ReportSchedulePanel({
  studentId,
  studentName,
  now,
}: ReportSchedulePanelProps) {
  const { t } = useTranslation();
  const timeZone = useCenterTimeZone();
  const today = todayInZone(timeZone, now);
  const id = useId();
  const query = useQuery({
    queryKey: schedulesKey(studentId),
    queryFn: ({ signal }) => fetchSchedules(studentId, signal),
    retry: false,
  });
  const [adding, setAdding] = useState(false);
  const [change, setChange] = useState<Change | null>(null);
  const [notice, setNotice] = useState<
    'added' | 'deactivated' | 'reactivated' | null
  >(null);
  const [focus, setFocus] = useState<{ n: number; to: FocusTarget } | null>(
    null,
  );
  const headingRef = useRef<HTMLHeadingElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const student = studentName ?? t('progressReports.schedule.thisStudent');

  useEffect(() => {
    if (!focus) {
      return;
    }
    const timer = window.setTimeout(() => {
      const target = {
        add: addRef.current,
        notice: noticeRef.current,
        heading: headingRef.current,
        trigger: triggerRef.current,
      }[focus.to];
      if (target?.isConnected) {
        target.focus();
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focus]);

  function focusNext(to: FocusTarget) {
    setFocus((current) => ({ n: (current?.n ?? 0) + 1, to }));
  }

  function remember(event: { currentTarget: HTMLElement }) {
    triggerRef.current = event.currentTarget;
  }

  async function add(body: ScheduleCreate) {
    await createSchedule(studentId, body);
    await query.refetch();
    setAdding(false);
    setNotice('added');
    focusNext('add');
  }

  async function confirmChange(target: Change) {
    await setScheduleActive(target.schedule, target.activate);
    await query.refetch();
    setChange(null);
    setNotice(target.activate ? 'reactivated' : 'deactivated');
    focusNext('notice');
  }

  async function reload() {
    await query.refetch();
    setChange(null);
    focusNext('heading');
  }

  const data = query.data;
  const active = data?.items.filter((item) => item.is_active) ?? [];
  const inactive = data?.items.filter((item) => !item.is_active) ?? [];
  const noTutor = data !== undefined && data.eligible_authors.length === 0;

  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2
        id={`${id}-heading`}
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        {t('progressReports.schedule.heading')}
      </h2>
      <p className="text-sm">{t('progressReports.schedule.intro')}</p>
      {notice ? (
        <p
          ref={noticeRef}
          role="status"
          tabIndex={-1}
          className="text-sm font-medium outline-none"
        >
          {t(`progressReports.schedule.notices.${notice}`)}
        </p>
      ) : null}
      {query.isPending ? (
        <LoadingState rows={2} />
      ) : query.isError ? (
        <ErrorAlert error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <>
          <div className="flex min-w-0 flex-col gap-1">
            <Button
              ref={addRef}
              type="button"
              variant="outline"
              className="self-start"
              aria-disabled={noTutor ? true : undefined}
              aria-describedby={noTutor ? `${id}-no-tutor` : undefined}
              onClick={(event) => {
                if (noTutor) {
                  return;
                }
                remember(event);
                setNotice(null);
                setAdding(true);
              }}
            >
              {t('progressReports.schedule.add')}
            </Button>
            {noTutor ? (
              <p id={`${id}-no-tutor`} className="text-sm">
                {t('progressReports.schedule.noTutor', { student })}
              </p>
            ) : null}
          </div>
          {data?.items.length === 0 ? (
            <p className="text-sm">{t('progressReports.schedule.empty')}</p>
          ) : null}
          {active.length > 0 ? (
            <ul className="flex min-w-0 flex-col gap-3">
              {active.map((schedule) => (
                <ScheduleRow
                  key={schedule.id}
                  schedule={schedule}
                  student={student}
                  onChange={(event) => {
                    remember(event);
                    setNotice(null);
                    setChange({ schedule, activate: false });
                  }}
                />
              ))}
            </ul>
          ) : null}
          {inactive.length > 0 ? (
            <section className="flex min-w-0 flex-col gap-3">
              <h3 className="text-base font-semibold">
                {t('progressReports.schedule.inactive')}
              </h3>
              <ul className="flex min-w-0 flex-col gap-3">
                {inactive.map((schedule) => (
                  <ScheduleRow
                    key={schedule.id}
                    schedule={schedule}
                    student={student}
                    onChange={(event) => {
                      remember(event);
                      setNotice(null);
                      setChange({ schedule, activate: true });
                    }}
                  />
                ))}
              </ul>
            </section>
          ) : null}
          <ReportScheduleDialog
            open={adding}
            today={today}
            authors={data?.eligible_authors ?? []}
            onAdd={add}
            onCancel={() => {
              setAdding(false);
              focusNext('trigger');
            }}
          />
          <ScheduleChangeDialog
            change={change}
            onConfirm={confirmChange}
            onReload={reload}
            onCancel={() => {
              setChange(null);
              focusNext('trigger');
            }}
          />
        </>
      )}
    </section>
  );
}

function ScheduleRow({
  schedule,
  student,
  onChange,
}: {
  schedule: ReportSchedule;
  student: string;
  onChange: (event: { currentTarget: HTMLElement }) => void;
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const periods = usePeriodText();
  const tutor = schedule.author.display_name;
  const summary = t('progressReports.schedule.summary', {
    cadence: t(`progressReports.kinds.${schedule.cadence}`),
    subject: schedule.subject
      ? schedule.subject.name
      : t('progressReports.schedule.allSubjects'),
  });
  const latest = schedule.latest_period;
  const action = schedule.is_active
    ? t('progressReports.schedule.deactivate')
    : t('progressReports.schedule.reactivate');
  return (
    <li className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-sm">
      <p className="font-medium break-words">{tutor}</p>
      <p className="break-words">{summary}</p>
      <p>
        {t('progressReports.schedule.since', {
          date: formatLocalDate(schedule.starts_on),
        })}
      </p>
      {latest ? (
        <p className="break-words">
          {periods.period({
            kind: schedule.cadence,
            period_start: latest.period_start,
            period_end: latest.period_end,
          })}
          {': '}
          {latest.report_id ? (
            <Link to={reportPath(latest.report_id)} className={TEXT_LINK_CLASS}>
              {t(`progressReports.schedule.periodStatus.${latest.status}`)}
            </Link>
          ) : (
            t(`progressReports.schedule.periodStatus.${latest.status}`)
          )}
        </p>
      ) : null}
      {schedule.ineligible_reason ? (
        <p className="flex items-start gap-1.5 font-medium break-words">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0"
          />
          <span>
            {t(
              `progressReports.schedule.ineligible.${schedule.ineligible_reason}`,
              {
                tutor,
                student,
              },
            )}
          </span>
        </p>
      ) : null}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="mt-1 self-start"
        aria-label={t('progressReports.schedule.actionLabel', {
          action,
          tutor,
          summary,
        })}
        onClick={onChange}
      >
        {action}
      </Button>
    </li>
  );
}

function ScheduleChangeDialog({
  change,
  onConfirm,
  onReload,
  onCancel,
}: {
  change: Change | null;
  onConfirm: (change: Change) => Promise<void>;
  onReload: () => Promise<void>;
  onCancel: () => void;
}) {
  const sending = useRef(false);
  return (
    <Dialog
      open={change !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onCancel();
        }
      }}
    >
      {change ? (
        <ChangeContent
          key={change.schedule.id}
          change={change}
          onConfirm={onConfirm}
          onReload={onReload}
          onCancel={onCancel}
          sendingRef={sending}
        />
      ) : null}
    </Dialog>
  );
}

function ChangeContent({
  change,
  onConfirm,
  onReload,
  onCancel,
  sendingRef,
}: {
  change: Change;
  onConfirm: (change: Change) => Promise<void>;
  onReload: () => Promise<void>;
  onCancel: () => void;
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const failureRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ error: unknown } | null>(null);

  useEffect(() => {
    if (failure === null) {
      return;
    }
    const timer = window.setTimeout(() => failureRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [failure]);

  async function confirm() {
    if (sendingRef.current) {
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    setFailure(null);
    try {
      await onConfirm(change);
    } catch (caught) {
      setFailure({ error: caught });
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }

  const activate = change.activate;
  const conflict =
    failure !== null &&
    classifyScheduleError(failure.error).kind === 'conflict';

  return (
    <DialogContent
      onCloseAutoFocus={(event) => event.preventDefault()}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>
          {activate
            ? t('progressReports.schedule.reactivateTitle')
            : t('progressReports.schedule.deactivateTitle')}
        </DialogTitle>
        <DialogDescription>
          {activate
            ? t('progressReports.schedule.reactivateText')
            : t('progressReports.schedule.deactivateText')}
        </DialogDescription>
      </DialogHeader>
      {failure !== null ? (
        <div ref={failureRef} tabIndex={-1} className="outline-none">
          <ErrorAlert
            error={failure.error}
            message={
              conflict ? t('progressReports.schedule.conflict') : undefined
            }
            retryLabel={conflict ? t('academics.reload') : undefined}
            onRetry={conflict ? () => void onReload() : undefined}
          />
        </div>
      ) : null}
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={onCancel}
        >
          {t('progressReports.cancel')}
        </Button>
        <Button
          type="button"
          variant={activate ? 'default' : 'destructive'}
          disabled={busy}
          onClick={() => void confirm()}
        >
          {busy ? (
            <Spinner
              label={
                activate
                  ? t('progressReports.schedule.reactivating')
                  : t('progressReports.schedule.deactivating')
              }
              showLabel
            />
          ) : activate ? (
            t('progressReports.schedule.reactivate')
          ) : (
            t('progressReports.schedule.deactivate')
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
