import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import type { BusinessSettings, BusinessSettingsUpdate } from './api';
import {
  MESSAGE_LANGUAGES,
  messageLength,
  messagesChanged,
  messageUpdate,
  messageValues,
  RECEIPT_MESSAGE_MAX_LENGTH,
  type MessageLanguage,
  type MessageValues,
} from './receiptMessage';

// The receipt message card (M8-04b C, #121 B2): one textarea per receipt
// language, each with a live character count out of 500. Save stays
// disabled until a message differs from the stored one, then sends
// `version` and only the changed messages (an emptied one as null). It never
// sends the time zone, so it never asks for the password. A 422 lands on its
// field; version_conflict reloads the settings and says so, or keeps the
// shown text with a stale alert when the reload fails (E3). "Saved." is a
// polite status. No API call of its own: the page passes `onSave` and
// `onReload`; new `settings` (after a save or a reload) reset the fields.

export type ReceiptMessageState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; settings: BusinessSettings };

interface ReceiptMessageCardProps {
  state: ReceiptMessageState;
  /** Sends the body; rejects with the error to show. */
  onSave: (body: BusinessSettingsUpdate) => Promise<void>;
  /** Refetches the settings (after a conflict); rejects when that fails. */
  onReload: () => Promise<void>;
}

type FieldMessageKey =
  'settings.receiptMessage.tooLong' | 'settings.receiptMessage.invalid';

type Notice =
  | { kind: 'error'; error: unknown }
  | { kind: 'reloaded' }
  | { kind: 'stale'; error: unknown };

const FIELD_OF: Record<string, MessageLanguage> = {
  receipt_message_es: 'es',
  receipt_message_en: 'en',
};

export function ReceiptMessageCard({
  state,
  onSave,
  onReload,
}: ReceiptMessageCardProps) {
  const { t } = useTranslation();
  const id = useId();
  const settings = state.status === 'ready' ? state.settings : null;
  const [values, setValues] = useState<MessageValues>(() =>
    settings ? messageValues(settings) : { es: '', en: '' },
  );
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<MessageLanguage, FieldMessageKey>>
  >({});
  const [notice, setNotice] = useState<Notice | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [reloading, setReloading] = useState(false);
  const inFlight = useRef(false);
  const fields = useRef<Partial<Record<MessageLanguage, HTMLTextAreaElement>>>(
    {},
  );

  // New settings (a save, a reload) reset the fields: the render-time
  // "previous value" pattern, not an effect.
  const [shown, setShown] = useState(settings);
  if (settings !== shown) {
    setShown(settings);
    if (settings) {
      setValues(messageValues(settings));
    }
  }

  const titleId = `${id}-title`;
  const helpId = `${id}-help`;
  const fieldId = (language: MessageLanguage) => `${id}-${language}`;

  async function reload() {
    setReloading(true);
    try {
      await onReload();
      setNotice({ kind: 'reloaded' });
    } catch (error) {
      setNotice({ kind: 'stale', error });
    } finally {
      setReloading(false);
    }
  }

  async function submit(current: BusinessSettings) {
    setNotice(null);
    setSaved(false);
    const tooLong = MESSAGE_LANGUAGES.filter(
      (language) =>
        messageLength(values[language]) > RECEIPT_MESSAGE_MAX_LENGTH,
    );
    if (tooLong.length > 0) {
      setFieldErrors(
        Object.fromEntries(
          tooLong.map((language) => [
            language,
            'settings.receiptMessage.tooLong',
          ]),
        ),
      );
      fields.current[tooLong[0]!]?.focus();
      return;
    }
    setFieldErrors({});
    setSaving(true);
    try {
      await onSave(messageUpdate(current, values));
      setSaved(true);
    } catch (error) {
      if (error instanceof ApiError && error.code === 'version_conflict') {
        await reload();
        return;
      }
      if (error instanceof ApiError && error.fieldErrors.length > 0) {
        const next: Partial<Record<MessageLanguage, FieldMessageKey>> = {};
        for (const fieldError of error.fieldErrors) {
          const language = FIELD_OF[String(fieldError.path[1])];
          if (language) {
            next[language] =
              fieldError.code === 'string_too_long'
                ? 'settings.receiptMessage.tooLong'
                : 'settings.receiptMessage.invalid';
          }
        }
        const first = MESSAGE_LANGUAGES.find((language) => next[language]);
        if (first) {
          setFieldErrors(next);
          fields.current[first]?.focus();
          return;
        }
      }
      setNotice({ kind: 'error', error });
    } finally {
      setSaving(false);
    }
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!settings || inFlight.current) {
      return;
    }
    inFlight.current = true;
    void submit(settings).finally(() => {
      inFlight.current = false;
    });
  }

  let body;
  if (state.status === 'loading') {
    body = (
      <LoadingState rows={4} label={t('settings.receiptMessage.loading')} />
    );
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const changed = messagesChanged(state.settings, values);
    body = (
      <form
        method="post"
        noValidate
        aria-labelledby={titleId}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {MESSAGE_LANGUAGES.map((language) => {
            const length = messageLength(values[language]);
            const errorKey = fieldErrors[language];
            const errorId = `${fieldId(language)}-error`;
            const counterId = `${fieldId(language)}-counter`;
            return (
              <div key={language} className="flex min-w-0 flex-col gap-2">
                <label
                  htmlFor={fieldId(language)}
                  className="text-sm font-medium"
                >
                  {t(
                    language === 'es'
                      ? 'settings.receiptMessage.es'
                      : 'settings.receiptMessage.en',
                  )}
                </label>
                <textarea
                  id={fieldId(language)}
                  ref={(element) => {
                    if (element) {
                      fields.current[language] = element;
                    }
                  }}
                  rows={5}
                  className={TEXTAREA_CLASS}
                  value={values[language]}
                  aria-invalid={errorKey ? true : undefined}
                  aria-describedby={[
                    helpId,
                    errorKey ? errorId : null,
                    counterId,
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  onChange={(event) => {
                    const value = event.target.value;
                    setValues((previous) => ({
                      ...previous,
                      [language]: value,
                    }));
                    setSaved(false);
                  }}
                />
                <FieldError
                  id={errorId}
                  message={
                    errorKey
                      ? t(errorKey, { max: RECEIPT_MESSAGE_MAX_LENGTH })
                      : undefined
                  }
                />
                <p
                  id={counterId}
                  className={
                    length > RECEIPT_MESSAGE_MAX_LENGTH
                      ? 'text-sm text-destructive'
                      : 'text-sm text-muted-foreground'
                  }
                >
                  {t('settings.receiptMessage.counter', {
                    length,
                    max: RECEIPT_MESSAGE_MAX_LENGTH,
                  })}
                </p>
              </div>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={!changed || saving}>
            {saving ? (
              <Spinner label={t('settings.saving')} showLabel />
            ) : (
              t('settings.receiptMessage.save')
            )}
          </Button>
        </div>
      </form>
    );
  }

  return (
    <section
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-4 rounded-lg border border-border p-4"
    >
      <div className="flex flex-col gap-1">
        <h2 id={titleId} className="text-lg font-semibold">
          {t('settings.receiptMessage.title')}
        </h2>
        <p id={helpId} className="text-sm text-muted-foreground">
          {t('settings.receiptMessage.help')}
        </p>
      </div>
      <p role="status" className="text-sm">
        {saved
          ? t('settings.saved')
          : notice?.kind === 'reloaded'
            ? t('settings.receiptMessage.reloaded')
            : ''}
      </p>
      {notice?.kind === 'error' ? <ErrorAlert error={notice.error} /> : null}
      {notice?.kind === 'stale' ? (
        <ErrorAlert
          error={notice.error}
          message={t('settings.receiptMessage.stale')}
          onRetry={reloading ? undefined : () => void reload()}
          retryLabel={t('settings.reload')}
        />
      ) : null}
      {body}
    </section>
  );
}
