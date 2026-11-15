import { zodResolver } from '@hookform/resolvers/zod';
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { useFieldArray, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useBlocker } from 'react-router';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { CatalogStatusBadge } from '@/features/catalog/CatalogStatusBadge';
import type { SubjectOption } from '@/features/courses/api';
import { SELECT_CLASS } from '@/features/students/styles';
import { Counter } from '../Counter';
import { LeaveDialog } from '../LeaveDialog';
import { TEXTAREA_CLASS } from '../styles';
import {
  EVALUATION_KINDS,
  type EvaluationKind,
  type EvaluationTemplate,
} from './api';
import {
  CRITERIA_MAX,
  CRITERION_DESCRIPTION_MAX,
  CRITERION_TITLE_MAX,
  DESCRIPTION_MAX,
  EMPTY_CRITERION,
  isConflict,
  isDuplicateName,
  NAME_MAX,
  NEW_TEMPLATE,
  serverTemplateMessage,
  templateFieldOf,
  templateFormSchema,
  templateValues,
  type TemplateField,
  type TemplateFormValues,
  type TemplateMessage,
} from './rules';

// The evaluation template editor (#308 B2), React Hook Form + Zod: Name,
// Kind (a select on create; read-only text on edit), Subject ("Any
// subject" or an active subject), Description with a counter, and 1-20
// criteria in order (title, description, maximum score with at most two
// places, F1) with "Move up", "Move down" and "Remove" named by the row's
// position and title. "Add criterion" is disabled at 20 with the reason;
// "Remove" is disabled on the last row. Moving announces the new position
// and keeps focus on the moved row; "Add" focuses the new row's title;
// "Remove" focuses "Add criterion". On edit, Deactivate / Activate sends the
// PUT with `is_active`. One request per save; errors land on their fields
// (focus on the first), a version conflict shows "Reload". Leaving with
// unsaved changes asks first (the #77 leave pattern).

export interface TemplateEditorProps {
  /** The template being edited; null for a new one. */
  template: EvaluationTemplate | null;
  subjects: readonly SubjectOption[];
  /** Saves (create or replace); rejects with the error to show. */
  onSave: (values: TemplateFormValues) => Promise<void>;
  /** Refetches the template after a version conflict. */
  /** Refetches the template after a version conflict; resolves with the
   * template read (when there is one). */
  onReload: () => Promise<EvaluationTemplate | undefined | void>;
  /** Stories: an error to show. */
  initialError?: unknown;
  /** Arrived from a create: "Template saved.". */
  initiallySaved?: boolean;
}

function useMessage() {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  return (message: TemplateMessage | undefined): string | undefined => {
    if (!message) {
      return undefined;
    }
    if (message.startsWith('errors:')) {
      return errorMessage(
        new ApiError({ code: message.slice('errors:'.length), status: 422 }),
        tErrors,
      );
    }
    // A key of our own schema or server mapping.
    return (t as unknown as (key: string) => string)(message);
  };
}

export function TemplateEditor({
  template,
  subjects,
  onSave,
  onReload,
  initialError = null,
  initiallySaved = false,
}: TemplateEditorProps) {
  const { t } = useTranslation();
  const message = useMessage();
  const id = useId();
  const editing = template !== null;
  const defaultValues = useMemo(
    () => (template ? templateValues(template) : NEW_TEMPLATE),
    [template],
  );
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError: setFieldError,
    setFocus,
    setValue,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<TemplateFormValues>({
    resolver: zodResolver(templateFormSchema),
    defaultValues,
    shouldFocusError: true,
  });
  const { fields, append, remove, move } = useFieldArray({
    control,
    name: 'criteria',
  });
  const values = useWatch({ control }) as TemplateFormValues;
  const [error, setError] = useState<unknown>(initialError);
  const [saved, setSaved] = useState(initiallySaved);
  const [announcement, setAnnouncement] = useState('');
  const inFlight = useRef(false);
  const leaving = useRef(false);
  const savedRef = useRef<HTMLParagraphElement>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const rowsRef = useRef<HTMLOListElement>(null);
  const [focusTo, setFocusTo] = useState<{
    n: number;
    to:
      | 'saved'
      | 'alert'
      | 'add'
      | 'name'
      | { row: string; control: 'up' | 'down' };
  } | null>(
    // #308 QA D1: arriving from the create focuses "Template saved.".
    initiallySaved ? { n: 0, to: 'saved' } : null,
  );

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      isDirty &&
      !leaving.current &&
      currentLocation.pathname !== nextLocation.pathname,
  );

  // #308 QA D3/D4: after Reload, Name takes focus once the form shows the
  // template that was read: here, right after the reset, when the reload
  // brought a new version (the version it waits for is kept in a ref).
  const focusNameOnVersion = useRef<number | null>(null);
  const shownVersion = useRef<number | null>(null);

  useEffect(() => {
    reset(defaultValues);
    shownVersion.current = template?.version ?? null;
    if (
      focusNameOnVersion.current !== null &&
      shownVersion.current === focusNameOnVersion.current
    ) {
      focusNameOnVersion.current = null;
      setFocusTo((current) => ({ n: (current?.n ?? 0) + 1, to: 'name' }));
    }
  }, [defaultValues, reset, template?.version]);

  useEffect(() => {
    if (!focusTo) {
      return;
    }
    const timer = window.setTimeout(() => {
      const { to } = focusTo;
      if (to === 'saved') {
        savedRef.current?.focus();
      } else if (to === 'alert') {
        alertRef.current?.focus();
      } else if (to === 'add') {
        addRef.current?.focus();
      } else if (to === 'name') {
        setFocus('name');
      } else {
        const row = rowsRef.current?.querySelector(`[data-row="${to.row}"]`);
        const wanted = row?.querySelector<HTMLButtonElement>(
          `[data-move="${to.control}"]`,
        );
        const other = row?.querySelector<HTMLButtonElement>(
          `[data-move="${to.control === 'up' ? 'down' : 'up'}"]`,
        );
        (wanted && !wanted.disabled ? wanted : other)?.focus();
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTo, setFocus]);

  function focusNext(to: NonNullable<typeof focusTo>['to']) {
    setFocusTo((current) => ({ n: (current?.n ?? 0) + 1, to }));
  }

  /** Places a refused save's field errors; true when any was placed. */
  function placeServerErrors(caught: unknown): boolean {
    if (isDuplicateName(caught)) {
      setFieldError('name', {
        message: 'academics.templates.validation.nameTaken',
      });
      setFocus('name');
      return true;
    }
    if (!(caught instanceof ApiError) || caught.fieldErrors.length === 0) {
      return false;
    }
    let first: TemplateField | null = null;
    for (const fieldError of caught.fieldErrors) {
      const field = templateFieldOf(fieldError);
      if (field) {
        setFieldError(field, {
          message: serverTemplateMessage(fieldError.code),
        });
        first ??= field;
      }
    }
    if (!first) {
      return false;
    }
    if (first === 'criteria' || first === 'kind') {
      focusNext('add');
    } else {
      setFocus(first);
    }
    return true;
  }

  async function save(submitted: TemplateFormValues) {
    setError(null);
    setSaved(false);
    leaving.current = !editing;
    try {
      await onSave(submitted);
      leaving.current = false;
      if (editing) {
        setSaved(true);
        focusNext('saved');
      }
    } catch (caught) {
      leaving.current = false;
      if (!placeServerErrors(caught)) {
        setError(caught);
        focusNext('alert');
      }
    }
  }

  function run(event: FormEvent<HTMLFormElement> | null, active?: boolean) {
    event?.preventDefault();
    if (inFlight.current) {
      return;
    }
    if (active !== undefined) {
      setValue('isActive', active, { shouldDirty: true });
    }
    inFlight.current = true;
    void handleSubmit(save, () => {
      // A refused validation keeps the stored status.
      if (template && active !== undefined) {
        setValue('isActive', template.is_active);
      }
    })().finally(() => {
      inFlight.current = false;
    });
  }

  function moveRow(index: number, offset: -1 | 1) {
    const to = index + offset;
    const key = fields[index]!.id;
    move(index, to);
    setAnnouncement(
      t('academics.templates.criteria.moved', { position: to + 1 }),
    );
    focusNext({ row: key, control: offset < 0 ? 'up' : 'down' });
  }

  const nameMessage = message(errors.name?.message);
  const kindMessage = message(errors.kind?.message);
  const subjectMessage = message(errors.subjectId?.message);
  const descriptionMessage = message(errors.description?.message);
  const criteriaMessage = message(
    errors.criteria?.root?.message ?? errors.criteria?.message,
  );
  const atMax = fields.length >= CRITERIA_MAX;
  const subjectChoices =
    template?.subject &&
    !subjects.some((subject) => subject.id === template.subject!.id)
      ? [
          ...subjects,
          {
            id: template.subject.id,
            name: template.subject.name,
            levelName: '',
          },
        ]
      : subjects;
  const field = (name: string) => `${id}-${name}`;
  const described = (...parts: (string | false | undefined)[]) =>
    parts.filter(Boolean).join(' ') || undefined;

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      {editing ? (
        <div className="flex flex-wrap items-center gap-3">
          <CatalogStatusBadge active={template.is_active} />
        </div>
      ) : null}
      {saved ? (
        <p
          ref={savedRef}
          role="status"
          tabIndex={-1}
          className="text-sm font-medium outline-none"
        >
          {t('academics.templates.saved')}
        </p>
      ) : null}
      {error ? (
        <div ref={alertRef} tabIndex={-1} className="outline-none">
          {isConflict(error) ? (
            <ErrorAlert
              error={error}
              message={t('academics.templates.conflict')}
              retryLabel={t('academics.reload')}
              onRetry={() => {
                setError(null);
                // #308 QA D3: the alert and its Reload are gone: Name.
                void onReload().then((fresh) => {
                  if (fresh && fresh.version !== shownVersion.current) {
                    // Focus once the form is reset to that version (above).
                    focusNameOnVersion.current = fresh.version;
                  } else {
                    // Already shown (or nothing new): no reset follows.
                    focusNext('name');
                  }
                });
              }}
            />
          ) : (
            <ErrorAlert error={error} />
          )}
        </div>
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={(event) => run(event)}
        className="flex min-w-0 flex-col gap-6"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('name')} className="text-sm font-medium">
            {t('academics.templates.fields.name')}
          </label>
          <Input
            id={field('name')}
            autoComplete="off"
            maxLength={NAME_MAX + 20}
            aria-invalid={nameMessage ? true : undefined}
            aria-describedby={described(nameMessage && field('name-error'))}
            {...register('name')}
          />
          <FieldError id={field('name-error')} message={nameMessage} />
        </div>
        <div className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-2">
            {editing ? (
              <>
                <p className="text-sm font-medium">
                  {t('academics.templates.fields.kind')}
                </p>
                <p className="text-sm">
                  {t(`academics.kinds.${template.kind}`)}
                </p>
                <p className="text-sm text-muted-foreground">
                  {t('academics.templates.kindFixed')}
                </p>
                <FieldError id={field('kind-error')} message={kindMessage} />
              </>
            ) : (
              <>
                <label htmlFor={field('kind')} className="text-sm font-medium">
                  {t('academics.templates.fields.kind')}
                </label>
                <select
                  id={field('kind')}
                  className={SELECT_CLASS}
                  aria-invalid={kindMessage ? true : undefined}
                  aria-describedby={described(
                    kindMessage && field('kind-error'),
                  )}
                  {...register('kind')}
                >
                  <option value="">
                    {t('academics.templates.fields.chooseKind')}
                  </option>
                  {EVALUATION_KINDS.map((kind: EvaluationKind) => (
                    <option key={kind} value={kind}>
                      {t(`academics.kinds.${kind}`)}
                    </option>
                  ))}
                </select>
                <FieldError id={field('kind-error')} message={kindMessage} />
              </>
            )}
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={field('subject')} className="text-sm font-medium">
              {t('academics.templates.fields.subject')}
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
              <option value="">{t('academics.templates.anySubject')}</option>
              {subjectChoices.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.levelName
                    ? t('academics.subjectOption', {
                        subject: subject.name,
                        level: subject.levelName,
                      })
                    : subject.name}
                </option>
              ))}
            </select>
            <FieldError id={field('subject-error')} message={subjectMessage} />
          </div>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('description')} className="text-sm font-medium">
            {t('academics.templates.fields.description')}
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
            max={DESCRIPTION_MAX}
          />
        </div>

        <section
          aria-labelledby={field('criteria-heading')}
          className="flex min-w-0 flex-col gap-3"
        >
          <h2 id={field('criteria-heading')} className="text-lg font-semibold">
            {t('academics.templates.criteria.heading')}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t('academics.templates.criteria.help', { max: CRITERIA_MAX })}
          </p>
          {criteriaMessage ? (
            <p role="alert" className="text-sm text-destructive">
              {criteriaMessage}
            </p>
          ) : null}
          <p role="status" aria-live="polite" className="sr-only">
            {announcement}
          </p>
          <ol ref={rowsRef} className="flex min-w-0 flex-col gap-4">
            {fields.map((row, index) => {
              const number = index + 1;
              const title = values.criteria?.[index]?.title?.trim() ?? '';
              const name = title
                ? t('academics.templates.criteria.rowNamed', {
                    number,
                    title,
                  })
                : t('academics.templates.criteria.rowUnnamed', { number });
              const rowErrors = errors.criteria?.[index];
              const titleMessage = message(rowErrors?.title?.message);
              const descMessage = message(rowErrors?.description?.message);
              const maxMessage = message(rowErrors?.maxScore?.message);
              const rowId = (part: string) => field(`c-${row.id}-${part}`);
              return (
                <li
                  key={row.id}
                  data-row={row.id}
                  className="flex min-w-0 flex-col gap-3 rounded-md border border-border p-4"
                >
                  <h3 className="text-base font-semibold break-words">
                    {t('academics.templates.criteria.row', { number })}
                  </h3>
                  <div className="flex min-w-0 flex-col gap-2">
                    <label
                      htmlFor={rowId('title')}
                      className="text-sm font-medium"
                    >
                      {t('academics.templates.criteria.titleLabel', {
                        number,
                      })}
                    </label>
                    <Input
                      id={rowId('title')}
                      autoComplete="off"
                      maxLength={CRITERION_TITLE_MAX + 20}
                      aria-invalid={titleMessage ? true : undefined}
                      aria-describedby={described(
                        titleMessage && rowId('title-error'),
                      )}
                      {...register(`criteria.${index}.title`)}
                    />
                    <FieldError
                      id={rowId('title-error')}
                      message={titleMessage}
                    />
                  </div>
                  <div className="flex min-w-0 flex-col gap-2">
                    <label
                      htmlFor={rowId('description')}
                      className="text-sm font-medium"
                    >
                      {t('academics.templates.criteria.descriptionLabel', {
                        number,
                      })}
                    </label>
                    <textarea
                      id={rowId('description')}
                      rows={2}
                      className={TEXTAREA_CLASS}
                      aria-invalid={descMessage ? true : undefined}
                      aria-describedby={described(
                        descMessage && rowId('description-error'),
                        rowId('description-counter'),
                      )}
                      {...register(`criteria.${index}.description`)}
                    />
                    <FieldError
                      id={rowId('description-error')}
                      message={descMessage}
                    />
                    <Counter
                      id={rowId('description-counter')}
                      value={values.criteria?.[index]?.description ?? ''}
                      max={CRITERION_DESCRIPTION_MAX}
                    />
                  </div>
                  <div className="flex min-w-0 flex-col gap-2 sm:max-w-xs">
                    <label
                      htmlFor={rowId('max')}
                      className="text-sm font-medium"
                    >
                      {t('academics.templates.criteria.maxLabel', { number })}
                    </label>
                    <Input
                      id={rowId('max')}
                      inputMode="decimal"
                      autoComplete="off"
                      aria-invalid={maxMessage ? true : undefined}
                      aria-describedby={described(
                        rowId('max-help'),
                        maxMessage && rowId('max-error'),
                      )}
                      {...register(`criteria.${index}.maxScore`)}
                    />
                    <p
                      id={rowId('max-help')}
                      className="text-sm text-muted-foreground"
                    >
                      {t('academics.templates.criteria.maxHelp')}
                    </p>
                    <FieldError id={rowId('max-error')} message={maxMessage} />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      data-move="up"
                      disabled={index === 0}
                      aria-label={t('academics.templates.criteria.moveUp', {
                        name,
                      })}
                      onClick={() => moveRow(index, -1)}
                    >
                      {t('academics.templates.criteria.up')}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      data-move="down"
                      disabled={index === fields.length - 1}
                      aria-label={t('academics.templates.criteria.moveDown', {
                        name,
                      })}
                      onClick={() => moveRow(index, 1)}
                    >
                      {t('academics.templates.criteria.down')}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={fields.length === 1}
                      aria-label={t('academics.templates.criteria.remove', {
                        name,
                      })}
                      onClick={() => {
                        remove(index);
                        setAnnouncement(
                          t('academics.templates.criteria.removed', {
                            number,
                          }),
                        );
                        focusNext('add');
                      }}
                    >
                      {t('academics.templates.criteria.removeShort')}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ol>
          <div className="flex flex-col items-start gap-1">
            <Button
              ref={addRef}
              type="button"
              variant="outline"
              disabled={atMax}
              aria-describedby={atMax ? field('add-max') : undefined}
              onClick={() =>
                append({ ...EMPTY_CRITERION }, { shouldFocus: true })
              }
            >
              {t('academics.templates.criteria.add')}
            </Button>
            {atMax ? (
              <p
                id={field('add-max')}
                className="text-sm text-muted-foreground"
              >
                {t('academics.templates.criteria.atMax', { max: CRITERIA_MAX })}
              </p>
            ) : null}
          </div>
        </section>

        <div className="flex flex-col items-start gap-4 border-t border-border pt-4">
          {editing ? (
            <div className="flex flex-col items-start gap-2">
              <p className="text-sm text-muted-foreground">
                {t('academics.templates.activeHelp')}
              </p>
              <Button
                type="button"
                variant="outline"
                disabled={isSubmitting}
                onClick={() => run(null, !template.is_active)}
              >
                {template.is_active
                  ? t('academics.templates.deactivate')
                  : t('academics.templates.activate')}
              </Button>
            </div>
          ) : null}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('academics.templates.saving')} showLabel />
            ) : (
              t('academics.templates.save')
            )}
          </Button>
        </div>
      </form>
      <LeaveDialog blocker={blocker} />
    </div>
  );
}
