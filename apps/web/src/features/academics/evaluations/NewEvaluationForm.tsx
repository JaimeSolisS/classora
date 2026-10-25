import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useBlocker } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { FieldError } from '@/features/auth/FieldError';
import type { SubjectOption } from '@/features/courses/api';
import { SELECT_CLASS } from '@/features/students/styles';
import { toLanguage } from '@/i18n/language';
import { formatInstant, type LocalDate } from '@/lib/time';
import { LeaveDialog } from '../LeaveDialog';
import type { EvaluationTemplate } from '../templates/api';
import type { EvaluationContext, EvaluationCreate } from './api';
import { EvaluationFields } from './EvaluationFields';
import { useEvaluationMessage } from './useEvaluationMessage';
import { Failure, type FailureState } from './Failure';
import {
  classifyRefusal,
  createBody,
  emptyScores,
  hasErrors,
  NO_ERRORS,
  validateEvaluation,
  type EvaluationErrors,
  type EvaluationValues,
  type FormCriterion,
} from './rules';

// The new-evaluation form (#308 C1-C3, C9), phone-first, for a tutor: the
// heading with the student; with a class (its evaluation context), the
// class's subject and local date and time; the Template select (active
// templates, the class's subject's when there is a class; each option names
// its kind and subject), the Subject (the template's, else the class's,
// else a required select), "Evaluated on" (the class's date or today, at
// most today), the course assessment select only for a course class, then
// one fieldset per criterion and the feedback. "Save draft" sends one POST
// and the page goes to the new evaluation. "Only you can see this draft."
// No templates: a message and no form. Refusals land on their fields, the
// template select (then reloaded) or the failure region; typed values are
// kept. Leaving with unsaved changes asks first.

export type TemplatesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly EvaluationTemplate[] };

export interface NewEvaluationFormProps {
  student: { id: string; display_name: string };
  /** The evaluable class the form is linked to, if any. */
  context: EvaluationContext | null;
  templates: TemplatesState;
  /** Active subjects, for a template without a subject and no class. */
  subjects: readonly SubjectOption[];
  today: LocalDate;
  /** The class's local date (the default "Evaluated on"). */
  sessionDate: LocalDate | null;
  timeZone: string;
  /** Creates the draft; the page then goes to it. Rejects on refusal. */
  onCreate: (body: EvaluationCreate) => Promise<void>;
  onReloadTemplates: () => void;
  /** The same form without the class. */
  withoutSession: string;
  /** Stories: a template chosen at first. */
  initialTemplateId?: string;
  /** Stories: a failure to show. */
  initialFailure?: FailureState | null;
}

const V = 'academics.evaluations.validation';

export function NewEvaluationForm({
  student,
  context,
  templates,
  subjects,
  today,
  sessionDate,
  timeZone,
  onCreate,
  onReloadTemplates,
  withoutSession,
  initialTemplateId = '',
  initialFailure = null,
}: NewEvaluationFormProps) {
  const { t, i18n } = useTranslation();
  const message = useEvaluationMessage();
  const id = useId();
  const items = templates.status === 'ready' ? templates.items : [];
  const [templateId, setTemplateId] = useState(initialTemplateId);
  const template = items.find((item) => item.id === templateId) ?? null;
  const [subjectId, setSubjectId] = useState('');
  const [assessmentId, setAssessmentId] = useState('');
  const initialValues = (chosen: EvaluationTemplate | null) => ({
    evaluatedOn: sessionDate ?? today,
    scores: chosen ? emptyScores(chosen.criteria) : [],
    feedback: '',
    changeReason: '',
  });
  const [values, setValues] = useState<EvaluationValues>(() =>
    initialValues(template),
  );
  const [errors, setErrors] = useState<EvaluationErrors>(NO_ERRORS);
  const [failure, setFailure] = useState<FailureState | null>(initialFailure);
  const [busy, setBusy] = useState(false);
  const leaving = useRef(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [focusTarget, setFocusTarget] = useState<{
    n: number;
    to: 'failure' | 'invalid';
  } | null>(null);

  const dirty =
    templateId !== '' ||
    values.feedback.trim() !== '' ||
    values.scores.some(
      (entry) => entry.score.trim() !== '' || entry.comment.trim() !== '',
    );
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      !leaving.current &&
      (currentLocation.pathname !== nextLocation.pathname ||
        currentLocation.search !== nextLocation.search),
  );

  useEffect(() => {
    if (!focusTarget) {
      return;
    }
    const timer = window.setTimeout(() => {
      const target =
        focusTarget.to === 'failure'
          ? failureRef.current
          : formRef.current?.querySelector<HTMLElement>(
              '[aria-invalid="true"]',
            );
      (target ?? headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTarget]);

  function focusNext(to: 'failure' | 'invalid') {
    setFocusTarget((current) => ({ n: (current?.n ?? 0) + 1, to }));
  }

  const criteria: FormCriterion[] = template ? template.criteria : [];
  const needsSubjectSelect = template !== null && !template.subject && !context;
  const subjectToSend = template?.subject
    ? null
    : context
      ? context.subject.id
      : subjectId || null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    const found = validateEvaluation(values, {
      criteria,
      mode: 'draft',
      today,
    });
    if (!template) {
      found.template = `${V}.templateRequired`;
    }
    if (needsSubjectSelect && !subjectId) {
      found.subject = `${V}.subjectRequired`;
    }
    setErrors(found);
    if (hasErrors(found)) {
      focusNext('invalid');
      return;
    }
    setBusy(true);
    setFailure(null);
    leaving.current = true;
    const positions = [...criteria]
      .map((criterion) => criterion.position)
      .sort((a, b) => a - b);
    try {
      await onCreate(
        createBody(values, {
          studentId: student.id,
          templateId,
          subjectId: subjectToSend,
          classSessionId: context?.class_session_id ?? null,
          courseAssessmentId:
            context?.course && assessmentId ? assessmentId : null,
        }),
      );
    } catch (caught) {
      leaving.current = false;
      const refusal = classifyRefusal(caught, positions);
      if (refusal.kind === 'fields') {
        setErrors(refusal.errors);
        focusNext('invalid');
      } else if (refusal.kind === 'template') {
        setErrors({ ...NO_ERRORS, template: `${V}.templateUnavailable` });
        onReloadTemplates();
        focusNext('invalid');
      } else {
        setFailure({ refusal, error: caught });
        focusNext('failure');
      }
    } finally {
      setBusy(false);
    }
  }

  const language = toLanguage(i18n.resolvedLanguage);
  const templateMessage = message(errors.template);
  const subjectMessage = message(errors.subject);

  let body;
  if (templates.status === 'loading') {
    body = <LoadingState rows={3} />;
  } else if (templates.status === 'error') {
    body = <ErrorAlert error={templates.error} onRetry={templates.onRetry} />;
  } else if (items.length === 0) {
    body = (
      <p role="status" className="text-sm">
        {t('academics.evaluations.noTemplates')}
      </p>
    );
  } else {
    body = (
      <form
        ref={formRef}
        method="post"
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex min-w-0 flex-col gap-5"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-template`} className="text-sm font-medium">
            {t('academics.evaluations.fields.template')}
          </label>
          <select
            id={`${id}-template`}
            className={SELECT_CLASS}
            value={templateId}
            aria-invalid={templateMessage ? true : undefined}
            aria-describedby={
              templateMessage ? `${id}-template-error` : undefined
            }
            onChange={(event) => {
              const next =
                items.find((item) => item.id === event.target.value) ?? null;
              setTemplateId(event.target.value);
              setValues((current) => ({
                ...current,
                scores: next ? emptyScores(next.criteria) : [],
              }));
              setErrors(NO_ERRORS);
            }}
          >
            <option value="">
              {t('academics.evaluations.fields.chooseTemplate')}
            </option>
            {items.map((item) => (
              <option key={item.id} value={item.id}>
                {t('academics.evaluations.templateOption', {
                  name: item.name,
                  kind: t(`academics.kinds.${item.kind}`),
                  subject:
                    item.subject?.name ?? t('academics.templates.anySubject'),
                })}
              </option>
            ))}
          </select>
          <FieldError id={`${id}-template-error`} message={templateMessage} />
        </div>
        {template ? (
          <>
            {template.description ? (
              <p className="text-sm break-words whitespace-pre-line text-muted-foreground">
                {template.description}
              </p>
            ) : null}
            <div className="flex min-w-0 flex-col gap-2">
              {needsSubjectSelect ? (
                <>
                  <label
                    htmlFor={`${id}-subject`}
                    className="text-sm font-medium"
                  >
                    {t('academics.evaluations.fields.subject')}
                  </label>
                  <select
                    id={`${id}-subject`}
                    className={SELECT_CLASS}
                    value={subjectId}
                    aria-invalid={subjectMessage ? true : undefined}
                    aria-describedby={
                      subjectMessage ? `${id}-subject-error` : undefined
                    }
                    onChange={(event) => setSubjectId(event.target.value)}
                  >
                    <option value="">
                      {t('academics.evaluations.fields.chooseSubject')}
                    </option>
                    {subjects.map((subject) => (
                      <option key={subject.id} value={subject.id}>
                        {t('academics.subjectOption', {
                          subject: subject.name,
                          level: subject.levelName,
                        })}
                      </option>
                    ))}
                  </select>
                </>
              ) : (
                <>
                  <p className="text-sm font-medium">
                    {t('academics.evaluations.fields.subject')}
                  </p>
                  <p className="text-sm break-words">
                    {template.subject?.name ?? context?.subject.name}
                  </p>
                </>
              )}
              <FieldError id={`${id}-subject-error`} message={subjectMessage} />
            </div>
            {context?.course ? (
              <div className="flex min-w-0 flex-col gap-2">
                <label
                  htmlFor={`${id}-assessment`}
                  className="text-sm font-medium"
                >
                  {t('academics.evaluations.fields.assessment')}
                </label>
                <select
                  id={`${id}-assessment`}
                  className={SELECT_CLASS}
                  value={assessmentId}
                  onChange={(event) => setAssessmentId(event.target.value)}
                >
                  <option value="">
                    {t('academics.evaluations.fields.noAssessment')}
                  </option>
                  {context.course.assessments.map((assessment) => (
                    <option key={assessment.id} value={assessment.id}>
                      {assessment.is_required
                        ? t('academics.evaluations.fields.assessmentRequired', {
                            title: assessment.title,
                          })
                        : assessment.title}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
            <EvaluationFields
              idPrefix={id}
              criteria={criteria}
              values={values}
              errors={errors}
              onChange={setValues}
              today={today}
            />
            <p className="text-sm">{t('academics.evaluations.privateDraft')}</p>
          </>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner
                label={t('academics.evaluations.actions.saving')}
                showLabel
              />
            ) : (
              t('academics.evaluations.actions.saveDraft')
            )}
          </Button>
        </div>
      </form>
    );
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-2">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold outline-none"
        >
          {t('academics.evaluations.newTitle')}
        </h1>
        <p className="text-base break-words">{student.display_name}</p>
        {context ? (
          <p className="text-sm break-words">
            {t('academics.evaluations.sessionLine', {
              subject: context.subject.name,
              when: formatInstant(context.starts_at, {
                timeZone,
                language,
                style: 'dateTime',
              }),
            })}
          </p>
        ) : null}
      </div>
      {failure ? (
        <Failure
          ref={failureRef}
          failure={failure}
          criteria={criteria}
          withoutSession={withoutSession}
          onReload={() => {
            setFailure(null);
            onReloadTemplates();
            // The failure (and its Reload) is gone: the heading (#308 QA D3).
            focusNext('failure');
          }}
        />
      ) : null}
      {body}
      <LeaveDialog blocker={blocker} />
    </div>
  );
}
