import { useEffect, useId, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import { paragraphsOf, type PrivacyNotice } from './api';

export type PrivacyNoticeState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; notice: PrivacyNotice };

interface PrivacyNoticeViewProps {
  state: PrivacyNoticeState;
  /** The acceptance request is in flight. */
  accepting: boolean;
  /** The last acceptance failed with 409: a newer version is shown now. */
  changed: boolean;
  /** Any other acceptance error. */
  acceptError: unknown;
  onAccept: (version: number) => void;
}

// The privacy notice (#174 G2): the h1 (focused on arrival), the version and
// its publication date, the text of the UI language as plain paragraphs in a
// keyboard-reachable scroll region, then "I accept" (or "Accepted on …" when
// the user already accepted this version). Switching the language shows the
// other text from the same response.
export function PrivacyNoticeView({
  state,
  accepting,
  changed,
  acceptError,
  onAccept,
}: PrivacyNoticeViewProps) {
  const { t } = useTranslation();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const headingId = useId();

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  let content: ReactNode;
  if (state.status === 'loading') {
    content = <LoadingState rows={6} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    content = (
      <NoticeContent
        notice={state.notice}
        accepting={accepting}
        changed={changed}
        acceptError={acceptError}
        onAccept={onAccept}
      />
    );
  }

  // One h1 element in every state, so the focus set on arrival stays on it.
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <h1
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className="text-2xl font-semibold outline-none"
      >
        {t('privacyNotice.title')}
      </h1>
      {content}
    </div>
  );
}

function NoticeContent({
  notice,
  accepting,
  changed,
  acceptError,
  onAccept,
}: Omit<PrivacyNoticeViewProps, 'state'> & { notice: PrivacyNotice }) {
  const { t, i18n } = useTranslation();
  const { formatInstant } = useFormatters();
  const language = toLanguage(i18n.resolvedLanguage);
  const text = language === 'en' ? notice.body_en : notice.body_es;
  const accepted = notice.accepted_at !== null;

  return (
    <>
      <p className="-mt-4 text-sm text-muted-foreground">
        {t('privacyNotice.version', {
          version: notice.version,
          date: formatInstant(notice.published_at, 'date'),
        })}
      </p>
      <div className="flex flex-col gap-2">
        {accepted ? null : <p>{t('privacyNotice.intro')}</p>}
        {/* Always rendered, so the message is announced when it appears. */}
        <div role="status" aria-live="polite">
          {changed ? (
            <p className="font-medium">{t('privacyNotice.changed')}</p>
          ) : null}
        </div>
      </div>
      <div
        role="region"
        aria-label={t('privacyNotice.textLabel')}
        tabIndex={0}
        className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto rounded-md border border-border p-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        {paragraphsOf(text).map((paragraph, index) => (
          <p key={index} className="whitespace-pre-line">
            {paragraph}
          </p>
        ))}
      </div>
      {acceptError ? <ErrorAlert error={acceptError} /> : null}
      {accepted ? (
        <p className="font-medium">
          {t('privacyNotice.accepted', {
            date: formatInstant(notice.accepted_at!, 'dateTime'),
          })}
        </p>
      ) : (
        <div>
          <Button
            type="button"
            disabled={accepting}
            onClick={() => onAccept(notice.version)}
          >
            {accepting ? (
              <Spinner label={t('privacyNotice.accepting')} showLabel />
            ) : (
              t('privacyNotice.accept')
            )}
          </Button>
        </div>
      )}
    </>
  );
}
