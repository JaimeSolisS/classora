import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import type { PaymentMethod } from '@/features/billing/accounts/api';
import { CatalogStatusBadge } from '@/features/catalog/CatalogStatusBadge';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';

// The payment methods card (M8-04b B, #121 A): every method with its status
// and one button, "Deactivate" or "Activate" (accessible name "Deactivate
// Cash", E2). Deactivating asks first in a confirmation dialog; the request
// itself may open the #28 password dialog through apiCall (the caller's
// `onChange`). While a request runs every button refuses clicks and the
// pressed one is marked busy (aria-disabled, so focus stays on it). The last
// active method's button is disabled, with the reason as its description.
// payment_method_last_active shows under its row; version_conflict reloads
// the list and says so, or keeps it with a stale alert when the reload
// fails (E3); any other error uses the shared ErrorAlert. No API call of
// its own: the page passes `onChange` and `onReload`.

const KNOWN_METHODS = new Set(['cash', 'transfer', 'other']);

export type PaymentMethodsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; methods: PaymentMethod[] };

interface PaymentMethodsCardProps {
  state: PaymentMethodsState;
  /** Sends the change and refreshes the list; rejects with the error. */
  onChange: (method: PaymentMethod, isActive: boolean) => Promise<void>;
  /** Refetches the list (after a conflict); rejects when that fails. */
  onReload: () => Promise<void>;
}

type Notice =
  | { kind: 'error'; error: unknown }
  | { kind: 'lastActive'; code: string }
  | { kind: 'reloaded' }
  | { kind: 'stale'; error: unknown };

export function PaymentMethodsCard({
  state,
  onChange,
  onReload,
}: PaymentMethodsCardProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const [confirming, setConfirming] = useState<PaymentMethod | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [reloading, setReloading] = useState(false);

  const label = (code: string) =>
    KNOWN_METHODS.has(code)
      ? t(`billing.paymentMethods.${code as 'cash'}`)
      : code;

  async function reload(): Promise<void> {
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

  async function change(method: PaymentMethod, isActive: boolean) {
    if (busy !== null) {
      return;
    }
    setBusy(method.code);
    setNotice(null);
    setAnnouncement('');
    try {
      await onChange(method, isActive);
      setAnnouncement(
        t(
          isActive
            ? 'settings.payments.methods.activated'
            : 'settings.payments.methods.deactivated',
          { method: label(method.code) },
        ),
      );
    } catch (error) {
      if (error instanceof ApiError && error.code === 'version_conflict') {
        await reload();
      } else if (
        error instanceof ApiError &&
        error.code === 'payment_method_last_active'
      ) {
        setNotice({ kind: 'lastActive', code: method.code });
        // The list was out of date: another method went inactive meanwhile.
        void onReload().catch(() => {});
      } else {
        setNotice({ kind: 'error', error });
      }
    } finally {
      setBusy(null);
    }
  }

  const titleId = `${id}-title`;
  const lastActiveId = `${id}-last-active`;

  let body;
  if (state.status === 'loading') {
    body = (
      <LoadingState
        layout="table"
        rows={3}
        label={t('settings.payments.methods.loading')}
      />
    );
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const activeCount = state.methods.filter((m) => m.is_active).length;
    body = (
      <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
        {state.methods.map((method) => {
          const name = label(method.code);
          const isLast = method.is_active && activeCount <= 1;
          const isBusy = busy === method.code;
          const refused = busy !== null;
          return (
            <li
              key={method.code}
              className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{name}</span>
                <CatalogStatusBadge active={method.is_active} />
              </div>
              <div className="flex flex-col gap-1 sm:items-end">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={isLast}
                  aria-disabled={refused && !isLast ? true : undefined}
                  aria-busy={isBusy ? true : undefined}
                  aria-label={t(
                    method.is_active
                      ? 'settings.payments.methods.deactivateMethod'
                      : 'settings.payments.methods.activateMethod',
                    { method: name },
                  )}
                  aria-describedby={isLast ? lastActiveId : undefined}
                  className="self-start aria-disabled:opacity-50 sm:self-auto"
                  onClick={() => {
                    if (refused) {
                      return;
                    }
                    if (method.is_active) {
                      setConfirming(method);
                    } else {
                      void change(method, true);
                    }
                  }}
                >
                  {isBusy ? <Spinner /> : null}
                  {t(
                    method.is_active
                      ? 'settings.payments.methods.deactivate'
                      : 'settings.payments.methods.activate',
                  )}
                </Button>
                {isLast ? (
                  <p
                    id={lastActiveId}
                    className="text-sm text-muted-foreground"
                  >
                    {t('settings.payments.methods.lastActive')}
                  </p>
                ) : null}
                {notice?.kind === 'lastActive' &&
                notice.code === method.code ? (
                  <p role="alert" className="text-sm text-destructive">
                    {tErrors('payment_method_last_active')}
                  </p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <section
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-4 rounded-lg border border-border p-4"
    >
      <div className="flex flex-col gap-1">
        <h2 id={titleId} className="text-lg font-semibold">
          {t('settings.payments.methods.title')}
        </h2>
        <p className="text-sm text-muted-foreground">
          {t('settings.payments.methods.help')}
        </p>
      </div>
      <p role="status" className="text-sm">
        {announcement ||
          (notice?.kind === 'reloaded'
            ? t('settings.payments.methods.reloaded')
            : '')}
      </p>
      {notice?.kind === 'error' ? <ErrorAlert error={notice.error} /> : null}
      {notice?.kind === 'stale' ? (
        <ErrorAlert
          error={notice.error}
          message={t('settings.payments.methods.stale')}
          onRetry={reloading ? undefined : () => void reload()}
          retryLabel={t('settings.reload')}
        />
      ) : null}
      {body}
      <ConfirmDialog
        open={confirming !== null}
        title={t('settings.payments.methods.confirm.title', {
          method: confirming ? label(confirming.code) : '',
        })}
        body={t('settings.payments.methods.confirm.body')}
        confirmLabel={t('settings.payments.methods.deactivate')}
        destructive
        onConfirm={() => {
          const method = confirming;
          setConfirming(null);
          if (method) {
            void change(method, false);
          }
          return Promise.resolve();
        }}
        onCancel={() => setConfirming(null)}
      />
    </section>
  );
}
