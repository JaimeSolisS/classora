import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useId, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link, useBlocker } from 'react-router';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Counter } from '@/features/academics/Counter';
import { ConfirmDialog } from '@/features/academics/evaluations/ConfirmDialog';
import { LeaveDialog } from '@/features/academics/LeaveDialog';
import { SMALL_LINK_CLASS, TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import { useFormatters } from '@/lib/formatters';
import { useReportReviewer, useRoles } from './access';
import {
  studentReportPath,
  studentReportsPath,
  type ProgressReport,
  type ReportUpdate,
} from './api';
import { NarrativeView, ReportHistory } from './Narrative';
import { ProgressReportFacts } from './ProgressReportFacts';
import { ReasonDialog } from './ReasonDialog';
import { ReportFailure, type ReportFailureState } from './ReportFailure';
import {
  classifyRefusal,
  formerAuthor,
  NARRATIVE_FIELDS,
  NARRATIVE_MAX,
  narrativeDiffers,
  narrativeSchema,
  narrativeValues,
  patchBody,
  selfReviewBlocked,
  summaryMissing,
  viewMode,
  type NarrativeField,
  type NarrativeValues,
  type ReportMessage,
} from './rules';
import { StatusBadge } from './StatusBadge';
import { usePeriodText, useReportMessage } from './text';

// One progress report (#315 C, E), presentational and phone-first. The mode
// comes from R3's flags (rules.viewMode):
// - edit (can_edit): the changes-requested banner, Summary (required to
//   submit), Strengths, Areas for improvement and Next steps (React Hook
//   Form + Zod, counters), the facts preview with "Refresh facts", and Save
//   (only the changed keys), Submit / Resubmit (a blank summary is refused
//   first; a dirty form is saved first) and Delete, each per its flag;
// - review (a decision flag, or a reviewer on a non-draft report): the
//   narrative, the frozen facts, the history and Approve / Request changes
//   / Retract strictly by the flags; a reviewer's own submitted report
//   (not an Owner) shows them disabled with the reason;
// - read: the author's state notices, the narrative, the frozen facts, the
//   history.
// Focus: Cancel or Escape in a dialog returns it to the button that opened
// it; Save and Refresh move it to the status message; a submit or a
// decision to the heading (with the status message); a refused one to its
// failure region or the first invalid field. Every move runs from one 0 ms
// effect, after the render it waits for. Leaving with unsaved changes asks
// first; Reload with unsaved changes asks first.

export interface ReportActions {
  onSave: (body: ReportUpdate) => Promise<ProgressReport>;
  onSubmit: () => Promise<ProgressReport>;
  onDelete: () => Promise<void>;
  onApprove: () => Promise<ProgressReport>;
  onRequestChanges: (reason: string) => Promise<ProgressReport>;
  onRetract: (reason: string) => Promise<ProgressReport>;
  /** Refetches R3 (a fresh facts preview). */
  onRefreshFacts: () => Promise<void>;
  /** Refetches after a refusal; the page remounts the view. */
  onReload: () => void;
}

type DialogName =
  'submit' | 'delete' | 'approve' | 'changes' | 'retract' | 'discard';

type FocusTarget = 'notice' | 'failure' | 'trigger' | 'heading' | 'invalid';

export interface ReportViewProps {
  report: ProgressReport;
  actions: ReportActions;
  /** A notice to show first (after the create). */
  initialNotice?: string | null;
  /** Focus the heading when it mounts (after a Reload). */
  focusHeadingOnMount?: boolean;
  /** Stories: open a dialog. */
  initialDialog?: DialogName | null;
  /** Stories: a failure to show. */
  initialFailure?: ReportFailureState | null;
  /** Stories: field errors to show. */
  initialErrors?: Partial<Record<NarrativeField, ReportMessage>>;
}

const LABELS: Record<NarrativeField, string> = {
  summary: 'progressReports.fields.summary',
  strengths: 'progressReports.fields.strengths',
  areas_for_improvement: 'progressReports.fields.areas',
  next_steps: 'progressReports.fields.nextSteps',
};

export function ReportView({
  report,
  actions,
  initialNotice = null,
  focusHeadingOnMount = false,
  initialDialog = null,
  initialFailure = null,
  initialErrors,
}: ReportViewProps) {
  const { t } = useTranslation();
  const tAny = t as unknown as (key: string, options?: object) => string;
  const { formatInstant } = useFormatters();
  const periods = usePeriodText();
  const message = useReportMessage();
  const id = useId();
  const reviewer = useReportReviewer();
  const owner = useRoles().includes('owner');
  const mode = viewMode(report, { reviewer });
  const blocked = selfReviewBlocked(report, { reviewer, owner });
  const student = report.student.display_name;
  const author = report.author.display_name;

  const [baseline, setBaseline] = useState<NarrativeValues>(() =>
    narrativeValues(report),
  );
  const form = useForm<NarrativeValues>({
    resolver: zodResolver(narrativeSchema),
    defaultValues: baseline,
    shouldFocusError: false,
  });
  const [seededErrors] = useState(initialErrors);
  useEffect(() => {
    for (const field of NARRATIVE_FIELDS) {
      const found = seededErrors?.[field];
      if (found) {
        form.setError(field, { message: found });
      }
    }
  }, [form, seededErrors]);
  const watched = useWatch({ control: form.control }) as NarrativeValues;
  const values: NarrativeValues = { ...baseline, ...watched };
  const errors = form.formState.errors;

  const [notice, setNotice] = useState<string | null>(initialNotice);
  const [failure, setFailure] = useState<ReportFailureState | null>(
    initialFailure,
  );
  const [dialog, setDialog] = useState<DialogName | null>(initialDialog);
  const [busy, setBusy] = useState<'save' | 'submit' | 'refresh' | null>(null);
  const leaving = useRef(false);
  const trigger = useRef<HTMLElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [focusTarget, setFocusTarget] = useState<{
    n: number;
    to: FocusTarget;
  } | null>(
    initialNotice
      ? { n: 0, to: 'notice' }
      : focusHeadingOnMount
        ? { n: 0, to: 'heading' }
        : null,
  );

  const dirty = mode === 'edit' && narrativeDiffers(values, baseline);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      !leaving.current &&
      currentLocation.pathname !== nextLocation.pathname,
  );

  function focusNext(to: FocusTarget) {
    setFocusTarget((current) => ({ n: (current?.n ?? 0) + 1, to }));
  }

  useEffect(() => {
    if (!focusTarget) {
      return;
    }
    const timer = window.setTimeout(() => {
      const { to } = focusTarget;
      let target: HTMLElement | null | undefined = null;
      if (to === 'notice') {
        target = noticeRef.current;
      } else if (to === 'failure') {
        target = failureRef.current;
      } else if (to === 'trigger') {
        target = trigger.current?.isConnected ? trigger.current : null;
      } else if (to === 'invalid') {
        target = formRef.current?.querySelector<HTMLElement>(
          '[aria-invalid="true"]',
        );
      }
      (target ?? headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTarget]);

  function done(text: string, to: FocusTarget = 'notice') {
    setFailure(null);
    setNotice(text);
    focusNext(to);
  }

  /** Shows a refused write on its fields or in the failure region; typed
   * values are kept. */
  function refused(caught: unknown) {
    const refusal = classifyRefusal(caught);
    setNotice(null);
    if (refusal.kind === 'missing') {
      return;
    }
    if (refusal.kind === 'fields') {
      setFailure(null);
      for (const field of NARRATIVE_FIELDS) {
        const found = refusal.errors[field];
        if (found) {
          form.setError(field, { message: found });
        }
      }
      focusNext('invalid');
      return;
    }
    setFailure({ refusal, error: caught });
    focusNext('failure');
  }

  function reload(from?: HTMLElement) {
    if (dirty) {
      trigger.current = from ?? null;
      setDialog('discard');
      return;
    }
    setFailure(null);
    actions.onReload();
  }

  /** Saves the changed keys; true when stored (or nothing changed). */
  async function save(current: NarrativeValues): Promise<boolean> {
    const body = patchBody(current, baseline, report.version);
    if (!body) {
      return true;
    }
    try {
      const stored = await actions.onSave(body);
      const fresh = narrativeValues(stored);
      setBaseline(fresh);
      form.reset(fresh);
      return true;
    } catch (caught) {
      refused(caught);
      return false;
    }
  }

  const onSave = form.handleSubmit(
    async (current) => {
      if (busy) {
        return;
      }
      setBusy('save');
      const stored = await save(current);
      setBusy(null);
      if (stored) {
        done(t('progressReports.notices.saved'));
      }
    },
    () => {
      setNotice(null);
      focusNext('invalid');
    },
  );

  async function startSubmit(from: HTMLElement) {
    if (busy) {
      return;
    }
    trigger.current = from;
    const valid = await form.trigger();
    if (!valid) {
      setNotice(null);
      focusNext('invalid');
      return;
    }
    const current = form.getValues();
    if (summaryMissing(current)) {
      setNotice(null);
      form.setError('summary', {
        message: 'progressReports.editor.summaryRequired',
      });
      focusNext('invalid');
      return;
    }
    if (narrativeDiffers(current, baseline)) {
      setBusy('submit');
      const stored = await save(current);
      setBusy(null);
      if (!stored) {
        return;
      }
    }
    setFailure(null);
    setDialog('submit');
  }

  async function refreshFacts() {
    if (busy) {
      return;
    }
    setBusy('refresh');
    try {
      await actions.onRefreshFacts();
      setBusy(null);
      done(t('progressReports.notices.factsUpdated'));
    } catch (caught) {
      setBusy(null);
      refused(caught);
    }
  }

  function openDialog(name: DialogName, from: HTMLElement) {
    trigger.current = from;
    setNotice(null);
    setDialog(name);
  }

  function cancelDialog() {
    setDialog(null);
    focusNext('trigger');
  }

  const resubmit = report.status === 'changes_requested';
  const decisionDone = (key: string) => {
    setDialog(null);
    done(tAny(key), 'heading');
  };

  // ------------------------------------------------------------- the parts

  const header = (
    <header className="flex min-w-0 flex-col gap-2">
      <h1
        ref={headingRef}
        tabIndex={-1}
        className="text-2xl font-semibold outline-none"
      >
        {t('progressReports.reportTitle')}
      </h1>
      <p className="text-base font-medium break-words">
        <Link
          to={studentReportsPath(report.student.id)}
          className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {student}
        </Link>
      </p>
      <p className="text-sm break-words">{periods.line(report)}</p>
      <p className="text-sm break-words">
        {report.subject?.name ?? t('progressReports.allSubjects')}
      </p>
      {report.is_own ? null : (
        <p className="text-sm break-words text-muted-foreground">
          {t('progressReports.by', { name: author })}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <StatusBadge status={report.status} />
      </div>
    </header>
  );

  const banner =
    mode === 'edit' &&
    report.status === 'changes_requested' &&
    report.changes_requested ? (
      <div className="flex min-w-0 flex-col gap-1 rounded-md border border-border bg-muted p-4 text-sm">
        <p className="font-medium">
          {t('progressReports.editor.changesRequested', {
            by: report.changes_requested.by.display_name,
            date: formatInstant(report.changes_requested.at, 'date'),
          })}
        </p>
        <p className="break-words whitespace-pre-line">
          {report.changes_requested.reason}
        </p>
      </div>
    ) : null;

  let stateNotice = null;
  if (mode === 'read' && report.is_own) {
    if (formerAuthor(report)) {
      stateNotice = (
        <p className="text-sm">
          {t('progressReports.read.formerAuthor', { student })}
        </p>
      );
    } else if (report.status === 'submitted' && report.submitted_at) {
      stateNotice = (
        <p className="text-sm">
          {t('progressReports.read.waiting', {
            at: formatInstant(report.submitted_at, 'dateTime'),
          })}
        </p>
      );
    } else if (report.status === 'approved_for_family' && report.approved) {
      stateNotice = (
        <div className="flex min-w-0 flex-col items-start gap-1 text-sm">
          <p className="break-words">
            {t('progressReports.read.approved', {
              by: report.approved.by.display_name,
              date: formatInstant(report.approved.at, 'date'),
              student,
            })}
          </p>
          <Link
            to={studentReportPath(report.student.id, report.id)}
            className={SMALL_LINK_CLASS}
          >
            {t('progressReports.read.asFamily')}
          </Link>
        </div>
      );
    } else if (report.status === 'retracted' && report.retracted) {
      stateNotice = (
        <div className="flex min-w-0 flex-col items-start gap-1 text-sm">
          <p className="break-words">
            {t('progressReports.read.retracted', {
              by: report.retracted.by.display_name,
              date: formatInstant(report.retracted.at, 'date'),
            })}
          </p>
          <p className="break-words whitespace-pre-line">
            {t('progressReports.read.reason', {
              reason: report.retracted.reason,
            })}
          </p>
          <Link
            to={studentReportsPath(report.student.id)}
            className={SMALL_LINK_CLASS}
          >
            {t('progressReports.read.toStudentReports')}
          </Link>
        </div>
      );
    }
  }

  const editForm = (
    <form
      id={`${id}-form`}
      ref={formRef}
      method="post"
      noValidate
      onSubmit={(event) => void onSave(event)}
      className="flex min-w-0 flex-col gap-5"
    >
      {NARRATIVE_FIELDS.map((field) => {
        const fieldId = `${id}-${field}`;
        const error = message(errors[field]?.message);
        return (
          <div key={field} className="flex min-w-0 flex-col gap-1.5">
            <label htmlFor={fieldId} className="text-sm font-medium">
              {tAny(LABELS[field])}
            </label>
            {field === 'summary' ? (
              <p
                id={`${fieldId}-help`}
                className="text-sm text-muted-foreground"
              >
                {t('progressReports.editor.summaryHelp')}
              </p>
            ) : null}
            <textarea
              id={fieldId}
              rows={field === 'summary' ? 6 : 4}
              className={TEXTAREA_CLASS}
              aria-invalid={error ? true : undefined}
              aria-describedby={[
                field === 'summary' ? `${fieldId}-help` : null,
                error ? `${fieldId}-error` : null,
                `${fieldId}-counter`,
              ]
                .filter(Boolean)
                .join(' ')}
              {...form.register(field)}
            />
            <FieldError id={`${fieldId}-error`} message={error} />
            <Counter
              id={`${fieldId}-counter`}
              value={values[field]}
              max={NARRATIVE_MAX[field]}
            />
          </div>
        );
      })}
    </form>
  );

  const factsPanel =
    mode === 'edit' ? (
      report.facts_preview ? (
        <ProgressReportFacts
          facts={report.facts_preview}
          heading={t('progressReports.facts.preview')}
          note={t('progressReports.facts.previewNote')}
          action={
            <RefreshButton busy={busy === 'refresh'} onClick={refreshFacts} />
          }
        />
      ) : (
        <section className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-4">
          <h2 className="text-lg font-semibold">
            {t('progressReports.facts.preview')}
          </h2>
          <p className="text-sm">{t('progressReports.facts.previewMissing')}</p>
          <RefreshButton busy={busy === 'refresh'} onClick={refreshFacts} />
        </section>
      )
    ) : report.facts ? (
      <ProgressReportFacts
        facts={report.facts}
        heading={
          report.facts_computed_at
            ? t('progressReports.facts.asSubmitted', {
                at: formatInstant(report.facts_computed_at, 'dateTime'),
              })
            : t('progressReports.facts.title')
        }
      />
    ) : report.facts_preview ? (
      <ProgressReportFacts
        facts={report.facts_preview}
        heading={t('progressReports.facts.preview')}
      />
    ) : null;

  const explanationId = `${id}-self-review`;
  const reviewActions =
    mode === 'review' ? (
      blocked ? (
        <div className="flex min-w-0 flex-col gap-2">
          <p id={explanationId} className="text-sm">
            {t('progressReports.review.selfReview')}
          </p>
          <div className="flex flex-wrap gap-2">
            {(
              [
                'progressReports.actions.approve',
                'progressReports.actions.requestChanges',
                'progressReports.actions.retract',
              ] as const
            ).map((key) => (
              <Button
                key={key}
                type="button"
                variant="outline"
                aria-disabled="true"
                aria-describedby={explanationId}
                className="cursor-not-allowed opacity-60"
                onClick={(event) => event.preventDefault()}
              >
                {t(key)}
              </Button>
            ))}
          </div>
        </div>
      ) : report.can_approve ||
        report.can_request_changes ||
        report.can_retract ? (
        <div className="flex flex-wrap gap-2">
          {report.can_approve ? (
            <Button
              type="button"
              onClick={(event) => openDialog('approve', event.currentTarget)}
            >
              {t('progressReports.actions.approve')}
            </Button>
          ) : null}
          {report.can_request_changes ? (
            <Button
              type="button"
              variant="outline"
              onClick={(event) => openDialog('changes', event.currentTarget)}
            >
              {t('progressReports.actions.requestChanges')}
            </Button>
          ) : null}
          {report.can_retract ? (
            <Button
              type="button"
              variant="destructive"
              onClick={(event) => openDialog('retract', event.currentTarget)}
            >
              {t('progressReports.actions.retract')}
            </Button>
          ) : null}
        </div>
      ) : null
    ) : null;

  const editActions =
    mode === 'edit' ? (
      <div className="flex flex-wrap gap-2">
        <Button type="submit" form={`${id}-form`} disabled={busy !== null}>
          {busy === 'save' ? (
            <Spinner label={t('progressReports.busy.saving')} showLabel />
          ) : (
            t('progressReports.actions.save')
          )}
        </Button>
        {report.can_submit ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy !== null}
            onClick={(event) => void startSubmit(event.currentTarget)}
          >
            {busy === 'submit' ? (
              <Spinner label={t('progressReports.busy.saving')} showLabel />
            ) : resubmit ? (
              t('progressReports.actions.resubmit')
            ) : (
              t('progressReports.actions.submit')
            )}
          </Button>
        ) : null}
        {report.can_delete ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy !== null}
            onClick={(event) => openDialog('delete', event.currentTarget)}
          >
            {t('progressReports.actions.delete')}
          </Button>
        ) : null}
      </div>
    ) : null;

  return (
    <div className="flex w-full max-w-5xl min-w-0 flex-col gap-6">
      {header}
      {notice ? (
        <p
          ref={noticeRef}
          role="status"
          tabIndex={-1}
          className="text-sm font-medium outline-none"
        >
          {notice}
        </p>
      ) : null}
      {failure ? (
        <ReportFailure
          ref={failureRef}
          failure={failure}
          mode={mode}
          author={author}
          student={student}
          onReload={() => reload(failureRef.current ?? undefined)}
        />
      ) : null}
      <div className="grid min-w-0 grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
        <div className="flex min-w-0 flex-col gap-5">
          {banner}
          {stateNotice}
          {mode === 'edit' ? editForm : <NarrativeView report={report} />}
          {mode === 'edit' ? null : <ReportHistory report={report} />}
        </div>
        {factsPanel ? <div className="min-w-0">{factsPanel}</div> : null}
      </div>
      {editActions}
      {reviewActions}

      <ConfirmDialog
        open={dialog === 'submit'}
        title={
          resubmit
            ? t('progressReports.submit.titleResubmit')
            : t('progressReports.submit.title')
        }
        description={t('progressReports.submit.description', { student })}
        confirmLabel={
          resubmit
            ? t('progressReports.actions.resubmit')
            : t('progressReports.actions.submit')
        }
        busyLabel={t('progressReports.busy.submitting')}
        cancelLabel={t('progressReports.cancel')}
        onCancel={cancelDialog}
        onConfirm={async () => {
          try {
            await actions.onSubmit();
            decisionDone('progressReports.notices.submitted');
          } catch (caught) {
            setDialog(null);
            refused(caught);
          }
        }}
      />
      <ConfirmDialog
        open={dialog === 'delete'}
        title={t('progressReports.delete.title')}
        description={
          resubmit
            ? `${t('progressReports.delete.description')} ${t('progressReports.delete.withdrawn')}`
            : t('progressReports.delete.description')
        }
        confirmLabel={t('progressReports.delete.confirm')}
        busyLabel={t('progressReports.busy.deleting')}
        cancelLabel={t('progressReports.cancel')}
        destructive
        onCancel={cancelDialog}
        onConfirm={async () => {
          leaving.current = true;
          try {
            await actions.onDelete();
          } catch (caught) {
            leaving.current = false;
            setDialog(null);
            refused(caught);
          }
        }}
      />
      <ConfirmDialog
        open={dialog === 'approve'}
        title={t('progressReports.approve.title')}
        description={t('progressReports.approve.description', { student })}
        confirmLabel={t('progressReports.actions.approve')}
        busyLabel={t('progressReports.busy.approving')}
        cancelLabel={t('progressReports.cancel')}
        onCancel={cancelDialog}
        onConfirm={async () => {
          try {
            await actions.onApprove();
            decisionDone('progressReports.notices.approved');
          } catch (caught) {
            setDialog(null);
            refused(caught);
          }
        }}
      />
      <ReasonDialog
        open={dialog === 'changes'}
        title={t('progressReports.changes.title')}
        description={t('progressReports.changes.description', { author })}
        confirmLabel={t('progressReports.actions.requestChanges')}
        busyLabel={t('progressReports.busy.sending')}
        onCancel={cancelDialog}
        onConfirm={async (reason) => {
          await actions.onRequestChanges(reason);
          decisionDone('progressReports.notices.changesRequested');
        }}
        onFailure={(caught) => {
          setDialog(null);
          refused(caught);
        }}
      />
      <ReasonDialog
        open={dialog === 'retract'}
        title={t('progressReports.retract.title')}
        description={t('progressReports.retract.description', { author })}
        warning={
          report.status === 'approved_for_family'
            ? t('progressReports.retract.warning', { student })
            : undefined
        }
        confirmLabel={t('progressReports.actions.retract')}
        busyLabel={t('progressReports.busy.retracting')}
        destructive
        onCancel={cancelDialog}
        onConfirm={async (reason) => {
          await actions.onRetract(reason);
          decisionDone('progressReports.notices.retracted');
        }}
        onFailure={(caught) => {
          setDialog(null);
          refused(caught);
        }}
      />
      <ConfirmDialog
        open={dialog === 'discard'}
        title={t('progressReports.discard.title')}
        description={t('progressReports.discard.description')}
        confirmLabel={t('progressReports.discard.confirm')}
        busyLabel={t('progressReports.discard.confirm')}
        cancelLabel={t('progressReports.discard.keep')}
        destructive
        onCancel={cancelDialog}
        onConfirm={() => {
          setDialog(null);
          setFailure(null);
          leaving.current = true;
          actions.onReload();
          return Promise.resolve();
        }}
      />
      <LeaveDialog blocker={blocker} />
    </div>
  );
}

function RefreshButton({
  busy,
  onClick,
}: {
  busy: boolean;
  onClick: () => Promise<void>;
}) {
  const { t } = useTranslation();
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="self-start"
      disabled={busy}
      onClick={() => void onClick()}
    >
      {busy ? (
        <Spinner label={t('progressReports.busy.refreshing')} showLabel />
      ) : (
        t('progressReports.actions.refreshFacts')
      )}
    </Button>
  );
}
