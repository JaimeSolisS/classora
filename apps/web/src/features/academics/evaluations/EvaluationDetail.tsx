import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useBlocker } from 'react-router';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import { LeaveDialog } from '../LeaveDialog';
import { BADGE_CLASS } from '../styles';
import type { Evaluation, EvaluationPatch } from './api';
import { ConfirmDialog } from './ConfirmDialog';
import { EvaluationFields } from './EvaluationFields';
import { EvaluationReadView } from './EvaluationReadView';
import { Failure, type FailureState } from './Failure';
import { FinalizeDialog } from './FinalizeDialog';
import {
  classifyRefusal,
  criteriaOf,
  evaluationValues,
  hasErrors,
  missingCriteria,
  NO_ERRORS,
  patchBody,
  statusKey,
  validateEvaluation,
  valuesDiffer,
  type EvaluationErrors,
  type EvaluationValues,
} from './rules';
import { VoidDialog } from './VoidDialog';

// One evaluation (#308 C4-C9), presentational and phone-first: the heading
// with the student, a text status badge ("Draft", "Final", "Final ·
// Updated", "Voided"), the read-only template, subject, class and course,
// then the form or the read view, and the actions the API's can_* flags
// allow, never more:
// - can_edit (a draft): the form with "Save draft", "Finalize" (saves the
//   pending changes first; with an unscored criterion it lists them and
//   focuses the first empty score instead of opening the dialog) and
//   "Delete draft" (confirmed);
// - can_amend (a final one): "Amend" opens the form with the required,
//   internal "Reason for the change"; Cancel asks first when something was
//   typed;
// - can_void (staff): "Void" with a required reason.
// Focus: Cancel or Escape in a dialog returns it to the button that opened
// it; a done action moves it to the status message (after a void, to the
// heading, C7); a refused one to its failure, or to the first invalid
// field. Leaving with unsaved changes asks first.

export interface EvaluationActions {
  /** Saves a draft or an amendment; resolves with the stored view. */
  onSave: (body: EvaluationPatch) => Promise<Evaluation>;
  onFinalize: () => Promise<Evaluation>;
  onDelete: () => Promise<void>;
  onVoid: (reason: string) => Promise<Evaluation>;
  /** Refetches after a conflict or a changed state. */
  onReload: () => void;
}

export interface EvaluationDetailProps {
  evaluation: Evaluation;
  today: LocalDate;
  actions: EvaluationActions;
  /** A notice to show first ("Draft saved." after the create). */
  initialNotice?: string | null;
  /** Stories: start amending. */
  initialAmending?: boolean;
  /** Stories: open a dialog. */
  initialDialog?: 'finalize' | 'void' | 'delete' | null;
  /** Stories: a failure to show. */
  initialFailure?: FailureState | null;
}

type FocusTarget =
  | 'notice'
  | 'failure'
  | 'trigger'
  | 'heading'
  | 'invalid'
  | 'missing'
  | 'amend';

export function StatusBadge({
  evaluation,
}: {
  evaluation: Pick<Evaluation, 'status' | 'amended_at' | 'voided'>;
}) {
  const { t } = useTranslation();
  return (
    <span className={BADGE_CLASS}>
      {t(`academics.evaluations.status.${statusKey(evaluation)}`)}
    </span>
  );
}

export function EvaluationDetail({
  evaluation,
  today,
  actions,
  initialNotice = null,
  initialAmending = false,
  initialDialog = null,
  initialFailure = null,
}: EvaluationDetailProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const id = useId();
  const criteria = criteriaOf(evaluation);
  const editable = evaluation.can_edit;
  const [amending, setAmending] = useState(
    initialAmending && evaluation.can_amend,
  );
  const [baseline, setBaseline] = useState<EvaluationValues>(() =>
    evaluationValues(evaluation),
  );
  const [values, setValues] = useState<EvaluationValues>(baseline);
  const [errors, setErrors] = useState<EvaluationErrors>(NO_ERRORS);
  const [notice, setNotice] = useState<string | null>(initialNotice);
  const [failure, setFailure] = useState<FailureState | null>(initialFailure);
  const [dialog, setDialog] = useState<
    'finalize' | 'void' | 'delete' | 'discard' | null
  >(initialDialog);
  const [busy, setBusy] = useState<'save' | 'finalize' | null>(null);
  const leaving = useRef(false);
  const trigger = useRef<HTMLElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLDivElement>(null);
  const amendRef = useRef<HTMLButtonElement>(null);
  const [focusTarget, setFocusTarget] = useState<{
    n: number;
    to: FocusTarget;
    position?: number;
  } | null>(null);

  const dirty = (editable || amending) && valuesDiffer(values, baseline);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      !leaving.current &&
      currentLocation.pathname !== nextLocation.pathname,
  );

  function focusNext(to: FocusTarget, position?: number) {
    setFocusTarget((current) => ({ n: (current?.n ?? 0) + 1, to, position }));
  }

  useEffect(() => {
    if (!focusTarget) {
      return;
    }
    const timer = window.setTimeout(() => {
      const { to, position } = focusTarget;
      let target: HTMLElement | null | undefined = null;
      if (to === 'notice') {
        target = noticeRef.current;
      } else if (to === 'failure') {
        target = failureRef.current;
      } else if (to === 'trigger') {
        target = trigger.current?.isConnected ? trigger.current : null;
      } else if (to === 'amend') {
        target = amendRef.current;
      } else if (to === 'invalid') {
        target = formRef.current?.querySelector<HTMLElement>(
          '[aria-invalid="true"]',
        );
      } else if (to === 'missing') {
        target = formRef.current?.querySelector<HTMLElement>(
          `[data-score-position="${position}"]`,
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

  /** Shows a refused write: on the fields, the missing criteria or the
   * failure region (C8). Typed values are kept. */
  function refused(caught: unknown) {
    const refusal = classifyRefusal(
      caught,
      values.scores.map((entry) => entry.position).sort((a, b) => a - b),
    );
    setNotice(null);
    if (refusal.kind === 'fields') {
      setFailure(null);
      setErrors(refusal.errors);
      focusNext('invalid');
      return;
    }
    setFailure({ refusal, error: caught });
    if (refusal.kind === 'incomplete' && refusal.positions.length > 0) {
      focusNext('missing', Math.min(...refusal.positions));
    } else {
      focusNext('failure');
    }
  }

  function reload() {
    setFailure(null);
    actions.onReload();
  }

  /** Validates and saves (a draft or an amendment); true when stored. */
  async function save(mode: 'draft' | 'amend'): Promise<boolean> {
    const found = validateEvaluation(values, { criteria, mode, today });
    setErrors(found);
    if (hasErrors(found)) {
      setNotice(null);
      focusNext('invalid');
      return false;
    }
    try {
      await actions.onSave(patchBody(values, evaluation.version, mode));
      setBaseline(values);
      return true;
    } catch (caught) {
      refused(caught);
      return false;
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    setBusy('save');
    const mode = amending ? 'amend' : 'draft';
    const stored = await save(mode);
    setBusy(null);
    if (stored) {
      if (mode === 'amend') {
        setAmending(false);
        setValues((current) => ({ ...current, changeReason: '' }));
        done(t('academics.evaluations.notices.amended'));
      } else {
        done(t('academics.evaluations.notices.draftSaved'));
      }
    }
  }

  async function finalize(from: HTMLElement) {
    if (busy) {
      return;
    }
    trigger.current = from;
    const missing = missingCriteria(values, criteria);
    if (missing.length > 0) {
      setNotice(null);
      setFailure({
        refusal: {
          kind: 'incomplete',
          positions: missing.map((criterion) => criterion.position),
        },
        error: null,
      });
      focusNext('missing', missing[0]!.position);
      return;
    }
    if (dirty) {
      setBusy('finalize');
      const stored = await save('draft');
      setBusy(null);
      if (!stored) {
        return;
      }
    }
    setFailure(null);
    setDialog('finalize');
  }

  const status = statusKey(evaluation);
  const subjectLine = evaluation.course
    ? t('academics.evaluations.read.course', {
        assessment: evaluation.course.assessment_title,
        cohort: evaluation.course.cohort_name,
      })
    : null;

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-3">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold break-words outline-none"
        >
          {t('academics.evaluations.title', {
            student: evaluation.student.display_name,
          })}
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge evaluation={evaluation} />
          <span className="text-sm text-muted-foreground">
            {t('academics.evaluations.read.author', {
              name: evaluation.author.display_name,
            })}
          </span>
        </div>
        <dl className="grid min-w-0 grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">
            {t('academics.evaluations.fields.template')}
          </dt>
          <dd className="min-w-0 break-words">
            {t('academics.evaluations.templateLine', {
              name: evaluation.template.name,
              kind: t(`academics.kinds.${evaluation.kind}`),
            })}
          </dd>
          <dt className="font-medium">
            {t('academics.evaluations.fields.subject')}
          </dt>
          <dd className="min-w-0 break-words">{evaluation.subject.name}</dd>
          {evaluation.class_session ? (
            <>
              <dt className="font-medium">
                {t('academics.evaluations.fields.session')}
              </dt>
              <dd>{formatInstant(evaluation.class_session.starts_at)}</dd>
            </>
          ) : null}
          {subjectLine ? (
            <>
              <dt className="font-medium">
                {t('academics.evaluations.fields.course')}
              </dt>
              <dd className="min-w-0 break-words">{subjectLine}</dd>
            </>
          ) : null}
        </dl>
        {status === 'draft' && editable ? (
          <p className="text-sm">{t('academics.evaluations.privateDraft')}</p>
        ) : null}
      </div>

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
        <Failure
          ref={failureRef}
          failure={failure}
          criteria={criteria}
          onReload={reload}
        />
      ) : null}

      {editable || amending ? (
        <form
          method="post"
          noValidate
          onSubmit={(event) => void submit(event)}
          className="flex min-w-0 flex-col gap-6"
        >
          <div ref={formRef} className="min-w-0">
            <EvaluationFields
              idPrefix={id}
              criteria={criteria}
              values={values}
              errors={errors}
              onChange={setValues}
              today={today}
              amend={amending}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy !== null}>
              {busy === 'save' ? (
                <Spinner
                  label={t('academics.evaluations.actions.saving')}
                  showLabel
                />
              ) : amending ? (
                t('academics.evaluations.actions.saveAmendment')
              ) : (
                t('academics.evaluations.actions.saveDraft')
              )}
            </Button>
            {editable && evaluation.can_finalize ? (
              <Button
                type="button"
                variant="outline"
                disabled={busy !== null}
                onClick={(event) => void finalize(event.currentTarget)}
              >
                {busy === 'finalize' ? (
                  <Spinner
                    label={t('academics.evaluations.actions.saving')}
                    showLabel
                  />
                ) : (
                  t('academics.evaluations.actions.finalize')
                )}
              </Button>
            ) : null}
            {editable && evaluation.can_delete ? (
              <Button
                type="button"
                variant="outline"
                disabled={busy !== null}
                onClick={(event) => {
                  trigger.current = event.currentTarget;
                  setDialog('delete');
                }}
              >
                {t('academics.evaluations.actions.delete')}
              </Button>
            ) : null}
            {amending ? (
              <Button
                type="button"
                variant="outline"
                disabled={busy !== null}
                onClick={(event) => {
                  if (dirty || values.changeReason.trim() !== '') {
                    trigger.current = event.currentTarget;
                    setDialog('discard');
                  } else {
                    setAmending(false);
                    setErrors(NO_ERRORS);
                    focusNext('amend');
                  }
                }}
              >
                {t('academics.evaluations.cancel')}
              </Button>
            ) : null}
          </div>
        </form>
      ) : (
        <EvaluationReadView evaluation={evaluation} />
      )}

      {!amending && (evaluation.can_amend || evaluation.can_void) ? (
        <div className="flex flex-wrap gap-2">
          {evaluation.can_amend ? (
            <Button
              ref={amendRef}
              type="button"
              variant="outline"
              onClick={() => {
                const fresh = evaluationValues(evaluation);
                setBaseline(fresh);
                setValues(fresh);
                setErrors(NO_ERRORS);
                setNotice(null);
                setFailure(null);
                setAmending(true);
                focusNext(
                  'missing',
                  Math.min(...criteria.map((criterion) => criterion.position)),
                );
              }}
            >
              {t('academics.evaluations.actions.amend')}
            </Button>
          ) : null}
          {evaluation.can_void ? (
            <Button
              type="button"
              variant="destructive"
              onClick={(event) => {
                trigger.current = event.currentTarget;
                setNotice(null);
                setDialog('void');
              }}
            >
              {t('academics.evaluations.actions.void')}
            </Button>
          ) : null}
        </div>
      ) : null}

      <FinalizeDialog
        open={dialog === 'finalize'}
        student={evaluation.student.display_name}
        onCancel={() => {
          setDialog(null);
          focusNext('trigger');
        }}
        onConfirm={async () => {
          try {
            await actions.onFinalize();
            setDialog(null);
            setBaseline(values);
            done(t('academics.evaluations.notices.finalized'));
          } catch (caught) {
            setDialog(null);
            refused(caught);
          }
        }}
      />
      <ConfirmDialog
        open={dialog === 'delete'}
        title={t('academics.evaluations.delete.title')}
        description={t('academics.evaluations.delete.description')}
        confirmLabel={t('academics.evaluations.delete.confirm')}
        busyLabel={t('academics.evaluations.delete.busy')}
        cancelLabel={t('academics.evaluations.cancel')}
        destructive
        onCancel={() => {
          setDialog(null);
          focusNext('trigger');
        }}
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
        open={dialog === 'discard'}
        title={t('academics.evaluations.discard.title')}
        description={t('academics.evaluations.discard.description')}
        confirmLabel={t('academics.evaluations.discard.confirm')}
        busyLabel={t('academics.evaluations.discard.confirm')}
        cancelLabel={t('academics.evaluations.discard.keep')}
        destructive
        onCancel={() => {
          setDialog(null);
          focusNext('trigger');
        }}
        onConfirm={() => {
          const fresh = evaluationValues(evaluation);
          setDialog(null);
          setAmending(false);
          setBaseline(fresh);
          setValues(fresh);
          setErrors(NO_ERRORS);
          focusNext('amend');
          return Promise.resolve();
        }}
      />
      <VoidDialog
        open={dialog === 'void'}
        onCancel={() => {
          setDialog(null);
          focusNext('trigger');
        }}
        onConfirm={async (reason) => {
          await actions.onVoid(reason);
          setDialog(null);
          done(t('academics.evaluations.notices.voided'), 'heading');
        }}
        onFailure={(caught) => {
          setDialog(null);
          refused(caught);
        }}
      />
      <LeaveDialog blocker={blocker} />
    </div>
  );
}
