import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCan } from '@/features/auth/useSession';
import { BILLING_SETTINGS_KEY, fetchBillingSettings } from '../settings/api';
import {
  fetchUnbilledEnrollments,
  fetchUnbilledItems,
  unbilledEnrollmentsKey,
  unbilledFilters,
  unbilledListKey,
  unbilledParams,
  type UnbilledFilters,
} from './api';
import type { UnbilledEnrollmentsState } from './UnbilledEnrollments';
import { UnbilledView, type UnbilledState } from './UnbilledView';

// /billing/unbilled (#366 C, #372 B), for billing.view holders: the
// billing start date (shared with the Settings tab, for the empty states),
// one page of the unbilled sessions and one page of the unbilled
// enrollments, with the reason and both pages in the URL
// (?reason=&page=&enrollment_page=, so a reload and Back keep them). A
// failed settings or sessions load shows the error in the sessions section
// with "Try again", which reloads what failed; the enrollments section
// loads and fails on its own (hidden while the settings aren't known or
// billing is off).

export function UnbilledPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.unbilled.title'));
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = unbilledFilters(searchParams);
  const canOpenFamilies = useCan('guardian.view');
  const canManagePrices = useCan('billing.manage');

  const settings = useQuery({
    queryKey: BILLING_SETTINGS_KEY,
    queryFn: ({ signal }) => fetchBillingSettings(signal),
  });
  const list = useQuery({
    queryKey: unbilledListKey(filters),
    queryFn: ({ signal }) => fetchUnbilledItems(filters, signal),
    placeholderData: keepPreviousData,
  });

  const enrollmentList = useQuery({
    queryKey: unbilledEnrollmentsKey(filters),
    queryFn: ({ signal }) => fetchUnbilledEnrollments(filters, signal),
    placeholderData: keepPreviousData,
  });

  function apply(next: UnbilledFilters) {
    setSearchParams(unbilledParams(next));
  }

  const failed = settings.isError ? settings.error : list.error;
  const state: UnbilledState =
    settings.isError || list.isError
      ? {
          status: 'error',
          error: failed,
          onRetry: () => {
            if (settings.isError) void settings.refetch();
            if (list.isError) void list.refetch();
          },
        }
      : settings.data && list.data
        ? { status: 'ready', settings: settings.data, page: list.data }
        : { status: 'loading' };

  const startsOn = settings.data?.billing_starts_on ?? null;
  const enrollments: UnbilledEnrollmentsState = settings.isError
    ? { status: 'hidden' }
    : !settings.data
      ? { status: 'loading' }
      : startsOn === null
        ? { status: 'hidden' }
        : enrollmentList.isError
          ? {
              status: 'error',
              error: enrollmentList.error,
              onRetry: () => void enrollmentList.refetch(),
            }
          : enrollmentList.data
            ? { status: 'ready', startsOn, page: enrollmentList.data }
            : { status: 'loading' };

  return (
    <UnbilledView
      state={state}
      enrollments={enrollments}
      filters={filters}
      onFilters={apply}
      onPage={(page) => apply({ ...filters, page })}
      onEnrollmentPage={(enrollmentPage) =>
        apply({ ...filters, enrollmentPage })
      }
      canOpenFamilies={canOpenFamilies}
      canManagePrices={canManagePrices}
    />
  );
}
