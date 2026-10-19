import { zodResolver } from '@hookform/resolvers/zod';
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { useFieldArray, useForm, type FieldPath } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import type { EducationLevelReference } from './api';
import {
  CONTACT_NAME_MAX_LENGTH,
  EMAIL_MAX_LENGTH,
  EMPTY_CONTACT,
  GRADE_MAX_LENGTH,
  MAX_CONTACTS,
  NAME_MAX_LENGTH,
  PHONE_MAX_LENGTH,
  RELATIONSHIP_MAX_LENGTH,
  SCHOOL_MAX_LENGTH,
  EARLIEST_BIRTHDATE,
  studentFormSchema,
  type StudentFormValues,
  type StudentMessageKey,
} from './rules';
import { SELECT_CLASS } from './styles';

// The student form (#33 J1, J2, K3), shared by /students/new and
// /students/:id/edit. React Hook Form + Zod mirror StudentCreate/Update.
// Emergency contacts are a fieldset of 1 to 5 rows (useFieldArray): "Add
// contact" is disabled at 5 and "Remove" with one row left (each with a help
// text); "Move up"/"Move down" reorder rows with the keyboard. Focus goes to
// the new row's first field after "Add contact", to a neighbour after
// "Remove", and stays on the moved row's button after a move. Server field
// errors land on their fields (education_level_unavailable on the level,
// emergency_contact_unknown on that contact's name); version_conflict shows
// "Reload" (the page then remounts the form with the new version, keyed by
// it). Without any API call: the page passes `onSubmit` (which rejects
// with the error to show) and `onReload`.

type MessageKey =
  | StudentMessageKey
  | 'errors:education_level_unavailable'
  | 'errors:emergency_contact_unknown';

type FieldName = FieldPath<StudentFormValues>;

const SERVER_FIELDS: Record<string, [FieldName, MessageKey]> = {
  given_names: ['givenNames', 'students.validation.nameRequired'],
  family_names: ['familyNames', 'students.validation.nameRequired'],
  birthdate: ['birthdate', 'students.validation.birthdateRange'],
  email: ['email', 'students.validation.emailInvalid'],
  phone: ['phone', 'students.validation.phoneInvalid'],
  school_name: ['school', 'students.validation.schoolTooLong'],
  school_grade: ['grade', 'students.validation.gradeTooLong'],
  education_level_id: ['levelId', 'errors:education_level_unavailable'],
};

const SERVER_CONTACT_FIELDS: Record<
  string,
  ['name' | 'relationship' | 'phone', MessageKey]
> = {
  name: ['name', 'students.validation.nameRequired'],
  relationship: ['relationship', 'students.validation.nameRequired'],
  phone: ['phone', 'students.validation.phoneInvalid'],
  id: ['name', 'errors:emergency_contact_unknown'],
};

/** Where a server field error goes, or null when it has no field here. */
function serverField(
  path: readonly (string | number)[],
  code: string,
): [FieldName, MessageKey] | null {
  const [, field, index, sub] = path;
  if (field === 'emergency_contacts' && typeof index === 'number') {
    const mapped = SERVER_CONTACT_FIELDS[String(sub ?? 'id')];
    if (!mapped) {
      return null;
    }
    const message: MessageKey =
      code === 'emergency_contact_unknown'
        ? 'errors:emergency_contact_unknown'
        : mapped[1];
    return [`contacts.${index}.${mapped[0]}`, message];
  }
  return SERVER_FIELDS[String(field)] ?? null;
}

interface StudentFormProps {
  /** The values to start from (and to return to after a reload). */
  defaultValues: StudentFormValues;
  /** The active levels offered. */
  levels: readonly EducationLevelReference[];
  /** The student's current level when it is inactive (kept as an option). */
  currentLevel?: EducationLevelReference | null;
  /** Today in the center's time zone (`YYYY-MM-DD`): the birthdate's max. */
  today: string;
  /** Sends the form; rejects with the error to show. */
  onSubmit: (values: StudentFormValues) => Promise<void>;
  /** Refetches the student after a version conflict (edit only). */
  onReload?: () => Promise<void>;
  /** Where "Cancel" goes. */
  cancelTo: string;
  /** The id of the page heading that names the form. */
  labelledBy: string;
}

export function StudentForm({
  defaultValues,
  levels,
  currentLevel,
  today,
  onSubmit,
  onReload,
  cancelTo,
  labelledBy,
}: StudentFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const [error, setError] = useState<unknown>(null);
  const inFlight = useRef(false);
  const schema = useMemo(() => studentFormSchema(today), [today]);
  const {
    register,
    control,
    handleSubmit,
    setError: setFieldError,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<StudentFormValues>({
    resolver: zodResolver(schema),
    defaultValues,
    shouldFocusError: true,
  });
  const { fields, append, remove, move } = useFieldArray({
    control,
    name: 'contacts',
    keyName: 'fieldKey',
  });

  // Focus after a row action, once the rows have re-rendered.
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const pendingFocus = useRef<
    { kind: 'field'; name: FieldName } | { kind: 'button'; key: string } | null
  >(null);
  useEffect(() => {
    const target = pendingFocus.current;
    pendingFocus.current = null;
    if (target?.kind === 'field') {
      setFocus(target.name);
    } else if (target?.kind === 'button') {
      buttons.current.get(target.key)?.focus();
    }
  }, [fields, setFocus]);

  function addContact() {
    append(
      { ...EMPTY_CONTACT },
      { shouldFocus: true, focusName: `contacts.${fields.length}.name` },
    );
  }

  function removeContact(index: number) {
    const neighbour = index < fields.length - 1 ? index : index - 1;
    remove(index);
    pendingFocus.current = {
      kind: 'field',
      name: `contacts.${neighbour}.name`,
    };
  }

  function moveContact(index: number, direction: -1 | 1) {
    const to = index + direction;
    const key = fields[index]!.fieldKey;
    move(index, to);
    // Stay on the same button unless it becomes disabled at the edge.
    let action: 'up' | 'down' = direction === -1 ? 'up' : 'down';
    if (to === 0) {
      action = 'down';
    } else if (to === fields.length - 1) {
      action = 'up';
    }
    pendingFocus.current = { kind: 'button', key: `${key}:${action}` };
  }

  const submit = handleSubmit(async (values) => {
    setError(null);
    try {
      await onSubmit(values);
    } catch (caught) {
      if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        let first: FieldName | null = null;
        for (const fieldError of caught.fieldErrors) {
          const mapped = serverField(fieldError.path, fieldError.code);
          if (mapped) {
            setFieldError(mapped[0], { message: mapped[1] });
            first ??= mapped[0];
          }
        }
        if (first) {
          setFocus(first);
          return;
        }
      }
      setError(caught);
    }
  });

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    void submit(event).finally(() => {
      inFlight.current = false;
    });
  }

  async function reload() {
    setError(null);
    await onReload?.();
  }

  const message = (key: string | undefined) => {
    if (!key) {
      return undefined;
    }
    return key.startsWith('errors:')
      ? tErrors(key.slice('errors:'.length) as 'education_level_unavailable')
      : t(key as StudentMessageKey);
  };
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const offerInactive =
    currentLevel && !currentLevel.is_active
      ? levels.every((level) => level.id !== currentLevel.id)
      : false;

  /** A labelled text field with its error. */
  function textField(
    name: FieldName,
    label: string,
    options: {
      type?: string;
      maxLength?: number;
      max?: string;
      min?: string;
      describedBy?: string;
      fieldMessage?: string;
    } = {},
  ) {
    const fieldId = `${id}-${name.replaceAll('.', '-')}`;
    const errorId = `${fieldId}-error`;
    const described = [
      options.describedBy,
      options.fieldMessage ? errorId : undefined,
    ]
      .filter(Boolean)
      .join(' ');
    return (
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId} className="text-sm font-medium">
          {label}
        </label>
        <Input
          id={fieldId}
          type={options.type ?? 'text'}
          autoComplete="off"
          maxLength={options.maxLength}
          max={options.max}
          min={options.min}
          aria-invalid={options.fieldMessage ? true : undefined}
          aria-describedby={described || undefined}
          {...register(name)}
        />
        <FieldError id={errorId} message={options.fieldMessage} />
      </div>
    );
  }

  const levelId = `${id}-level`;
  const levelErrorId = `${levelId}-error`;
  const levelMessage = message(errors.levelId?.message);
  const contactsHelpId = `${id}-contacts-help`;
  const lastOneId = `${id}-contacts-last`;
  const maxId = `${id}-contacts-max`;

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={isConflict && onReload ? () => void reload() : undefined}
          retryLabel={isConflict ? t('students.conflict.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-6"
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {textField('givenNames', t('students.fields.givenNames'), {
            maxLength: NAME_MAX_LENGTH,
            fieldMessage: message(errors.givenNames?.message),
          })}
          {textField('familyNames', t('students.fields.familyNames'), {
            maxLength: NAME_MAX_LENGTH,
            fieldMessage: message(errors.familyNames?.message),
          })}
          {textField('birthdate', t('students.fields.birthdate'), {
            type: 'date',
            min: EARLIEST_BIRTHDATE,
            max: today,
            fieldMessage: message(errors.birthdate?.message),
          })}
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {textField('email', t('students.fields.email'), {
            type: 'email',
            maxLength: EMAIL_MAX_LENGTH,
            fieldMessage: message(errors.email?.message),
          })}
          {textField('phone', t('students.fields.phone'), {
            type: 'tel',
            maxLength: PHONE_MAX_LENGTH,
            fieldMessage: message(errors.phone?.message),
          })}
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {textField('school', t('students.fields.school'), {
            maxLength: SCHOOL_MAX_LENGTH,
            fieldMessage: message(errors.school?.message),
          })}
          {textField('grade', t('students.fields.grade'), {
            maxLength: GRADE_MAX_LENGTH,
            fieldMessage: message(errors.grade?.message),
          })}
          <div className="flex flex-col gap-2">
            <label htmlFor={levelId} className="text-sm font-medium">
              {t('students.fields.level')}
            </label>
            <select
              id={levelId}
              className={SELECT_CLASS}
              aria-invalid={levelMessage ? true : undefined}
              aria-describedby={levelMessage ? levelErrorId : undefined}
              {...register('levelId')}
            >
              <option value="">{t('students.fields.levelNone')}</option>
              {levels.map((level) => (
                <option key={level.id} value={level.id}>
                  {level.name}
                </option>
              ))}
              {offerInactive && currentLevel ? (
                <option value={currentLevel.id}>
                  {`${currentLevel.name} ${t('students.fields.levelInactive')}`}
                </option>
              ) : null}
            </select>
            <FieldError id={levelErrorId} message={levelMessage} />
          </div>
        </div>
        <fieldset
          aria-describedby={contactsHelpId}
          className="flex flex-col gap-4 rounded-md border border-border p-4"
        >
          <legend className="px-1 text-lg font-semibold">
            {t('students.contacts.title')}
          </legend>
          <p id={contactsHelpId} className="text-sm text-muted-foreground">
            {t('students.contacts.help')}
          </p>
          <ol className="flex flex-col gap-4">
            {fields.map((field, index) => {
              const n = index + 1;
              const rowErrors = errors.contacts?.[index];
              return (
                <li key={field.fieldKey}>
                  <fieldset className="flex flex-col gap-3 rounded-md border border-border p-3">
                    <legend className="px-1 text-sm font-semibold">
                      {t('students.contacts.legend', { n })}
                    </legend>
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                      {textField(
                        `contacts.${index}.name`,
                        t('students.contacts.nameLabel', { n }),
                        {
                          maxLength: CONTACT_NAME_MAX_LENGTH,
                          fieldMessage: message(rowErrors?.name?.message),
                        },
                      )}
                      {textField(
                        `contacts.${index}.relationship`,
                        t('students.contacts.relationshipLabel', { n }),
                        {
                          maxLength: RELATIONSHIP_MAX_LENGTH,
                          fieldMessage: message(
                            rowErrors?.relationship?.message,
                          ),
                        },
                      )}
                      {textField(
                        `contacts.${index}.phone`,
                        t('students.contacts.phoneLabel', { n }),
                        {
                          type: 'tel',
                          maxLength: PHONE_MAX_LENGTH,
                          fieldMessage: message(rowErrors?.phone?.message),
                        },
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('students.contacts.moveUpNamed', { n })}
                        disabled={index === 0}
                        ref={(element) => {
                          const key = `${field.fieldKey}:up`;
                          if (element) buttons.current.set(key, element);
                          else buttons.current.delete(key);
                        }}
                        onClick={() => moveContact(index, -1)}
                      >
                        {t('students.contacts.moveUp')}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('students.contacts.moveDownNamed', {
                          n,
                        })}
                        disabled={index === fields.length - 1}
                        ref={(element) => {
                          const key = `${field.fieldKey}:down`;
                          if (element) buttons.current.set(key, element);
                          else buttons.current.delete(key);
                        }}
                        onClick={() => moveContact(index, 1)}
                      >
                        {t('students.contacts.moveDown')}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('students.contacts.removeNamed', { n })}
                        aria-describedby={
                          fields.length === 1 ? lastOneId : undefined
                        }
                        disabled={fields.length === 1}
                        onClick={() => removeContact(index)}
                      >
                        {t('students.contacts.remove')}
                      </Button>
                    </div>
                  </fieldset>
                </li>
              );
            })}
          </ol>
          {fields.length === 1 ? (
            <p id={lastOneId} className="text-sm text-muted-foreground">
              {t('students.contacts.lastOne')}
            </p>
          ) : null}
          <div className="flex flex-col items-start gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={fields.length >= MAX_CONTACTS}
              aria-describedby={
                fields.length >= MAX_CONTACTS ? maxId : undefined
              }
              onClick={addContact}
            >
              {t('students.contacts.add')}
            </Button>
            {fields.length >= MAX_CONTACTS ? (
              <p id={maxId} className="text-sm text-muted-foreground">
                {t('students.contacts.max')}
              </p>
            ) : null}
          </div>
        </fieldset>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('students.saving')} showLabel />
            ) : (
              t('students.save')
            )}
          </Button>
          <Link
            to={cancelTo}
            className="inline-flex min-h-6 items-center px-1 text-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('students.cancel')}
          </Link>
        </div>
      </form>
    </div>
  );
}
