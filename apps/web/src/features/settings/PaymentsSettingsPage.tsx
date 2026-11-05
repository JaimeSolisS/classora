import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ComponentProps } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import {
  fetchPaymentMethods,
  PAYMENT_METHODS_KEY,
} from '@/features/billing/accounts/api';
import {
  fetchSettings,
  SETTINGS_QUERY_KEY,
  updatePaymentMethod,
  updateSettings,
} from './api';
import { PaymentMethodsCard } from './PaymentMethodsCard';
import { ReceiptMessageCard } from './ReceiptMessageCard';
import { SettingsTabs } from './SettingsTabs';

// /settings/payments (M8-04b A1, E1), for settings.manage holders: the
// "Payments and receipts" tab with two cards, the payment methods and the
// receipt message. A method change invalidates PAYMENT_METHODS_KEY, the
// list the payment, refund, withdrawal, payroll-payment and expense-payment
// dialogs read, so they stop (or start again) offering the method. A saved
// message replaces the shared SETTINGS_QUERY_KEY entry, so the General tab
// sends the current version.

interface PaymentsSettingsViewProps {
  methods: ComponentProps<typeof PaymentMethodsCard>;
  message: ComponentProps<typeof ReceiptMessageCard>;
}

/** The page without its queries (stories render each state). */
export function PaymentsSettingsView({
  methods,
  message,
}: PaymentsSettingsViewProps) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-6">
      <SettingsTabs />
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('settings.payments.title')}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t('settings.payments.help')}
        </p>
      </div>
      <div className="flex w-full max-w-3xl flex-col gap-6">
        <PaymentMethodsCard {...methods} />
        <ReceiptMessageCard {...message} />
      </div>
    </div>
  );
}

export function PaymentsSettingsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('settings.payments.title'));
  const queryClient = useQueryClient();
  const methods = useQuery({
    queryKey: PAYMENT_METHODS_KEY,
    queryFn: ({ signal }) => fetchPaymentMethods(signal),
  });
  const settings = useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: ({ signal }) => fetchSettings(signal),
  });

  return (
    <PaymentsSettingsView
      methods={{
        state: methods.data
          ? { status: 'ready', methods: methods.data }
          : methods.isError
            ? {
                status: 'error',
                error: methods.error,
                onRetry: () => void methods.refetch(),
              }
            : { status: 'loading' },
        onChange: async (method, isActive) => {
          await updatePaymentMethod(method.code, {
            is_active: isActive,
            version: method.version,
          });
          await queryClient.invalidateQueries({
            queryKey: PAYMENT_METHODS_KEY,
          });
        },
        onReload: async () => {
          const result = await methods.refetch();
          if (result.isError) {
            throw result.error;
          }
        },
      }}
      message={{
        state: settings.data
          ? { status: 'ready', settings: settings.data }
          : settings.isError
            ? {
                status: 'error',
                error: settings.error,
                onRetry: () => void settings.refetch(),
              }
            : { status: 'loading' },
        onSave: async (body) => {
          const saved = await updateSettings(body);
          queryClient.setQueryData(SETTINGS_QUERY_KEY, saved);
        },
        onReload: async () => {
          const result = await settings.refetch();
          if (result.isError) {
            throw result.error;
          }
        },
      }}
    />
  );
}
