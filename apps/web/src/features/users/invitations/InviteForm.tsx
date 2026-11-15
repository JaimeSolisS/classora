import { zodResolver } from '@hookform/resolvers/zod';
import { useId, useRef, useState, type FormEvent } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import type { RoleCode } from '@/features/auth/session';
import { z } from '@/lib/forms';
import { roleNameKey, STAFF_ROLES } from '../roles';
import type { InvitationInput } from './api';

// The invite form (#29 I2), without any API call: the page passes
// `onSubmit`, stories pass a stub. It offers only `roles` (the ones the user
// may assign; UX only, the API decides). A staff role shows a note that the
// password will be asked for; the dialog itself comes from apiCall (#28 D).

/** The #29 bounds of InvitationCreate. */
export const USERNAME_MAX_LENGTH = 256;
export const DISPLAY_NAME_MAX_LENGTH = 200;

const inviteSchema = z.object({
  username: z
    .string()
    .trim()
    .min(1, 'invitations.form.usernameRequired')
    .max(USERNAME_MAX_LENGTH, 'invitations.form.tooLong'),
  displayName: z
    .string()
    .trim()
    .min(1, 'invitations.form.displayNameRequired')
    .max(DISPLAY_NAME_MAX_LENGTH, 'invitations.form.tooLong'),
  role: z.string().min(1, 'invitations.form.roleRequired'),
});

type InviteValues = z.infer<typeof inviteSchema>;

type InviteMessageKey =
  | 'invitations.form.usernameRequired'
  | 'invitations.form.displayNameRequired'
  | 'invitations.form.roleRequired'
  | 'invitations.form.tooLong';

/** Server codes shown under the username field instead of the alert. */
const USERNAME_CODES = new Set(['username_invalid', 'username_taken']);
type UsernameCode = 'username_invalid' | 'username_taken';

interface InviteFormProps {
  /** The roles the user may assign (at least one). */
  roles: readonly RoleCode[];
  /** Sends the invitation. Rejects with the error to show (an ApiError). */
  onSubmit: (input: InvitationInput) => Promise<void>;
}

export function InviteForm({ roles, onSubmit }: InviteFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const ids = {
    username: `${id}-username`,
    displayName: `${id}-display-name`,
    role: `${id}-role`,
  };
  const [error, setError] = useState<unknown>(null);
  const [usernameCode, setUsernameCode] = useState<UsernameCode | null>(null);
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    reset,
    setFocus,
    control,
    formState: { errors, isSubmitting },
  } = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { username: '', displayName: '', role: '' },
    shouldFocusError: true,
  });
  const role = useWatch({ control, name: 'role' });
  const isStaffRole = STAFF_ROLES.has(role as RoleCode);

  const submit = handleSubmit(async (values) => {
    setError(null);
    setUsernameCode(null);
    try {
      await onSubmit({
        username: values.username,
        display_name: values.displayName,
        role: values.role as RoleCode,
      });
      reset();
    } catch (caught) {
      if (caught instanceof ApiError && USERNAME_CODES.has(caught.code)) {
        setUsernameCode(caught.code as UsernameCode);
        setFocus('username');
        return;
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

  const message = (key: string | undefined) =>
    key ? t(key as InviteMessageKey) : undefined;
  const usernameMessage = usernameCode
    ? tErrors(usernameCode)
    : message(errors.username?.message);
  const displayNameMessage = message(errors.displayName?.message);
  const roleMessage = message(errors.role?.message);
  const hintId = `${ids.username}-hint`;
  const errorId = (field: string) => `${field}-error`;
  const describedBy = (...parts: (string | false)[]) =>
    parts.filter(Boolean).join(' ') || undefined;

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex w-full max-w-md flex-col gap-4"
    >
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('invitations.form.title')}
      </h2>
      {error ? <ErrorAlert error={error} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.username} className="text-sm font-medium">
            {t('invitations.form.username')}
          </label>
          <Input
            id={ids.username}
            type="text"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            aria-invalid={usernameMessage ? true : undefined}
            aria-describedby={describedBy(
              hintId,
              Boolean(usernameMessage) && errorId(ids.username),
            )}
            {...register('username', {
              onChange: () => setUsernameCode(null),
            })}
          />
          <p id={hintId} className="text-sm text-muted-foreground">
            {t('common.usernameHint')}
          </p>
          <FieldError id={errorId(ids.username)} message={usernameMessage} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.displayName} className="text-sm font-medium">
            {t('invitations.form.displayName')}
          </label>
          <Input
            id={ids.displayName}
            type="text"
            autoComplete="off"
            aria-invalid={displayNameMessage ? true : undefined}
            aria-describedby={
              displayNameMessage ? errorId(ids.displayName) : undefined
            }
            {...register('displayName')}
          />
          <FieldError
            id={errorId(ids.displayName)}
            message={displayNameMessage}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.role} className="text-sm font-medium">
            {t('invitations.form.role')}
          </label>
          <select
            id={ids.role}
            className="h-9 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-destructive md:text-sm"
            aria-invalid={roleMessage ? true : undefined}
            aria-describedby={describedBy(
              Boolean(roleMessage) && errorId(ids.role),
              isStaffRole && `${ids.role}-note`,
            )}
            {...register('role')}
          >
            <option value="">{t('invitations.form.chooseRole')}</option>
            {roles.map((code) => (
              <option key={code} value={code}>
                {t(roleNameKey(code))}
              </option>
            ))}
          </select>
          <FieldError id={errorId(ids.role)} message={roleMessage} />
          {isStaffRole ? (
            <p id={`${ids.role}-note`} className="text-sm">
              {t('invitations.form.staffNote')}
            </p>
          ) : null}
        </div>
        <Button type="submit" disabled={isSubmitting} className="self-start">
          {isSubmitting ? (
            <Spinner label={t('invitations.form.submitting')} showLabel />
          ) : (
            t('invitations.form.submit')
          )}
        </Button>
      </form>
    </section>
  );
}
