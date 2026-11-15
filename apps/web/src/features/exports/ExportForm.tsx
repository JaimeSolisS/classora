import { useQuery } from '@tanstack/react-query';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Combobox } from '@/components/ui/combobox';
import { Input } from '@/components/ui/input';
import type { FilterOption } from '@/features/calendar/api';
import { SELECT_CLASS } from '@/features/students/styles';
import type { LocalDate } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { ExportDatasetCode } from './access';
import { API_FORM_LOADERS, type ExportFormLoaders } from './loaders';
import {
  EXPORT_FORMATS,
  EXPORTS_QUERY_KEY,
  requestExport,
  type ExportColumn,
  type ExportDataset,
  type ExportJob,
  type ExportParameters,
} from './api';
import {
  defaultValues,
  effectiveGroupBy,
  EXPENSE_STATUS_FILTERS,
  exportParameters,
  groupOptions,
  moveColumn,
  SERVER_FILTER_FIELDS,
  SESSION_KIND_FILTERS,
  STUDENT_STATUS_FILTERS,
  validate,
  type ExportField,
  type ExportFormValues,
} from './rules';
import { SaveSetupDialog, type SetupFields } from './SaveSetupDialog';
import { createSetup, updateSetup, type ReportSetup } from './setups';

// "New export" (M8-07b B): the dataset (radio group), the format, the
// columns (a checklist in catalogue order, all checked, reordered with
// keyboard-accessible Up/Down buttons, no drag library), the grouping
// (the checked groupable columns) and the dataset's filters. The client
// checks mirror the API (B2); a 422 maps to its field. A personal dataset
// shows the password hint (E3): its request may answer
// `403 reauth_required`, and apiCall then opens the password dialog and
// resends it once. `409 export_limit_reached` reads "3 in progress".

/** A dataset the web knows (the page drops any other, D2). */
export type KnownDataset = ExportDataset & { code: ExportDatasetCode };

interface ExportFormProps {
  datasets: readonly KnownDataset[];
  initialDataset: ExportDatasetCode;
  /** Today in the center's zone (the class sessions' default month). */
  today: LocalDate;
  /** A `202`: the page adds it to the history and announces it. */
  onRequested: (job: ExportJob) => void;
  loaders?: ExportFormLoaders;
  /** Stories: the request instead of the API. */
  submit?: typeof requestExport;
  /** M8-08 "Cargar en el formulario": the values of a saved setup. */
  initialValues?: ExportFormValues;
  /** The loaded setup, when it is the caller's own (D3 "Actualizar"). */
  loadedSetup?: ReportSetup | null;
  /** A setup was saved (`created`) or the loaded one updated. */
  onSetupSaved?: (setup: ReportSetup, how: 'created' | 'updated') => void;
  /** 409 version_conflict on "Actualizar": the page reloads the list. */
  onSetupsStale?: () => void;
  /** Stories: the setup calls instead of the API. */
  saveSetup?: typeof createSetup;
  changeSetup?: typeof updateSetup;
}

type FieldMessages = Partial<Record<ExportField, string>>;

/** The messages without `field`'s. */
function without(messages: FieldMessages, field: ExportField): FieldMessages {
  return Object.fromEntries(
    Object.entries(messages).filter(([name]) => name !== field),
  );
}

const FIELD_ORDER: readonly ExportField[] = [
  'columns',
  'from',
  'to',
  'incurredFrom',
  'incurredTo',
];

const CHECK_CLASS =
  'size-4 shrink-0 accent-primary outline-none focus-visible:ring-3 focus-visible:ring-ring';
const OPTION_CLASS = 'flex min-h-11 items-center gap-2 text-sm md:min-h-8';
const SELECT = cn(SELECT_CLASS, 'min-h-11 md:min-h-9');

export function ExportForm({
  datasets,
  initialDataset,
  today,
  onRequested,
  loaders = API_FORM_LOADERS,
  submit = requestExport,
  initialValues,
  loadedSetup = null,
  onSetupSaved,
  onSetupsStale,
  saveSetup = createSetup,
  changeSetup = updateSetup,
}: ExportFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const ids = {
    dataset: `${id}-dataset`,
    format: `${id}-format`,
    hint: `${id}-hint`,
    columnsHelp: `${id}-columns-help`,
    groupBy: `${id}-group-by`,
    from: `${id}-from`,
    to: `${id}-to`,
    incurredFrom: `${id}-incurred-from`,
    incurredTo: `${id}-incurred-to`,
    columns: `${id}-columns`,
    classroom: `${id}-classroom`,
    kind: `${id}-kind`,
    cancelled: `${id}-cancelled`,
    statusesHelp: `${id}-statuses-help`,
    studentStatus: `${id}-student-status`,
    level: `${id}-level`,
  };
  const errorId = (field: ExportField) => `${id}-${field}-error`;

  const datasetOf = (code: ExportDatasetCode) =>
    datasets.find((dataset) => dataset.code === code) ?? datasets[0]!;
  const [values, setValues] = useState<ExportFormValues>(
    () =>
      initialValues ??
      defaultValues(initialDataset, datasetOf(initialDataset).columns, today),
  );
  // The body "Guardar configuración" saves (the request's own builder, D3).
  const [saving, setSaving] = useState<ExportParameters | null>(null);
  const current = datasetOf(values.dataset);
  const catalogue = current.columns;
  const columnOf = (code: string): ExportColumn | undefined =>
    catalogue.find((column) => column.code === code);

  const [errors, setErrors] = useState<FieldMessages>({});
  const [failure, setFailure] = useState<{ error: unknown } | null>(null);
  const [limitMax, setLimitMax] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [moved, setMoved] = useState('');
  const moveButtons = useRef(new Map<string, HTMLButtonElement>());
  const [focusAfterMove, setFocusAfterMove] = useState<{
    code: string;
    direction: 'up' | 'down';
  } | null>(null);

  useEffect(() => {
    if (focusAfterMove === null) {
      return;
    }
    const { code, direction } = focusAfterMove;
    const other = direction === 'up' ? 'down' : 'up';
    const target = moveButtons.current.get(`${code}:${direction}`);
    (target && !target.disabled
      ? target
      : moveButtons.current.get(`${code}:${other}`)
    )?.focus();
  }, [focusAfterMove]);

  const rooms = useQuery({
    queryKey: [...EXPORTS_QUERY_KEY, 'rooms'],
    queryFn: ({ signal }) => loaders.fetchRooms(signal),
    enabled: values.dataset === 'class_sessions',
  });
  const levels = useQuery({
    queryKey: [...EXPORTS_QUERY_KEY, 'levels'],
    queryFn: ({ signal }) => loaders.fetchLevels(signal),
    enabled: values.dataset === 'students',
  });

  function update(change: Partial<ExportFormValues>) {
    setValues((previous) => ({ ...previous, ...change }));
  }

  function chooseDataset(code: ExportDatasetCode) {
    // Another dataset: its own columns and filters; the format stays.
    setValues((previous) =>
      defaultValues(code, datasetOf(code).columns, today, previous.format),
    );
    setErrors({});
    setFailure(null);
    setLimitMax(null);
    setMoved('');
  }

  function toggleColumn(code: string, checked: boolean) {
    setValues((previous) => ({
      ...previous,
      columns: previous.columns.map((choice) =>
        choice.code === code ? { ...choice, checked } : choice,
      ),
    }));
    if (checked && errors.columns) {
      setErrors((previous) => without(previous, 'columns'));
    }
  }

  function move(index: number, direction: 'up' | 'down') {
    const next = moveColumn(values.columns, index, direction === 'up' ? -1 : 1);
    const code = values.columns[index]!.code;
    const position = next.findIndex((choice) => choice.code === code) + 1;
    update({ columns: next });
    setMoved(
      t('exports.form.moved', {
        column: columnOf(code)?.label ?? code,
        position,
        total: next.length,
      }),
    );
    setFocusAfterMove({ code, direction });
  }

  function focusFirst(messages: FieldMessages) {
    const first = FIELD_ORDER.find((field) => messages[field]);
    if (!first) {
      return;
    }
    const element =
      first === 'columns'
        ? document
            .getElementById(ids.columns)
            ?.querySelector<HTMLInputElement>('input[type="checkbox"]')
        : document.getElementById(ids[first]);
    element?.focus();
  }

  /** The client checks (B2): false, with the messages shown, when any fails. */
  function checked(): boolean {
    const found = validate(values);
    const messages: FieldMessages = Object.fromEntries(
      Object.entries(found).map(([field, key]) => [
        field,
        t(`exports.validation.${key}`),
      ]),
    );
    setErrors(messages);
    if (Object.keys(messages).length > 0) {
      focusFirst(messages);
      return false;
    }
    return true;
  }

  /** A 422 at a filter goes to its field; false when none maps. */
  function showFieldProblems(error: unknown): boolean {
    if (!(error instanceof ApiError) || error.fieldErrors.length === 0) {
      return false;
    }
    const mapped: FieldMessages = {};
    for (const fieldError of error.fieldErrors) {
      // `["body","filters",f]` (a request) or `["body","parameters",
      // "filters",f]` (a saved setup, M8-08).
      const at = fieldError.path.indexOf('filters');
      const field =
        at > 0
          ? SERVER_FILTER_FIELDS[String(fieldError.path[at + 1])]
          : undefined;
      if (field && !mapped[field]) {
        mapped[field] =
          fieldError.code === 'export_range_invalid'
            ? tErrors('export_range_invalid')
            : t('exports.validation.invalid');
      }
    }
    if (Object.keys(mapped).length === 0) {
      return false;
    }
    setErrors(mapped);
    focusFirst(mapped);
    return true;
  }

  function onSaveSetup() {
    setFailure(null);
    setLimitMax(null);
    if (checked()) {
      setSaving(exportParameters(values, catalogue));
    }
  }

  async function saveAs(fields: SetupFields) {
    const setup = await saveSetup({
      name: fields.name,
      parameters: saving!,
      shared_role: fields.sharedRole,
    });
    onSetupSaved?.(setup, 'created');
  }

  async function saveOver(fields: SetupFields) {
    const setup = await changeSetup(loadedSetup!.id, {
      version: loadedSetup!.version,
      name: fields.name,
      parameters: saving!,
      shared_role: fields.sharedRole,
    });
    onSetupSaved?.(setup, 'updated');
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setFailure(null);
    setLimitMax(null);
    if (!checked()) {
      return;
    }
    setSubmitting(true);
    try {
      const job = await submit(exportParameters(values, catalogue));
      setMoved('');
      onRequested(job);
    } catch (error) {
      if (error instanceof ApiError && error.code === 'export_limit_reached') {
        const max = error.problem?.details?.max;
        setLimitMax(typeof max === 'number' ? max : 3);
      } else if (!showFieldProblems(error)) {
        setFailure({ error });
      }
    } finally {
      setSubmitting(false);
    }
  }

  const groups = groupOptions(values.columns, catalogue);
  const groupBy = effectiveGroupBy(values, catalogue);

  /** A labelled date input with its message (aria-describedby). */
  function dateField(
    field: 'from' | 'to' | 'incurredFrom' | 'incurredTo',
    value: string,
    onChange: (value: string) => void,
    required: boolean,
  ) {
    const message = errors[field];
    return (
      <div className="flex flex-col gap-2">
        <label htmlFor={ids[field]} className="text-sm font-medium">
          {t(`exports.form.${field}`)}
        </label>
        <Input
          id={ids[field]}
          type="date"
          className="min-h-11 md:min-h-9"
          value={value}
          required={required}
          aria-invalid={message ? true : undefined}
          aria-describedby={message ? errorId(field) : undefined}
          onChange={(event) => {
            onChange(event.target.value);
            if (message) {
              setErrors((previous) => without(previous, field));
            }
          }}
        />
        {message ? (
          <p id={errorId(field)} className="text-sm text-destructive">
            {message}
          </p>
        ) : null}
      </div>
    );
  }

  /**
   * A person filter: the combobox (#545 students, #548 tutors), then the
   * choice and "Remove".
   */
  function personFilter(field: 'tutor' | 'student') {
    const chosen = values.sessions[field];
    const label = t(`exports.form.${field}`);
    const optionLabel = (option: FilterOption) => option.name;
    const onSelect = (option: FilterOption) =>
      update({
        sessions: {
          ...values.sessions,
          [field]: { id: option.id, name: optionLabel(option) },
        },
      });
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <Combobox<FilterOption>
          label={label}
          queryKey={[...EXPORTS_QUERY_KEY, `${field}s`]}
          load={
            field === 'student' ? loaders.searchStudents : loaders.searchTutors
          }
          optionId={(option) => option.id}
          optionLabel={optionLabel}
          emptyText={t(
            field === 'student'
              ? 'combobox.emptyStudents'
              : 'combobox.emptyTutors',
          )}
          selected={chosen ? { id: chosen.id, label: chosen.name } : null}
          onSelect={onSelect}
          inputClassName="min-h-11 md:min-h-9"
          optionClassName="min-h-11 md:min-h-9"
        />
        {chosen ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span>
              {t('exports.form.chosen', { field: label, name: chosen.name })}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="min-h-11 md:min-h-8"
              onClick={() =>
                update({ sessions: { ...values.sessions, [field]: null } })
              }
            >
              {t('exports.form.clearChoice', { field: label })}
            </Button>
          </div>
        ) : null}
      </div>
    );
  }

  function sessionFilters() {
    const s = values.sessions;
    const set = (change: Partial<typeof s>) =>
      update({ sessions: { ...s, ...change } });
    return (
      <>
        <div className="grid gap-4 sm:grid-cols-2">
          {dateField('from', s.from, (from) => set({ from }), true)}
          {dateField('to', s.to, (to) => set({ to }), true)}
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {personFilter('tutor')}
          {personFilter('student')}
        </div>
        <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 md:items-end">
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.classroom} className="text-sm font-medium">
              {t('exports.form.classroom')}
            </label>
            <select
              id={ids.classroom}
              className={SELECT}
              value={s.classroom}
              aria-describedby={
                rooms.isError ? `${ids.classroom}-error` : undefined
              }
              onChange={(event) => set({ classroom: event.target.value })}
            >
              <option value="">{t('exports.form.allClassrooms')}</option>
              {(rooms.data ?? []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.name}
                </option>
              ))}
            </select>
            {rooms.isError ? (
              <p
                id={`${ids.classroom}-error`}
                className="text-sm text-destructive"
              >
                {t('exports.form.optionsFailed')}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.kind} className="text-sm font-medium">
              {t('exports.form.kind')}
            </label>
            <select
              id={ids.kind}
              className={SELECT}
              value={s.kind}
              onChange={(event) =>
                set({ kind: event.target.value as typeof s.kind })
              }
            >
              <option value="">{t('exports.form.allKinds')}</option>
              {SESSION_KIND_FILTERS.map((kind) => (
                <option key={kind} value={kind}>
                  {t(`exports.form.kinds.${kind}`)}
                </option>
              ))}
            </select>
          </div>
          <label htmlFor={ids.cancelled} className={OPTION_CLASS}>
            <input
              id={ids.cancelled}
              type="checkbox"
              className={CHECK_CLASS}
              checked={s.includeCancelled}
              onChange={(event) =>
                set({ includeCancelled: event.target.checked })
              }
            />
            {t('exports.form.includeCancelled')}
          </label>
        </div>
      </>
    );
  }

  function expenseFilters() {
    const e = values.expenses;
    const set = (change: Partial<typeof e>) =>
      update({ expenses: { ...e, ...change } });
    return (
      <>
        <div className="grid gap-4 sm:grid-cols-2">
          {dateField(
            'incurredFrom',
            e.incurredFrom,
            (incurredFrom) => set({ incurredFrom }),
            false,
          )}
          {dateField(
            'incurredTo',
            e.incurredTo,
            (incurredTo) => set({ incurredTo }),
            false,
          )}
        </div>
        <fieldset
          className="flex min-w-0 flex-col gap-1"
          aria-describedby={ids.statusesHelp}
        >
          <legend className="mb-1 text-sm font-medium">
            {t('exports.form.statuses')}
          </legend>
          <p id={ids.statusesHelp} className="text-sm text-muted-foreground">
            {t('exports.form.statusesHelp')}
          </p>
          <div className="flex flex-wrap gap-x-4">
            {EXPENSE_STATUS_FILTERS.map((status) => (
              <label
                key={status}
                htmlFor={`${id}-status-${status}`}
                className={OPTION_CLASS}
              >
                <input
                  id={`${id}-status-${status}`}
                  type="checkbox"
                  className={CHECK_CLASS}
                  checked={e.statuses.includes(status)}
                  onChange={(event) =>
                    set({
                      statuses: event.target.checked
                        ? [...e.statuses, status]
                        : e.statuses.filter((s) => s !== status),
                    })
                  }
                />
                {t(`finance.expenses.statuses.${status}`)}
              </label>
            ))}
          </div>
        </fieldset>
      </>
    );
  }

  function studentFilters() {
    const st = values.students;
    const set = (change: Partial<typeof st>) =>
      update({ students: { ...st, ...change } });
    return (
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.studentStatus} className="text-sm font-medium">
            {t('exports.form.studentStatus')}
          </label>
          <select
            id={ids.studentStatus}
            className={SELECT}
            value={st.status}
            onChange={(event) =>
              set({ status: event.target.value as typeof st.status })
            }
          >
            {STUDENT_STATUS_FILTERS.map((status) => (
              <option key={status} value={status}>
                {t(`exports.form.studentStatuses.${status}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.level} className="text-sm font-medium">
            {t('exports.form.educationLevel')}
          </label>
          <select
            id={ids.level}
            className={SELECT}
            value={st.educationLevel}
            aria-describedby={levels.isError ? `${ids.level}-error` : undefined}
            onChange={(event) => set({ educationLevel: event.target.value })}
          >
            <option value="">{t('exports.form.allLevels')}</option>
            {(levels.data?.items ?? []).map((level) => (
              <option key={level.id} value={level.id}>
                {level.name}
              </option>
            ))}
          </select>
          {levels.isError ? (
            <p id={`${ids.level}-error`} className="text-sm text-destructive">
              {t('exports.form.optionsFailed')}
            </p>
          ) : null}
        </div>
      </div>
    );
  }

  const columnsMessage = errors.columns;

  return (
    <>
      <form
        noValidate
        onSubmit={(event) => void onSubmit(event)}
        className="flex min-w-0 flex-col gap-6"
      >
        <fieldset className="flex min-w-0 flex-col gap-1">
          <legend className="mb-1 text-sm font-medium">
            {t('exports.form.dataset')}
          </legend>
          <div className="flex flex-wrap gap-x-6">
            {datasets.map((dataset) => (
              <label
                key={dataset.code}
                htmlFor={`${ids.dataset}-${dataset.code}`}
                className={OPTION_CLASS}
              >
                <input
                  id={`${ids.dataset}-${dataset.code}`}
                  type="radio"
                  name={ids.dataset}
                  value={dataset.code}
                  className={CHECK_CLASS}
                  checked={values.dataset === dataset.code}
                  onChange={() => chooseDataset(dataset.code)}
                />
                {t(`exports.datasets.${dataset.code}`)}
              </label>
            ))}
          </div>
          {current.personal ? (
            <p id={ids.hint} className="text-sm text-muted-foreground">
              {t('exports.form.personalHint')}
            </p>
          ) : null}
        </fieldset>

        <fieldset className="flex min-w-0 flex-col gap-1">
          <legend className="mb-1 text-sm font-medium">
            {t('exports.form.format')}
          </legend>
          <div className="flex flex-wrap gap-x-6">
            {EXPORT_FORMATS.map((format) => (
              <label
                key={format}
                htmlFor={`${ids.format}-${format}`}
                className={OPTION_CLASS}
              >
                <input
                  id={`${ids.format}-${format}`}
                  type="radio"
                  name={ids.format}
                  value={format}
                  className={CHECK_CLASS}
                  checked={values.format === format}
                  onChange={() => update({ format })}
                />
                {t(`exports.formats.${format}`)}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset
          id={ids.columns}
          className="flex min-w-0 flex-col gap-2"
          aria-describedby={
            columnsMessage
              ? `${ids.columnsHelp} ${errorId('columns')}`
              : ids.columnsHelp
          }
        >
          <legend className="mb-1 text-sm font-medium">
            {t('exports.form.columns')}
          </legend>
          <p id={ids.columnsHelp} className="text-sm text-muted-foreground">
            {t('exports.form.columnsHelp')}
          </p>
          <ol className="flex max-w-xl flex-col divide-y divide-border rounded-md border border-border">
            {values.columns.map((choice, index) => {
              const label = columnOf(choice.code)?.label ?? choice.code;
              const checkboxId = `${id}-column-${choice.code}`;
              return (
                <li
                  key={choice.code}
                  className="flex flex-wrap items-center gap-2 px-3 py-1"
                  data-column={choice.code}
                >
                  <label
                    htmlFor={checkboxId}
                    className={cn(OPTION_CLASS, 'min-w-0 flex-1')}
                  >
                    <input
                      id={checkboxId}
                      type="checkbox"
                      className={CHECK_CLASS}
                      checked={choice.checked}
                      aria-invalid={columnsMessage ? true : undefined}
                      onChange={(event) =>
                        toggleColumn(choice.code, event.target.checked)
                      }
                    />
                    <span className="[overflow-wrap:anywhere]">{label}</span>
                  </label>
                  <span className="flex gap-2">
                    <Button
                      ref={(element) => {
                        if (element) {
                          moveButtons.current.set(`${choice.code}:up`, element);
                        } else {
                          moveButtons.current.delete(`${choice.code}:up`);
                        }
                      }}
                      type="button"
                      variant="outline"
                      size="sm"
                      className="min-h-11 md:min-h-8"
                      disabled={index === 0}
                      aria-label={t('exports.form.moveUpFor', {
                        column: label,
                      })}
                      onClick={() => move(index, 'up')}
                    >
                      {t('exports.form.moveUp')}
                    </Button>
                    <Button
                      ref={(element) => {
                        if (element) {
                          moveButtons.current.set(
                            `${choice.code}:down`,
                            element,
                          );
                        } else {
                          moveButtons.current.delete(`${choice.code}:down`);
                        }
                      }}
                      type="button"
                      variant="outline"
                      size="sm"
                      className="min-h-11 md:min-h-8"
                      disabled={index === values.columns.length - 1}
                      aria-label={t('exports.form.moveDownFor', {
                        column: label,
                      })}
                      onClick={() => move(index, 'down')}
                    >
                      {t('exports.form.moveDown')}
                    </Button>
                  </span>
                </li>
              );
            })}
          </ol>
          {columnsMessage ? (
            <p id={errorId('columns')} className="text-sm text-destructive">
              {columnsMessage}
            </p>
          ) : null}
          <p role="status" aria-live="polite" className="sr-only">
            {moved}
          </p>
        </fieldset>

        <div className="flex max-w-xs flex-col gap-2">
          <label htmlFor={ids.groupBy} className="text-sm font-medium">
            {t('exports.form.groupBy')}
          </label>
          <select
            id={ids.groupBy}
            className={SELECT}
            value={groupBy}
            onChange={(event) => update({ groupBy: event.target.value })}
          >
            <option value="">{t('exports.form.noGrouping')}</option>
            {groups.map((code) => (
              <option key={code} value={code}>
                {columnOf(code)?.label ?? code}
              </option>
            ))}
          </select>
        </div>

        <fieldset className="flex min-w-0 flex-col gap-4 rounded-lg border border-border p-4">
          <legend className="px-1 text-sm font-medium">
            {t('exports.form.filters')}
          </legend>
          {values.dataset === 'class_sessions'
            ? sessionFilters()
            : values.dataset === 'expenses'
              ? expenseFilters()
              : studentFilters()}
        </fieldset>

        {limitMax !== null ? (
          <p role="alert" className="text-sm text-destructive">
            {t('exports.form.limitReached', { max: limitMax })}
          </p>
        ) : null}
        {failure ? <ErrorAlert error={failure.error} /> : null}

        <div className="flex flex-wrap gap-2">
          <Button
            type="submit"
            className="min-h-11 md:min-h-9"
            aria-describedby={current.personal ? ids.hint : undefined}
            disabled={submitting}
          >
            {submitting ? (
              <Spinner label={t('exports.form.submitting')} showLabel />
            ) : (
              t('exports.form.submit')
            )}
          </Button>
          {onSetupSaved ? (
            <Button
              type="button"
              variant="outline"
              className="min-h-11 md:min-h-9"
              disabled={submitting}
              onClick={onSaveSetup}
            >
              {t('exports.setups.save')}
            </Button>
          ) : null}
        </div>
      </form>
      <SaveSetupDialog
        open={saving !== null}
        title={t('exports.setups.saveTitle')}
        description={t('exports.setups.saveDescription')}
        initial={{
          name: loadedSetup?.name ?? '',
          sharedRole: loadedSetup?.shared_role ?? null,
        }}
        submitLabel={t('exports.setups.saveNew')}
        onSubmit={saveAs}
        alternative={
          loadedSetup
            ? {
                label: t('exports.setups.update', { name: loadedSetup.name }),
                onSubmit: saveOver,
              }
            : undefined
        }
        onStale={() => onSetupsStale?.()}
        onRange={(error) => {
          if (!showFieldProblems(error)) {
            setFailure({ error });
          }
        }}
        onClose={() => setSaving(null)}
      />
    </>
  );
}
