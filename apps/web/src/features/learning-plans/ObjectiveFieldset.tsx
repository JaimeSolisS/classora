import {
  useFieldArray,
  type Control,
  type FieldErrors,
  type UseFormRegister,
} from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Counter } from '@/features/academics/Counter';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import {
  ACTIVITIES_MAX,
  addMilestoneBlock,
  DUE_ON_MIN,
  EMPTY_MILESTONE,
  MILESTONE_TITLE_MAX,
  MILESTONES_PER_OBJECTIVE_MAX,
  MILESTONES_PER_PLAN_MAX,
  milestoneNeedsConfirm,
  OBJECTIVE_DESCRIPTION_MAX,
  OBJECTIVE_TITLE_MAX,
  type ObjectiveValues,
  type PlanFormValues,
  type PlanMessage,
} from './rules';

// One objective of the plan editor (#310 D3): a fieldset with the legend
// "Objective {n}", its Title and Description (with a counter), "Move up",
// "Move down" and "Remove" named by its position and title, and its 1-10
// milestones, each a fieldset "Milestone {n}.{m}" with Title, Activities
// (with a counter and the help "What the student will do"), Due date, the
// stored progress as text on edit, and its own move and remove buttons
// (milestones move only within their objective, #81 C2). "Add milestone" is
// disabled at 10 per objective or 50 per plan, with the reason linked by
// aria-describedby; "Remove" is disabled on the last milestone. The editor
// owns focus and the confirmations.

export type EditorFocus =
  | { to: 'heading' | 'failure' | 'trigger' | 'addObjective' }
  | { to: 'field'; name: string }
  | { to: 'objectiveMove'; key: string; control: 'up' | 'down' }
  | { to: 'milestoneMove'; key: string; control: 'up' | 'down' }
  | { to: 'addMilestone'; objectiveKey: string };

export interface ObjectiveFieldsetProps {
  index: number;
  objectiveKey: string;
  objectiveCount: number;
  planMilestones: number;
  values: ObjectiveValues | undefined;
  control: Control<PlanFormValues>;
  register: UseFormRegister<PlanFormValues>;
  errors: FieldErrors<PlanFormValues>['objectives'];
  idPrefix: string;
  editing: boolean;
  message: (message: PlanMessage | undefined) => string | undefined;
  onMove: (offset: -1 | 1) => void;
  onRemove: (trigger: HTMLElement) => void;
  /** Asks before `run` (a done milestone loses its progress). */
  confirm: (run: () => void, trigger: HTMLElement) => void;
  focusNext: (target: EditorFocus) => void;
  announce: (text: string) => void;
}

const described = (...parts: (string | false | undefined)[]) =>
  parts.filter(Boolean).join(' ') || undefined;

export function ObjectiveFieldset({
  index,
  objectiveKey,
  objectiveCount,
  planMilestones,
  values,
  control,
  register,
  errors,
  idPrefix,
  editing,
  message,
  onMove,
  onRemove,
  confirm,
  focusNext,
  announce,
}: ObjectiveFieldsetProps) {
  const { t } = useTranslation();
  const { fields, append, remove, move } = useFieldArray({
    control,
    name: `objectives.${index}.milestones`,
  });
  const number = index + 1;
  const title = values?.title.trim() ?? '';
  const name = title
    ? t('learningPlans.editor.objectiveNamed', { number, title })
    : t('learningPlans.editor.objectiveUnnamed', { number });
  const own = errors?.[index];
  const field = (part: string) => `${idPrefix}-o-${objectiveKey}-${part}`;
  const titleMessage = message(own?.title?.message);
  const descriptionMessage = message(own?.description?.message);
  const milestonesMessage = message(
    own?.milestones?.root?.message ?? own?.milestones?.message,
  );
  const addBlock = addMilestoneBlock(fields.length, planMilestones);
  const lastObjective = objectiveCount <= 1;

  function moveMilestone(from: number, offset: -1 | 1) {
    const to = from + offset;
    const key = fields[from]!.id;
    move(from, to);
    announce(
      t('learningPlans.editor.milestoneMoved', {
        position: `${number}.${to + 1}`,
      }),
    );
    focusNext({
      to: 'milestoneMove',
      key,
      control: offset < 0 ? 'up' : 'down',
    });
  }

  function removeMilestone(at: number) {
    remove(at);
    announce(
      t('learningPlans.editor.milestoneRemoved', {
        position: `${number}.${at + 1}`,
      }),
    );
    focusNext({ to: 'addMilestone', objectiveKey });
  }

  return (
    <li data-objective={objectiveKey} className="min-w-0">
      <fieldset className="flex min-w-0 flex-col gap-4 rounded-md border border-border p-4">
        <legend className="px-1 text-base font-semibold">
          {t('learningPlans.editor.objectiveLegend', { number })}
        </legend>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('title')} className="text-sm font-medium">
            {t('learningPlans.editor.objectiveTitle', { number })}
          </label>
          <Input
            id={field('title')}
            autoComplete="off"
            maxLength={OBJECTIVE_TITLE_MAX + 20}
            aria-invalid={titleMessage ? true : undefined}
            aria-describedby={described(titleMessage && field('title-error'))}
            {...register(`objectives.${index}.title`)}
          />
          <FieldError id={field('title-error')} message={titleMessage} />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('description')} className="text-sm font-medium">
            {t('learningPlans.editor.objectiveDescription', { number })}
          </label>
          <textarea
            id={field('description')}
            rows={2}
            className={TEXTAREA_CLASS}
            aria-invalid={descriptionMessage ? true : undefined}
            aria-describedby={described(
              descriptionMessage && field('description-error'),
              field('description-counter'),
            )}
            {...register(`objectives.${index}.description`)}
          />
          <FieldError
            id={field('description-error')}
            message={descriptionMessage}
          />
          <Counter
            id={field('description-counter')}
            value={values?.description ?? ''}
            max={OBJECTIVE_DESCRIPTION_MAX}
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-move-objective="up"
            disabled={index === 0}
            aria-label={t('learningPlans.editor.moveUp', { name })}
            onClick={() => onMove(-1)}
          >
            {t('learningPlans.editor.up')}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-move-objective="down"
            disabled={index === objectiveCount - 1}
            aria-label={t('learningPlans.editor.moveDown', { name })}
            onClick={() => onMove(1)}
          >
            {t('learningPlans.editor.down')}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={lastObjective}
            aria-label={t('learningPlans.editor.remove', { name })}
            aria-describedby={lastObjective ? field('remove-min') : undefined}
            onClick={(event) => onRemove(event.currentTarget)}
          >
            {t('learningPlans.editor.removeShort')}
          </Button>
        </div>
        {lastObjective ? (
          <p id={field('remove-min')} className="text-sm text-muted-foreground">
            {t('learningPlans.editor.objectiveMin')}
          </p>
        ) : null}

        <div className="flex min-w-0 flex-col gap-3">
          <h3 className="text-sm font-semibold">
            {t('learningPlans.editor.milestonesHeading')}
          </h3>
          <p className="text-sm text-muted-foreground">
            {t('learningPlans.editor.milestonesCounter', {
              n: fields.length,
              max: MILESTONES_PER_OBJECTIVE_MAX,
            })}
          </p>
          {milestonesMessage ? (
            <p role="alert" className="text-sm text-destructive">
              {milestonesMessage}
            </p>
          ) : null}
          <ol className="flex min-w-0 flex-col gap-3">
            {fields.map((row, at) => {
              const position = `${number}.${at + 1}`;
              const milestone = values?.milestones[at];
              const milestoneTitle = milestone?.title.trim() ?? '';
              const milestoneName = milestoneTitle
                ? t('learningPlans.editor.milestoneNamed', {
                    position,
                    title: milestoneTitle,
                  })
                : t('learningPlans.editor.milestoneUnnamed', { position });
              const rowErrors = own?.milestones?.[at];
              const mTitle = message(rowErrors?.title?.message);
              const mActivities = message(rowErrors?.activities?.message);
              const mDue = message(rowErrors?.dueOn?.message);
              const mid = (part: string) => `${idPrefix}-m-${row.id}-${part}`;
              const lastMilestone = fields.length <= 1;
              return (
                <li key={row.id} data-milestone={row.id} className="min-w-0">
                  <fieldset className="flex min-w-0 flex-col gap-3 rounded-md border border-border p-3">
                    <legend className="px-1 text-sm font-semibold">
                      {t('learningPlans.editor.milestoneLegend', { position })}
                    </legend>
                    <div className="flex min-w-0 flex-col gap-2">
                      <label
                        htmlFor={mid('title')}
                        className="text-sm font-medium"
                      >
                        {t('learningPlans.editor.milestoneTitle', { position })}
                      </label>
                      <Input
                        id={mid('title')}
                        autoComplete="off"
                        maxLength={MILESTONE_TITLE_MAX + 20}
                        aria-invalid={mTitle ? true : undefined}
                        aria-describedby={described(
                          mTitle && mid('title-error'),
                        )}
                        {...register(
                          `objectives.${index}.milestones.${at}.title`,
                        )}
                      />
                      <FieldError id={mid('title-error')} message={mTitle} />
                    </div>
                    <div className="flex min-w-0 flex-col gap-2">
                      <label
                        htmlFor={mid('activities')}
                        className="text-sm font-medium"
                      >
                        {t('learningPlans.editor.activities', { position })}
                      </label>
                      <textarea
                        id={mid('activities')}
                        rows={2}
                        className={TEXTAREA_CLASS}
                        aria-invalid={mActivities ? true : undefined}
                        aria-describedby={described(
                          mid('activities-help'),
                          mActivities && mid('activities-error'),
                          mid('activities-counter'),
                        )}
                        {...register(
                          `objectives.${index}.milestones.${at}.activities`,
                        )}
                      />
                      <p
                        id={mid('activities-help')}
                        className="text-sm text-muted-foreground"
                      >
                        {t('learningPlans.editor.activitiesHelp')}
                      </p>
                      <FieldError
                        id={mid('activities-error')}
                        message={mActivities}
                      />
                      <Counter
                        id={mid('activities-counter')}
                        value={milestone?.activities ?? ''}
                        max={ACTIVITIES_MAX}
                      />
                    </div>
                    <div className="flex min-w-0 flex-col gap-2 sm:max-w-xs">
                      <label
                        htmlFor={mid('due')}
                        className="text-sm font-medium"
                      >
                        {t('learningPlans.editor.dueOn', { position })}
                      </label>
                      <Input
                        id={mid('due')}
                        type="date"
                        min={DUE_ON_MIN}
                        aria-invalid={mDue ? true : undefined}
                        aria-describedby={described(mDue && mid('due-error'))}
                        {...register(
                          `objectives.${index}.milestones.${at}.dueOn`,
                        )}
                      />
                      <FieldError id={mid('due-error')} message={mDue} />
                    </div>
                    {editing && milestone?.status ? (
                      <p className="text-sm">
                        <span className="font-medium">
                          {t('learningPlans.editor.progressLabel')}
                        </span>{' '}
                        {t(`learningPlans.milestoneStatus.${milestone.status}`)}
                      </p>
                    ) : null}
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        data-move-milestone="up"
                        disabled={at === 0}
                        aria-label={t('learningPlans.editor.moveUp', {
                          name: milestoneName,
                        })}
                        onClick={() => moveMilestone(at, -1)}
                      >
                        {t('learningPlans.editor.up')}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        data-move-milestone="down"
                        disabled={at === fields.length - 1}
                        aria-label={t('learningPlans.editor.moveDown', {
                          name: milestoneName,
                        })}
                        onClick={() => moveMilestone(at, 1)}
                      >
                        {t('learningPlans.editor.down')}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={lastMilestone}
                        aria-label={t('learningPlans.editor.remove', {
                          name: milestoneName,
                        })}
                        aria-describedby={
                          lastMilestone ? mid('remove-min') : undefined
                        }
                        onClick={(event) => {
                          if (milestone && milestoneNeedsConfirm(milestone)) {
                            confirm(
                              () => removeMilestone(at),
                              event.currentTarget,
                            );
                          } else {
                            removeMilestone(at);
                          }
                        }}
                      >
                        {t('learningPlans.editor.removeShort')}
                      </Button>
                    </div>
                    {lastMilestone ? (
                      <p
                        id={mid('remove-min')}
                        className="text-sm text-muted-foreground"
                      >
                        {t('learningPlans.editor.milestoneMin')}
                      </p>
                    ) : null}
                  </fieldset>
                </li>
              );
            })}
          </ol>
          <div className="flex flex-col items-start gap-1">
            <Button
              type="button"
              variant="outline"
              data-add-milestone=""
              disabled={addBlock !== null}
              aria-label={t('learningPlans.editor.addMilestoneTo', { name })}
              aria-describedby={addBlock ? field('add-block') : undefined}
              onClick={() => {
                const at = fields.length;
                append({ ...EMPTY_MILESTONE }, { shouldFocus: false });
                focusNext({
                  to: 'field',
                  name: `objectives.${index}.milestones.${at}.title`,
                });
              }}
            >
              {t('learningPlans.editor.addMilestone')}
            </Button>
            {addBlock ? (
              <p
                id={field('add-block')}
                className="text-sm text-muted-foreground"
              >
                {addBlock === 'objective'
                  ? t('learningPlans.editor.milestonesAtMax', {
                      max: MILESTONES_PER_OBJECTIVE_MAX,
                    })
                  : t('learningPlans.editor.planAtMax', {
                      max: MILESTONES_PER_PLAN_MAX,
                    })}
              </p>
            ) : null}
          </div>
        </div>
      </fieldset>
    </li>
  );
}
