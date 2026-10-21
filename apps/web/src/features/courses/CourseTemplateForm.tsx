import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import type { CourseTemplate, CourseTemplateWrite, SubjectOption } from './api';
import {
  ASSESSMENTS_MAX,
  emptyValues,
  hasErrors,
  MODULES_MAX,
  moveRow,
  newAssessmentRow,
  newModuleRow,
  NO_ERRORS,
  removeModule,
  serverErrors,
  SUBJECTS_MAX,
  templateBody,
  templateValues,
  totalMinutes,
  validateTemplate,
  type AssessmentRow,
  type MessageKey,
  type ModuleRow,
  type TemplateErrors,
  type TemplateFormValues,
} from './rules';
import { useDuration } from './useDuration';

// The course template editor (#64 G3, G4, G7): one long form on its own
// page. "General" (name, description, the subjects as a checkbox group of
// the catalog's active subjects plus stored ones deactivated since, the
// number of sessions, the session length and the derived total); "Modules"
// (title, subject among the template's, planned sessions, "Required",
// description, materials references; "Move up" / "Move down", "Remove",
// "Add module"); "Assessments" (title, module or "Whole course",
// "Required", minimum score; the same controls); "Completion criteria"
// (minimum attendance and a read-only summary of what is required).
// Saving sends the whole document; new rows send no id. The checks mirror
// the API's (row errors, focus on the first); `name_taken` shows on the
// name and `version_conflict` offers "Reload". Every row control names its
// row ("Module 2 title"); a move is announced ("Module moved to position
// 1.") and keeps focus on the moved row; "Add" focuses the new row's title
// and "Remove" focuses the "Add" button.

export const TEXTAREA_CLASS =
  'w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30';

export type SubjectOptionsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly SubjectOption[] };

interface CourseTemplateFormProps {
  /** The stored template; none for a new one. */
  template?: CourseTemplate | null;
  subjects: SubjectOptionsState;
  /** Sends the document (with the loaded `version` for a stored one);
   * rejects with the error to show. */
  onSave: (body: CourseTemplateWrite) => Promise<void>;
  /** Refetches the template after a version conflict. */
  onReload?: () => Promise<void>;
  /** A status to show first (for example "Saved." after a create). */
  initialStatus?: string;
}

type Kind = 'module' | 'assessment';

const fieldId = (prefix: string, kind: Kind, key: string, field: string) =>
  `${prefix}-${kind}-${key}-${field}`;

export function CourseTemplateForm({
  template = null,
  subjects,
  onSave,
  onReload,
  initialStatus = '',
}: CourseTemplateFormProps) {
  const { t } = useTranslation();
  const duration = useDuration();
  const id = useId();
  const [values, setValues] = useState<TemplateFormValues>(() =>
    template ? templateValues(template) : emptyValues(),
  );
  const [errors, setErrors] = useState<TemplateErrors>(NO_ERRORS);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState(initialStatus);
  const [announcement, setAnnouncement] = useState('');
  const sending = useRef(false);
  const focusNext = useRef<string | null>(null);
  const ids = {
    name: `${id}-name`,
    description: `${id}-description`,
    subjects: `${id}-subjects`,
    sessionCount: `${id}-session-count`,
    sessionMinutes: `${id}-session-minutes`,
    minAttendance: `${id}-min-attendance`,
    addModule: `${id}-add-module`,
    addAssessment: `${id}-add-assessment`,
    modules: `${id}-modules`,
    assessments: `${id}-assessments`,
  };

  // A new version from the server (a save or a reload): the form shows it
  // (adjusted while rendering, so focus stays on the Save button).
  const [shownVersion, setShownVersion] = useState(template?.version ?? 0);
  if (template && template.version !== shownVersion) {
    setShownVersion(template.version);
    setValues(templateValues(template));
    setErrors(NO_ERRORS);
    setChecked(false);
  }

  useEffect(() => {
    const target = focusNext.current;
    if (target === null) {
      return;
    }
    focusNext.current = null;
    document.getElementById(target)?.focus();
  }, [values]);

  const focusErrors = useRef<TemplateErrors | null>(null);

  function update(next: TemplateFormValues) {
    setValues(next);
    setStatus('');
    if (checked) {
      setErrors(validateTemplate(next));
    }
  }

  function updateModule(index: number, change: Partial<ModuleRow>) {
    update({
      ...values,
      modules: values.modules.map((row, at) =>
        at === index ? { ...row, ...change } : row,
      ),
    });
  }

  function updateAssessment(index: number, change: Partial<AssessmentRow>) {
    update({
      ...values,
      assessments: values.assessments.map((row, at) =>
        at === index ? { ...row, ...change } : row,
      ),
    });
  }

  function move(kind: Kind, index: number, offset: number) {
    const rows = kind === 'module' ? values.modules : values.assessments;
    const row = rows[index];
    const target = index + offset;
    if (!row || target < 0 || target >= rows.length) {
      return;
    }
    // Focus stays on the moved row: the same button, or the other one at
    // the top or the bottom.
    const atEdge = target === 0 || target === rows.length - 1;
    const direction = offset < 0 ? 'up' : 'down';
    const other = offset < 0 ? 'down' : 'up';
    focusNext.current = fieldId(id, kind, row.key, atEdge ? other : direction);
    setAnnouncement(
      t(
        kind === 'module'
          ? 'courses.templates.moduleMoved'
          : 'courses.templates.assessmentMoved',
        { position: target + 1 },
      ),
    );
    if (kind === 'module') {
      update({ ...values, modules: moveRow(values.modules, index, offset) });
    } else {
      update({
        ...values,
        assessments: moveRow(values.assessments, index, offset),
      });
    }
  }

  function addModule() {
    const row = newModuleRow();
    focusNext.current = fieldId(id, 'module', row.key, 'title');
    update({ ...values, modules: [...values.modules, row] });
  }

  function addAssessment() {
    const row = newAssessmentRow();
    focusNext.current = fieldId(id, 'assessment', row.key, 'title');
    update({ ...values, assessments: [...values.assessments, row] });
  }

  function remove(kind: Kind, index: number) {
    if (kind === 'module') {
      focusNext.current = ids.addModule;
      update(removeModule(values, index));
    } else {
      focusNext.current = ids.addAssessment;
      update({
        ...values,
        assessments: values.assessments.filter((_, at) => at !== index),
      });
    }
  }

  function focusFirst(found: TemplateErrors) {
    const order: [keyof TemplateErrors['fields'], string][] = [
      ['name', ids.name],
      ['description', ids.description],
      ['subjects', ids.subjects],
      ['sessionCount', ids.sessionCount],
      ['sessionMinutes', ids.sessionMinutes],
    ];
    for (const [field, target] of order) {
      if (found.fields[field]) {
        const element = document.getElementById(target);
        (field === 'subjects'
          ? (element?.querySelector<HTMLElement>('button[role="checkbox"]') ??
            element)
          : element
        )?.focus();
        return;
      }
    }
    const moduleFields = [
      'title',
      'subjectId',
      'plannedSessions',
      'description',
      'materials',
    ] as const;
    const moduleIndex = values.modules.findIndex(
      (_, index) => found.modules[index],
    );
    if (moduleIndex >= 0) {
      const field = moduleFields.find(
        (name) => found.modules[moduleIndex]?.[name],
      );
      const row = values.modules[moduleIndex];
      if (row && field) {
        document.getElementById(fieldId(id, 'module', row.key, field))?.focus();
        return;
      }
    }
    if (found.fields.modules) {
      document.getElementById(ids.addModule)?.focus();
      return;
    }
    const assessmentIndex = values.assessments.findIndex(
      (_, index) => found.assessments[index],
    );
    if (assessmentIndex >= 0) {
      const field = (['title', 'moduleKey', 'minScore'] as const).find(
        (name) => found.assessments[assessmentIndex]?.[name],
      );
      const row = values.assessments[assessmentIndex];
      if (row && field) {
        document
          .getElementById(fieldId(id, 'assessment', row.key, field))
          ?.focus();
        return;
      }
    }
    if (found.fields.minAttendance) {
      document.getElementById(ids.minAttendance)?.focus();
    }
  }

  // Server errors arrive while the fields are disabled: focus the first
  // one once the form is enabled again.
  useEffect(() => {
    const found = focusErrors.current;
    if (busy || found === null) {
      return;
    }
    focusErrors.current = null;
    focusFirst(found);
  });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current) {
      return;
    }
    setError(null);
    setStatus('');
    const found = validateTemplate(values);
    setErrors(found);
    setChecked(true);
    if (hasErrors(found)) {
      focusFirst(found);
      return;
    }
    sending.current = true;
    setBusy(true);
    try {
      await onSave(templateBody(values));
      setStatus(t('courses.templates.saved'));
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === 'name_taken') {
        const taken: TemplateErrors = {
          ...NO_ERRORS,
          fields: { name: 'courses.templates.validation.nameTaken' },
        };
        setErrors(taken);
        focusErrors.current = taken;
      } else {
        const fromServer =
          caught instanceof ApiError
            ? serverErrors(caught.fieldErrors, values)
            : NO_ERRORS;
        if (hasErrors(fromServer)) {
          setErrors(fromServer);
          focusErrors.current = fromServer;
        } else {
          setError(caught);
        }
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const message = (key: MessageKey | undefined) => (key ? t(key) : undefined);
  const total = totalMinutes(values);

  // The subjects a module may name: the template's, with their names.
  const known = new Map<string, { name: string; active: boolean }>();
  for (const subject of template?.subjects ?? []) {
    known.set(subject.id, { name: subject.name, active: subject.is_active });
  }
  if (subjects.status === 'ready') {
    for (const option of subjects.items) {
      known.set(option.id, { name: option.name, active: true });
    }
  }
  const templateSubjects = values.subjectIds.map((subjectId) => ({
    id: subjectId,
    name: known.get(subjectId)?.name ?? subjectId,
  }));

  return (
    <form
      method="post"
      noValidate
      onSubmit={(event) => void submit(event)}
      className="flex flex-col gap-8"
    >
      <p className="text-sm text-muted-foreground">
        {t('courses.templates.snapshotHelp')}
      </p>
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={
            isConflict && onReload
              ? () => {
                  setError(null);
                  void onReload();
                }
              : undefined
          }
          retryLabel={isConflict ? t('courses.templates.reload') : undefined}
        />
      ) : null}

      <section
        className="flex flex-col gap-4"
        aria-labelledby={`${id}-general`}
      >
        <h2 id={`${id}-general`} className="text-xl font-semibold">
          {t('courses.templates.general')}
        </h2>
        <TextField
          id={ids.name}
          label={t('courses.templates.name')}
          value={values.name}
          maxLength={100}
          disabled={busy}
          message={message(errors.fields.name)}
          onChange={(name) => update({ ...values, name })}
        />
        <TextArea
          id={ids.description}
          label={t('courses.templates.description')}
          value={values.description}
          disabled={busy}
          message={message(errors.fields.description)}
          onChange={(description) => update({ ...values, description })}
        />
        <SubjectPicker
          id={ids.subjects}
          template={template}
          subjects={subjects}
          chosen={values.subjectIds}
          disabled={busy}
          message={message(errors.fields.subjects)}
          onChange={(subjectIds) => update({ ...values, subjectIds })}
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            id={ids.sessionCount}
            label={t('courses.templates.sessionCount')}
            value={values.sessionCount}
            numeric
            disabled={busy}
            message={message(errors.fields.sessionCount)}
            onChange={(sessionCount) => update({ ...values, sessionCount })}
          />
          <TextField
            id={ids.sessionMinutes}
            label={t('courses.templates.sessionMinutes')}
            value={values.sessionMinutes}
            numeric
            disabled={busy}
            message={message(errors.fields.sessionMinutes)}
            onChange={(sessionMinutes) => update({ ...values, sessionMinutes })}
          />
        </div>
        <p className="text-sm" aria-live="polite">
          <span className="font-medium">{t('courses.templates.total')}: </span>
          {total === null
            ? t('courses.templates.totalUnknown')
            : t('courses.templates.totalValue', {
                value: duration(total),
                count: Number(values.sessionCount),
              })}
        </p>
      </section>

      <section
        className="flex flex-col gap-4"
        aria-labelledby={`${id}-modules-title`}
      >
        <h2 id={`${id}-modules-title`} className="text-xl font-semibold">
          {t('courses.templates.modules')}
        </h2>
        <ol id={ids.modules} className="flex flex-col gap-4">
          {values.modules.map((row, index) => (
            <ModuleFields
              key={row.key}
              prefix={id}
              row={row}
              index={index}
              count={values.modules.length}
              subjects={templateSubjects}
              errors={errors.modules[index] ?? {}}
              disabled={busy}
              onChange={(change) => updateModule(index, change)}
              onMove={(offset) => move('module', index, offset)}
              onRemove={() => remove('module', index)}
            />
          ))}
        </ol>
        <FieldError
          id={`${ids.addModule}-error`}
          message={message(errors.fields.modules)}
        />
        <div>
          <Button
            id={ids.addModule}
            type="button"
            variant="outline"
            disabled={busy || values.modules.length >= MODULES_MAX}
            aria-describedby={
              errors.fields.modules ? `${ids.addModule}-error` : undefined
            }
            onClick={addModule}
          >
            {t('courses.templates.addModule')}
          </Button>
        </div>
      </section>

      <section
        className="flex flex-col gap-4"
        aria-labelledby={`${id}-assessments-title`}
      >
        <h2 id={`${id}-assessments-title`} className="text-xl font-semibold">
          {t('courses.templates.assessments')}
        </h2>
        <p className="text-sm text-muted-foreground">
          {t('courses.templates.assessmentsHelp')}
        </p>
        {values.assessments.length === 0 ? (
          <p className="text-sm">{t('courses.templates.noAssessments')}</p>
        ) : (
          <ol id={ids.assessments} className="flex flex-col gap-4">
            {values.assessments.map((row, index) => (
              <AssessmentFields
                key={row.key}
                prefix={id}
                row={row}
                index={index}
                count={values.assessments.length}
                modules={values.modules}
                errors={errors.assessments[index] ?? {}}
                disabled={busy}
                onChange={(change) => updateAssessment(index, change)}
                onMove={(offset) => move('assessment', index, offset)}
                onRemove={() => remove('assessment', index)}
              />
            ))}
          </ol>
        )}
        <FieldError
          id={`${ids.addAssessment}-error`}
          message={message(errors.fields.assessments)}
        />
        <div>
          <Button
            id={ids.addAssessment}
            type="button"
            variant="outline"
            disabled={busy || values.assessments.length >= ASSESSMENTS_MAX}
            onClick={addAssessment}
          >
            {t('courses.templates.addAssessment')}
          </Button>
        </div>
      </section>

      <section
        className="flex flex-col gap-4"
        aria-labelledby={`${id}-completion`}
      >
        <h2 id={`${id}-completion`} className="text-xl font-semibold">
          {t('courses.templates.completion')}
        </h2>
        <TextField
          id={ids.minAttendance}
          label={t('courses.templates.minAttendance')}
          help={t('courses.templates.minAttendanceHelp')}
          value={values.minAttendance}
          numeric
          disabled={busy}
          message={message(errors.fields.minAttendance)}
          onChange={(minAttendance) => update({ ...values, minAttendance })}
        />
        <p className="text-sm text-muted-foreground">
          {t('courses.templates.completionHelp')}
        </p>
        <RequiredSummary values={values} />
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy}>
          {busy ? (
            <Spinner label={t('courses.templates.saving')} showLabel />
          ) : (
            t('courses.templates.save')
          )}
        </Button>
        <p role="status" className="text-sm">
          {status}
        </p>
      </div>
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>
    </form>
  );
}

/** The required modules and assessments, as text (read-only). */
export function RequiredSummary({ values }: { values: TemplateFormValues }) {
  const { t } = useTranslation();
  const id = useId();
  const modules = values.modules
    .map((row, index) => ({ row, number: index + 1 }))
    .filter(({ row }) => row.isRequired);
  const assessments = values.assessments
    .map((row, index) => ({ row, number: index + 1 }))
    .filter(({ row }) => row.isRequired);
  const title = (text: string, fallback: string) => text.trim() || fallback;
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
      <dt id={`${id}-modules`} className="font-medium">
        {t('courses.templates.requiredModules')}
      </dt>
      <dd>
        {modules.length === 0
          ? t('courses.templates.noneRequired')
          : modules
              .map(({ row, number }) =>
                title(
                  row.title,
                  t('courses.templates.moduleHeading', { number }),
                ),
              )
              .join(', ')}
      </dd>
      <dt className="font-medium">
        {t('courses.templates.requiredAssessments')}
      </dt>
      <dd>
        {assessments.length === 0
          ? t('courses.templates.noneRequired')
          : assessments
              .map(({ row, number }) =>
                title(
                  row.title,
                  t('courses.templates.assessmentHeading', { number }),
                ),
              )
              .join(', ')}
      </dd>
    </dl>
  );
}

function TextField({
  id,
  label,
  ariaLabel,
  help,
  value,
  numeric = false,
  maxLength,
  disabled,
  message,
  onChange,
}: {
  id: string;
  label: string;
  ariaLabel?: string;
  help?: string;
  value: string;
  numeric?: boolean;
  maxLength?: number;
  disabled: boolean;
  message: string | undefined;
  onChange: (value: string) => void;
}) {
  const errorId = `${id}-error`;
  const helpId = `${id}-help`;
  const described = [help ? helpId : null, message ? errorId : null]
    .filter(Boolean)
    .join(' ');
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {help ? (
        <p id={helpId} className="text-sm text-muted-foreground">
          {help}
        </p>
      ) : null}
      <Input
        id={id}
        type="text"
        inputMode={numeric ? 'numeric' : undefined}
        autoComplete="off"
        className={numeric ? 'w-32' : undefined}
        maxLength={maxLength}
        value={value}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-invalid={message ? true : undefined}
        aria-describedby={described || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      <FieldError id={errorId} message={message} />
    </div>
  );
}

function TextArea({
  id,
  label,
  ariaLabel,
  help,
  value,
  disabled,
  message,
  onChange,
}: {
  id: string;
  label: string;
  ariaLabel?: string;
  help?: string;
  value: string;
  disabled: boolean;
  message: string | undefined;
  onChange: (value: string) => void;
}) {
  const errorId = `${id}-error`;
  const helpId = `${id}-help`;
  const described = [help ? helpId : null, message ? errorId : null]
    .filter(Boolean)
    .join(' ');
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {help ? (
        <p id={helpId} className="text-sm text-muted-foreground">
          {help}
        </p>
      ) : null}
      <textarea
        id={id}
        rows={3}
        maxLength={2000}
        className={TEXTAREA_CLASS}
        value={value}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-invalid={message ? true : undefined}
        aria-describedby={described || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      <FieldError id={errorId} message={message} />
    </div>
  );
}

function SubjectPicker({
  id,
  template,
  subjects,
  chosen,
  disabled,
  message,
  onChange,
}: {
  id: string;
  template: CourseTemplate | null;
  subjects: SubjectOptionsState;
  chosen: readonly string[];
  disabled: boolean;
  message: string | undefined;
  onChange: (subjectIds: string[]) => void;
}) {
  const { t } = useTranslation();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  // The active subjects, then stored ones not among them (deactivated
  // since), marked "(inactive)".
  const active = subjects.status === 'ready' ? subjects.items : [];
  const activeIds = new Set(active.map((option) => option.id));
  const options = [
    ...active.map((option) => ({ ...option, isActive: true })),
    ...(template?.subjects ?? [])
      .filter((subject) => !activeIds.has(subject.id))
      .map((subject) => ({
        id: subject.id,
        name: subject.name,
        levelName: subject.level_name,
        isActive: false,
      })),
  ];

  function toggle(subjectId: string, on: boolean) {
    onChange(
      on
        ? [...chosen.filter((value) => value !== subjectId), subjectId]
        : chosen.filter((value) => value !== subjectId),
    );
  }

  return (
    <fieldset
      id={id}
      className="flex flex-col gap-2"
      aria-describedby={message ? `${helpId} ${errorId}` : helpId}
    >
      <legend className="text-sm font-medium">
        {t('courses.templates.subjects')}
      </legend>
      <p id={helpId} className="text-sm text-muted-foreground">
        {t('courses.templates.subjectsHelp', { max: SUBJECTS_MAX })}
      </p>
      {subjects.status === 'error' ? (
        <ErrorAlert error={subjects.error} onRetry={subjects.onRetry} />
      ) : null}
      {subjects.status === 'loading' ? (
        <p className="text-sm">{t('courses.templates.subjectsLoading')}</p>
      ) : null}
      {subjects.status === 'ready' && options.length === 0 ? (
        <p className="text-sm">{t('courses.templates.noSubjects')}</p>
      ) : null}
      {options.length > 0 ? (
        <ul className="grid max-h-64 grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2">
          {options.map((option) => {
            const boxId = `${id}-subject-${option.id}`;
            const label = t('courses.templates.subjectOption', {
              name: option.name,
              level: option.levelName,
            });
            return (
              <li key={option.id} className="flex items-center gap-2">
                <Checkbox
                  id={boxId}
                  checked={chosen.includes(option.id)}
                  disabled={disabled}
                  aria-invalid={message ? true : undefined}
                  onCheckedChange={(next) => toggle(option.id, next === true)}
                />
                <label htmlFor={boxId} className="min-w-0 text-sm break-words">
                  {option.isActive
                    ? label
                    : t('courses.templates.inactiveSubject', { name: label })}
                </label>
              </li>
            );
          })}
        </ul>
      ) : null}
      <FieldError id={errorId} message={message} />
    </fieldset>
  );
}

interface RowControlsProps {
  prefix: string;
  kind: Kind;
  rowKey: string;
  index: number;
  count: number;
  disabled: boolean;
  onMove: (offset: number) => void;
  onRemove: () => void;
}

function RowControls({
  prefix,
  kind,
  rowKey,
  index,
  count,
  disabled,
  onMove,
  onRemove,
}: RowControlsProps) {
  const { t } = useTranslation();
  const number = index + 1;
  const labels =
    kind === 'module'
      ? {
          up: t('courses.templates.moveModuleUpLabel', { number }),
          down: t('courses.templates.moveModuleDownLabel', { number }),
          remove: t('courses.templates.removeModuleLabel', { number }),
        }
      : {
          up: t('courses.templates.moveAssessmentUpLabel', { number }),
          down: t('courses.templates.moveAssessmentDownLabel', { number }),
          remove: t('courses.templates.removeAssessmentLabel', { number }),
        };
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        id={fieldId(prefix, kind, rowKey, 'up')}
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled || index === 0}
        aria-label={labels.up}
        onClick={() => onMove(-1)}
      >
        {t('courses.templates.moveUp')}
      </Button>
      <Button
        id={fieldId(prefix, kind, rowKey, 'down')}
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled || index === count - 1}
        aria-label={labels.down}
        onClick={() => onMove(1)}
      >
        {t('courses.templates.moveDown')}
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled || (kind === 'module' && count === 1)}
        aria-label={labels.remove}
        onClick={onRemove}
      >
        {t('courses.templates.remove')}
      </Button>
    </div>
  );
}

function ModuleFields({
  prefix,
  row,
  index,
  count,
  subjects,
  errors,
  disabled,
  onChange,
  onMove,
  onRemove,
}: {
  prefix: string;
  row: ModuleRow;
  index: number;
  count: number;
  subjects: readonly { id: string; name: string }[];
  errors: TemplateErrors['modules'][number];
  disabled: boolean;
  onChange: (change: Partial<ModuleRow>) => void;
  onMove: (offset: number) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const number = index + 1;
  const id = (field: string) => fieldId(prefix, 'module', row.key, field);
  const message = (key: MessageKey | undefined) => (key ? t(key) : undefined);
  const subjectMessage = message(errors.subjectId);
  const subjectChoices = subjects.some((s) => s.id === row.subjectId)
    ? subjects
    : row.subjectId
      ? [...subjects, { id: row.subjectId, name: row.subjectId }]
      : subjects;
  return (
    <li className="flex flex-col gap-3 rounded-md border border-border p-4">
      <h3 className="text-base font-semibold">
        {t('courses.templates.moduleHeading', { number })}
      </h3>
      <TextField
        id={id('title')}
        label={t('courses.templates.moduleTitle')}
        ariaLabel={t('courses.templates.moduleTitleLabel', { number })}
        value={row.title}
        maxLength={200}
        disabled={disabled}
        message={message(errors.title)}
        onChange={(title) => onChange({ title })}
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-1">
          <label htmlFor={id('subjectId')} className="text-sm font-medium">
            {t('courses.templates.moduleSubject')}
          </label>
          <select
            id={id('subjectId')}
            className={SELECT_CLASS}
            value={row.subjectId}
            disabled={disabled}
            aria-label={t('courses.templates.moduleSubjectLabel', { number })}
            aria-invalid={subjectMessage ? true : undefined}
            aria-describedby={
              subjectMessage ? `${id('subjectId')}-error` : undefined
            }
            onChange={(event) => onChange({ subjectId: event.target.value })}
          >
            <option value="">{t('courses.templates.noSubject')}</option>
            {subjectChoices.map((subject) => (
              <option key={subject.id} value={subject.id}>
                {subject.name}
              </option>
            ))}
          </select>
          <FieldError
            id={`${id('subjectId')}-error`}
            message={subjectMessage}
          />
        </div>
        <TextField
          id={id('plannedSessions')}
          label={t('courses.templates.plannedSessions')}
          ariaLabel={t('courses.templates.plannedSessionsLabel', { number })}
          value={row.plannedSessions}
          numeric
          disabled={disabled}
          message={message(errors.plannedSessions)}
          onChange={(plannedSessions) => onChange({ plannedSessions })}
        />
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id={id('required')}
          checked={row.isRequired}
          disabled={disabled}
          aria-label={t('courses.templates.moduleRequiredLabel', { number })}
          onCheckedChange={(next) => onChange({ isRequired: next === true })}
        />
        <label htmlFor={id('required')} className="text-sm">
          {t('courses.templates.required')}
        </label>
      </div>
      <TextArea
        id={id('description')}
        label={t('courses.templates.moduleDescription')}
        ariaLabel={t('courses.templates.moduleDescriptionLabel', { number })}
        value={row.description}
        disabled={disabled}
        message={message(errors.description)}
        onChange={(description) => onChange({ description })}
      />
      <TextArea
        id={id('materials')}
        label={t('courses.templates.materials')}
        ariaLabel={t('courses.templates.materialsLabel', { number })}
        help={t('courses.templates.materialsHelp')}
        value={row.materials}
        disabled={disabled}
        message={message(errors.materials)}
        onChange={(materials) => onChange({ materials })}
      />
      <RowControls
        prefix={prefix}
        kind="module"
        rowKey={row.key}
        index={index}
        count={count}
        disabled={disabled}
        onMove={onMove}
        onRemove={onRemove}
      />
    </li>
  );
}

function AssessmentFields({
  prefix,
  row,
  index,
  count,
  modules,
  errors,
  disabled,
  onChange,
  onMove,
  onRemove,
}: {
  prefix: string;
  row: AssessmentRow;
  index: number;
  count: number;
  modules: readonly ModuleRow[];
  errors: TemplateErrors['assessments'][number];
  disabled: boolean;
  onChange: (change: Partial<AssessmentRow>) => void;
  onMove: (offset: number) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const number = index + 1;
  const id = (field: string) => fieldId(prefix, 'assessment', row.key, field);
  const message = (key: MessageKey | undefined) => (key ? t(key) : undefined);
  const moduleMessage = message(errors.moduleKey);
  return (
    <li className="flex flex-col gap-3 rounded-md border border-border p-4">
      <h3 className="text-base font-semibold">
        {t('courses.templates.assessmentHeading', { number })}
      </h3>
      <TextField
        id={id('title')}
        label={t('courses.templates.assessmentTitle')}
        ariaLabel={t('courses.templates.assessmentTitleLabel', { number })}
        value={row.title}
        maxLength={200}
        disabled={disabled}
        message={message(errors.title)}
        onChange={(title) => onChange({ title })}
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-1">
          <label htmlFor={id('moduleKey')} className="text-sm font-medium">
            {t('courses.templates.assessmentModule')}
          </label>
          <select
            id={id('moduleKey')}
            className={SELECT_CLASS}
            value={row.moduleKey}
            disabled={disabled}
            aria-label={t('courses.templates.assessmentModuleLabel', {
              number,
            })}
            aria-invalid={moduleMessage ? true : undefined}
            aria-describedby={
              moduleMessage ? `${id('moduleKey')}-error` : undefined
            }
            onChange={(event) => onChange({ moduleKey: event.target.value })}
          >
            <option value="">{t('courses.templates.wholeCourse')}</option>
            {modules.map((module, at) => (
              <option key={module.key} value={module.key}>
                {module.title.trim() ||
                  t('courses.templates.untitledModule', { number: at + 1 })}
              </option>
            ))}
          </select>
          <FieldError id={`${id('moduleKey')}-error`} message={moduleMessage} />
        </div>
        <TextField
          id={id('minScore')}
          label={t('courses.templates.minScore')}
          ariaLabel={t('courses.templates.minScoreLabel', { number })}
          value={row.minScore}
          numeric
          disabled={disabled}
          message={message(errors.minScore)}
          onChange={(minScore) => onChange({ minScore })}
        />
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id={id('required')}
          checked={row.isRequired}
          disabled={disabled}
          aria-label={t('courses.templates.assessmentRequiredLabel', {
            number,
          })}
          onCheckedChange={(next) => onChange({ isRequired: next === true })}
        />
        <label htmlFor={id('required')} className="text-sm">
          {t('courses.templates.assessmentRequired')}
        </label>
      </div>
      <RowControls
        prefix={prefix}
        kind="assessment"
        rowKey={row.key}
        index={index}
        count={count}
        disabled={disabled}
        onMove={onMove}
        onRemove={onRemove}
      />
    </li>
  );
}
