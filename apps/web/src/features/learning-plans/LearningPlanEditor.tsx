import { zodResolver } from '@hookform/resolvers/zod';
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import {
  useFieldArray,
  useForm,
  useWatch,
  type FieldPath,
} from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useBlocker } from 'react-router';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Counter } from '@/features/academics/Counter';
import { ConfirmDialog } from '@/features/academics/evaluations/ConfirmDialog';
import { LeaveDialog } from '@/features/academics/LeaveDialog';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import type { LearningPlan } from './api';
import { ObjectiveFieldset, type EditorFocus } from './ObjectiveFieldset';
import { PlanFailure, type PlanFailureState } from './PlanFailure';
import {
  addObjectiveBlock,
  classifyRefusal,
  emptyObjective,
  milestoneCount,
  MILESTONES_PER_PLAN_MAX,
  newPlanValues,
  objectiveNeedsConfirm,
  OBJECTIVES_MAX,
  PLAN_DESCRIPTION_MAX,
  PLAN_TITLE_MAX,
  placedFieldErrors,
  planFormSchema,
  planValues,
  type PlanFormValues,
  type PlanMessage,
} from './rules';

// The learning plan editor (#310 D), create and edit, phone-first, React
// Hook Form + Zod: Subject (create: a select of the subjects the caller
// may create in, "(has an active plan)" marked; edit: read-only text), Title,
// Goal description (counter), Starts on, Target date (min = Starts on), and
// 1-10 objectives with 1-10 milestones each (at most 50), with counters and
// disabled-with-reason adds (ObjectiveFieldset). One request per save
// (the page sends it and navigates); a refusal lands on its fields (by
// path, focus on the first) or in the failure region (focused). Reload after
// a conflict asks first when something was typed, then the page refetches
// and remounts the editor, which focuses its heading. Leaving with unsaved
// changes asks first (the #77 leave pattern).
//
// Focus: Add focuses the new title; Move keeps focus on the moved item's
// button (its other one at an end); Remove focuses the matching Add button;
// Cancel or Escape in a confirmation returns it to the button that opened
// it. Every move runs from one 0 ms effect, after the render it waits for.

export interface SubjectChoice {
  id: string;
  label: string;
}

export interface LearningPlanEditorProps {
  /** The plan being edited; null on a create. */
  plan: LearningPlan | null;
  /** The center's today (a create's default Starts on). */
  today: string;
  studentName: string | null;
  /** Create: the subjects offered. */
  subjects?: readonly SubjectChoice[];
  /** Create: the subjects that already have an active plan. */
  activeSubjectIds?: ReadonlySet<string>;
  /** Saves (create or replace) and navigates; rejects with the error.
   * `version` is the plan version the form was loaded from (null on a
   * create), never a newer one read meanwhile. */
  onSave: (values: PlanFormValues, version: number | null) => Promise<void>;
  /** Refetches and remounts (after a conflict or a changed state). */
  onReload: () => void;
  /** Focus the heading when it mounts (after a Reload). */
  focusHeadingOnMount?: boolean;
  /** Stories: a refusal to show. */
  initialFailure?: PlanFailureState | null;
  /** Stories: field errors to show. */
  initialFieldErrors?: unknown;
}

type Pending = { run: () => void; kind: 'remove' | 'reload' };

function useMessage() {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  return (message: PlanMessage | undefined): string | undefined => {
    if (!message) {
      return undefined;
    }
    if (message.startsWith('errors:')) {
      return errorMessage(
        new ApiError({ code: message.slice('errors:'.length), status: 422 }),
        tErrors,
      );
    }
    return (t as unknown as (key: string) => string)(message);
  };
}

export function LearningPlanEditor({
  plan,
  today,
  studentName,
  subjects = [],
  activeSubjectIds = new Set(),
  onSave,
  onReload,
  focusHeadingOnMount = false,
  initialFailure = null,
  initialFieldErrors,
}: LearningPlanEditorProps) {
  const { t } = useTranslation();
  const message = useMessage();
  const id = useId();
  const editing = plan !== null;
  const defaultValues = useMemo(
    () => (plan ? planValues(plan) : newPlanValues(today)),
    // The editor is remounted for a new plan version (Reload).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const loadedVersion = useRef(plan?.version ?? null);
  const schema = useMemo(() => planFormSchema(!editing), [editing]);
  const {
    register,
    control,
    handleSubmit,
    setError: setFieldError,
    setFocus,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<PlanFormValues>({
    resolver: zodResolver(schema),
    defaultValues,
    shouldFocusError: true,
  });
  const { fields, append, remove, move } = useFieldArray({
    control,
    name: 'objectives',
  });
  const values = useWatch({ control }) as PlanFormValues;
  const [failure, setFailure] = useState<PlanFailureState | null>(
    initialFailure,
  );
  const [pending, setPending] = useState<Pending | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const leaving = useRef(false);
  const inFlight = useRef(false);
  const trigger = useRef<HTMLElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const addObjectiveRef = useRef<HTMLButtonElement>(null);
  const objectivesRef = useRef<HTMLOListElement>(null);
  const [focusTarget, setFocusTarget] = useState<
    (EditorFocus & { n: number }) | null
  >(focusHeadingOnMount ? { n: 0, to: 'heading' } : null);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      isDirty &&
      !leaving.current &&
      currentLocation.pathname !== nextLocation.pathname,
  );

  function focusNext(target: EditorFocus) {
    setFocusTarget((current) => ({ ...target, n: (current?.n ?? 0) + 1 }));
  }

  useEffect(() => {
    if (!focusTarget) {
      return;
    }
    const timer = window.setTimeout(() => {
      const root = objectivesRef.current;
      let target: HTMLElement | null | undefined = null;
      switch (focusTarget.to) {
        case 'heading':
          target = headingRef.current;
          break;
        case 'failure':
          target = failureRef.current;
          break;
        case 'trigger':
          target = trigger.current?.isConnected
            ? trigger.current
            : failureRef.current;
          break;
        case 'addObjective':
          target = addObjectiveRef.current;
          break;
        case 'field':
          setFocus(focusTarget.name as FieldPath<PlanFormValues>);
          return;
        case 'objectiveMove':
        case 'milestoneMove': {
          const attribute =
            focusTarget.to === 'objectiveMove' ? 'objective' : 'milestone';
          const item = root?.querySelector(
            `[data-${attribute}="${focusTarget.key}"]`,
          );
          const find = (control: string) =>
            [
              ...(item?.querySelectorAll<HTMLButtonElement>(
                `[data-move-${attribute}="${control}"]`,
              ) ?? []),
            ][0];
          const wanted = find(focusTarget.control);
          const other = find(focusTarget.control === 'up' ? 'down' : 'up');
          target = wanted && !wanted.disabled ? wanted : other;
          break;
        }
        case 'addMilestone':
          target = root?.querySelector<HTMLElement>(
            `[data-objective="${focusTarget.objectiveKey}"] [data-add-milestone]`,
          );
          break;
      }
      (target ?? headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTarget, setFocus]);

  // Stories: server field errors placed as a refused save would.
  useEffect(() => {
    if (initialFieldErrors) {
      for (const { field, message: text } of placedFieldErrors(
        initialFieldErrors,
      )) {
        setFieldError(field as FieldPath<PlanFormValues>, { message: text });
      }
    }
  }, [initialFieldErrors, setFieldError]);

  /** Places a refused save's field errors; true when any was placed. */
  function placeServerErrors(caught: unknown): boolean {
    const placed = placedFieldErrors(caught);
    if (placed.length === 0) {
      return false;
    }
    for (const { field, message: text } of placed) {
      setFieldError(field as FieldPath<PlanFormValues>, { message: text });
    }
    const first = placed[0]!.field;
    if (first === 'objectives.root') {
      focusNext({ to: 'addObjective' });
    } else if (first.endsWith('.milestones.root')) {
      const index = Number(first.split('.')[1]);
      const key = fields[index]?.id;
      focusNext(
        key
          ? { to: 'addMilestone', objectiveKey: key }
          : { to: 'addObjective' },
      );
    } else {
      focusNext({ to: 'field', name: first });
    }
    return true;
  }

  async function save(submitted: PlanFormValues) {
    setFailure(null);
    leaving.current = true;
    try {
      await onSave(submitted, loadedVersion.current);
    } catch (caught) {
      leaving.current = false;
      const refusal = classifyRefusal(caught);
      if (refusal === 'missing') {
        return; // The page shows the not-found page.
      }
      if (refusal !== 'conflict' && placeServerErrors(caught)) {
        return;
      }
      setFailure({ refusal, error: caught });
      focusNext({ to: 'failure' });
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    void handleSubmit(save)().finally(() => {
      inFlight.current = false;
    });
  }

  function reload() {
    const from = document.activeElement;
    trigger.current = from instanceof HTMLElement ? from : null;
    if (isDirty) {
      setPending({
        kind: 'reload',
        run: () => {
          leaving.current = true;
          onReload();
        },
      });
    } else {
      leaving.current = true;
      onReload();
    }
  }

  function moveObjective(index: number, offset: -1 | 1) {
    const to = index + offset;
    const key = fields[index]!.id;
    move(index, to);
    setAnnouncement(
      t('learningPlans.editor.objectiveMoved', { position: to + 1 }),
    );
    focusNext({
      to: 'objectiveMove',
      key,
      control: offset < 0 ? 'up' : 'down',
    });
  }

  function removeObjective(index: number) {
    remove(index);
    setAnnouncement(
      t('learningPlans.editor.objectiveRemoved', { number: index + 1 }),
    );
    focusNext({ to: 'addObjective' });
  }

  const objectives = values.objectives ?? [];
  const planMilestones = milestoneCount(objectives);
  const addBlock = addObjectiveBlock(objectives);
  const titleMessage = message(errors.title?.message);
  const descriptionMessage = message(errors.description?.message);
  const subjectMessage = message(errors.subjectId?.message);
  const startsMessage = message(errors.startsOn?.message);
  const targetMessage = message(errors.targetOn?.message);
  const objectivesMessage = message(
    errors.objectives?.root?.message ?? errors.objectives?.message,
  );
  const field = (name: string) => `${id}-${name}`;
  const described = (...parts: (string | false | undefined)[]) =>
    parts.filter(Boolean).join(' ') || undefined;
  const heading = editing
    ? t('learningPlans.editor.editTitle')
    : t('learningPlans.editor.newTitle');

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-1">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold break-words outline-none"
        >
          {heading}
        </h1>
        {studentName ? (
          <p className="text-sm break-words text-muted-foreground">
            {t('learningPlans.editor.forStudent', { name: studentName })}
          </p>
        ) : null}
      </div>
      {failure ? (
        <PlanFailure
          ref={failureRef}
          failure={failure}
          subjectName={plan?.subject.name ?? ''}
          studentId={plan?.student_id ?? ''}
          onReload={reload}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={submit}
        className="flex min-w-0 flex-col gap-6"
      >
        <div className="flex min-w-0 flex-col gap-2">
          {editing ? (
            <>
              <p className="text-sm font-medium">
                {t('learningPlans.editor.subject')}
              </p>
              <p className="text-sm break-words">{plan.subject.name}</p>
              <p className="text-sm text-muted-foreground">
                {t('learningPlans.editor.subjectFixed')}
              </p>
            </>
          ) : (
            <>
              <label htmlFor={field('subject')} className="text-sm font-medium">
                {t('learningPlans.editor.subject')}
              </label>
              <select
                id={field('subject')}
                className={SELECT_CLASS}
                aria-invalid={subjectMessage ? true : undefined}
                aria-describedby={described(
                  subjectMessage && field('subject-error'),
                )}
                {...register('subjectId')}
              >
                <option value="">
                  {t('learningPlans.editor.chooseSubject')}
                </option>
                {subjects.map((subject) => (
                  <option key={subject.id} value={subject.id}>
                    {activeSubjectIds.has(subject.id)
                      ? t('learningPlans.editor.subjectWithActive', {
                          subject: subject.label,
                        })
                      : subject.label}
                  </option>
                ))}
              </select>
              <FieldError
                id={field('subject-error')}
                message={subjectMessage}
              />
            </>
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('title')} className="text-sm font-medium">
            {t('learningPlans.editor.title')}
          </label>
          <Input
            id={field('title')}
            autoComplete="off"
            maxLength={PLAN_TITLE_MAX + 20}
            aria-invalid={titleMessage ? true : undefined}
            aria-describedby={described(titleMessage && field('title-error'))}
            {...register('title')}
          />
          <FieldError id={field('title-error')} message={titleMessage} />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('description')} className="text-sm font-medium">
            {t('learningPlans.editor.description')}
          </label>
          <textarea
            id={field('description')}
            rows={3}
            className={TEXTAREA_CLASS}
            aria-invalid={descriptionMessage ? true : undefined}
            aria-describedby={described(
              descriptionMessage && field('description-error'),
              field('description-counter'),
            )}
            {...register('description')}
          />
          <FieldError
            id={field('description-error')}
            message={descriptionMessage}
          />
          <Counter
            id={field('description-counter')}
            value={values.description ?? ''}
            max={PLAN_DESCRIPTION_MAX}
          />
        </div>
        <div className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={field('starts')} className="text-sm font-medium">
              {t('learningPlans.editor.startsOn')}
            </label>
            <Input
              id={field('starts')}
              type="date"
              aria-invalid={startsMessage ? true : undefined}
              aria-describedby={described(
                startsMessage && field('starts-error'),
              )}
              {...register('startsOn')}
            />
            <FieldError id={field('starts-error')} message={startsMessage} />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={field('target')} className="text-sm font-medium">
              {t('learningPlans.editor.targetOn')}
            </label>
            <Input
              id={field('target')}
              type="date"
              min={values.startsOn || undefined}
              aria-invalid={targetMessage ? true : undefined}
              aria-describedby={described(
                targetMessage && field('target-error'),
              )}
              {...register('targetOn')}
            />
            <FieldError id={field('target-error')} message={targetMessage} />
          </div>
        </div>

        <section
          aria-labelledby={field('objectives-heading')}
          className="flex min-w-0 flex-col gap-3"
        >
          <h2
            id={field('objectives-heading')}
            className="text-lg font-semibold"
          >
            {t('learningPlans.editor.objectivesHeading')}
          </h2>
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>
              {t('learningPlans.editor.objectivesCounter', {
                n: fields.length,
                max: OBJECTIVES_MAX,
              })}
            </span>
            <span>
              {t('learningPlans.editor.planCounter', {
                n: planMilestones,
                max: MILESTONES_PER_PLAN_MAX,
              })}
            </span>
          </p>
          {objectivesMessage ? (
            <p role="alert" className="text-sm text-destructive">
              {objectivesMessage}
            </p>
          ) : null}
          <p role="status" aria-live="polite" className="sr-only">
            {announcement}
          </p>
          <ol ref={objectivesRef} className="flex min-w-0 flex-col gap-4">
            {fields.map((row, index) => (
              <ObjectiveFieldset
                // Remounted when its index changes, so its milestone array
                // follows the objective it belongs to.
                key={`${row.id}:${index}`}
                index={index}
                objectiveKey={row.id}
                objectiveCount={fields.length}
                planMilestones={planMilestones}
                values={objectives[index]}
                control={control}
                register={register}
                errors={errors.objectives}
                idPrefix={id}
                editing={editing}
                message={message}
                onMove={(offset) => moveObjective(index, offset)}
                onRemove={(from) => {
                  const objective = objectives[index];
                  if (objective && objectiveNeedsConfirm(objective)) {
                    trigger.current = from;
                    setPending({
                      kind: 'remove',
                      run: () => removeObjective(index),
                    });
                  } else {
                    removeObjective(index);
                  }
                }}
                confirm={(run, from) => {
                  trigger.current = from;
                  setPending({ kind: 'remove', run });
                }}
                focusNext={focusNext}
                announce={setAnnouncement}
              />
            ))}
          </ol>
          <div className="flex flex-col items-start gap-1">
            <Button
              ref={addObjectiveRef}
              type="button"
              variant="outline"
              disabled={addBlock !== null}
              aria-describedby={addBlock ? field('add-block') : undefined}
              onClick={() => {
                const at = fields.length;
                append(emptyObjective(), { shouldFocus: false });
                focusNext({ to: 'field', name: `objectives.${at}.title` });
              }}
            >
              {t('learningPlans.editor.addObjective')}
            </Button>
            {addBlock ? (
              <p
                id={field('add-block')}
                className="text-sm text-muted-foreground"
              >
                {addBlock === 'objectives'
                  ? t('learningPlans.editor.objectivesAtMax', {
                      max: OBJECTIVES_MAX,
                    })
                  : t('learningPlans.editor.planAtMax', {
                      max: MILESTONES_PER_PLAN_MAX,
                    })}
              </p>
            ) : null}
          </div>
        </section>

        <div className="flex flex-col items-start gap-3 border-t border-border pt-4">
          {plan?.status === 'active' ? (
            <p className="text-sm font-medium">
              {studentName
                ? t('learningPlans.editor.activeNote', { student: studentName })
                : t('learningPlans.editor.activeNoteNoName')}
            </p>
          ) : null}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('learningPlans.editor.saving')} showLabel />
            ) : (
              t('learningPlans.editor.save')
            )}
          </Button>
        </div>
      </form>
      <ConfirmDialog
        open={pending !== null}
        title={
          pending?.kind === 'reload'
            ? t('learningPlans.editor.reloadConfirm.title')
            : t('learningPlans.editor.removeConfirm.title')
        }
        description={
          pending?.kind === 'reload'
            ? t('learningPlans.editor.reloadConfirm.description')
            : t('learningPlans.editor.removeConfirm.description')
        }
        confirmLabel={
          pending?.kind === 'reload'
            ? t('learningPlans.editor.reloadConfirm.confirm')
            : t('learningPlans.editor.removeConfirm.confirm')
        }
        busyLabel={t('learningPlans.busy')}
        cancelLabel={t('learningPlans.cancel')}
        destructive
        onCancel={() => {
          setPending(null);
          focusNext({ to: 'trigger' });
        }}
        onConfirm={() => {
          const run = pending?.run;
          setPending(null);
          run?.();
          return Promise.resolve();
        }}
      />
      <LeaveDialog blocker={blocker} />
    </div>
  );
}
