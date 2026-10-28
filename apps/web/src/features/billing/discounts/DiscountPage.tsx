import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { BillingTabs } from '../BillingTabs';
import {
  fetchLevelOptions,
  fetchSubjectOptions,
  PRICE_OPTIONS_KEY,
  type Option,
  type SubjectOption,
} from '../pricing/api';
import type { TargetPickerProps } from '../TargetPicker';
import type { TargetOption } from '../targets';
import {
  addAssignment,
  discountKey,
  DISCOUNTS_QUERY_KEY,
  fetchDiscount,
  removeAssignment,
  updateDiscount,
  type DiscountAssignment,
  type DiscountDetail,
} from './api';
import { DiscountAssignments } from './DiscountAssignments';
import { DiscountKindBadge, DiscountStatusBadge } from './DiscountBadges';
import { DiscountDialog } from './DiscountDialog';
import { useDiscountLabels } from './labels';
import { discountUpdateBody, type DiscountFormValues } from './rules';

// /billing/discounts/:discountId (#367 D), for billing.manage holders: the
// discount as a definition list, "Edit" (the C dialog), "Deactivate" or
// "Activate" behind a confirmation, and its assignments. After a change
// the discount queries (this one and the list) are invalidated. An
// unknown id is the not-found message with a link back; "Back to
// discounts" keeps the list's filters and page.

function useBackTo(): string {
  const location = useLocation();
  const state: unknown = location.state;
  const search =
    typeof state === 'object' &&
    state !== null &&
    'listSearch' in state &&
    typeof state.listSearch === 'string'
      ? state.listSearch
      : '';
  return `/billing/discounts${search.startsWith('?') ? search : ''}`;
}

function BackLink() {
  const { t } = useTranslation();
  const to = useBackTo();
  return (
    <p>
      <Link to={to} className={CATALOG_LINK_CLASS}>
        {t('billing.discounts.back')}
      </Link>
    </p>
  );
}

export function DiscountPage() {
  const { t } = useTranslation();
  const { discountId = '' } = useParams();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const detail = useQuery({
    queryKey: discountKey(discountId),
    queryFn: ({ signal }) => fetchDiscount(discountId, signal),
  });
  const subjects = useQuery({
    queryKey: [...PRICE_OPTIONS_KEY, 'subjects'],
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: editing,
  });
  const levels = useQuery({
    queryKey: [...PRICE_OPTIONS_KEY, 'levels'],
    queryFn: ({ signal }) => fetchLevelOptions(signal),
    enabled: editing,
  });
  const notFound =
    detail.error instanceof ApiError && detail.error.status === 404;
  useDocumentTitle(
    notFound
      ? t('billing.discounts.notFound')
      : (detail.data?.name ?? t('billing.discounts.detailTitle')),
  );

  if (detail.isError) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <div className="flex flex-col gap-4">
          <h1 className="text-2xl font-semibold">
            {notFound
              ? t('billing.discounts.notFound')
              : t('billing.discounts.detailTitle')}
          </h1>
          {notFound ? (
            <p>{t('billing.discounts.notFoundHelp')}</p>
          ) : (
            <ErrorAlert
              error={detail.error}
              onRetry={() => void detail.refetch()}
            />
          )}
          <BackLink />
        </div>
      </div>
    );
  }
  if (!detail.data) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <LoadingState />
      </div>
    );
  }

  const discount = detail.data;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: DISCOUNTS_QUERY_KEY });
  }

  return (
    <DiscountPageView
      discount={discount}
      editing={editing}
      onEditing={setEditing}
      subjects={subjects.data ?? null}
      levels={levels.data ?? null}
      optionsError={subjects.error ?? levels.error ?? undefined}
      onRetryOptions={() => {
        void subjects.refetch();
        void levels.refetch();
      }}
      onEdit={async (values, initial) => {
        // The latest version (after a reload), the changes against what
        // the dialog opened with.
        await updateDiscount(
          discountId,
          discountUpdateBody(initial, values, discount.version),
        );
        await refresh();
      }}
      onSetActive={async (isActive) => {
        await updateDiscount(discountId, {
          version: discount.version,
          is_active: isActive,
        });
        await refresh();
      }}
      onAssign={async (target) => {
        await addAssignment(
          discountId,
          target.kind === 'student'
            ? { student_id: target.id }
            : { family_id: target.id },
        );
        await refresh();
      }}
      onRemove={async (assignment) => {
        await removeAssignment(discountId, assignment.id);
        await refresh();
      }}
      onReload={refresh}
    />
  );
}

export interface DiscountPageViewProps {
  discount: DiscountDetail;
  editing: boolean;
  onEditing: (editing: boolean) => void;
  subjects: readonly SubjectOption[] | null;
  levels: readonly Option[] | null;
  optionsError?: unknown;
  onRetryOptions?: () => void;
  /** Saves the edit; rejects with the error to show. */
  onEdit: (
    values: DiscountFormValues,
    initial: DiscountFormValues,
  ) => Promise<void>;
  /** Activates or deactivates; rejects with the error to show. */
  onSetActive: (isActive: boolean) => Promise<void>;
  onAssign: (target: TargetOption) => Promise<void>;
  onRemove: (assignment: DiscountAssignment) => Promise<void>;
  onReload: () => Promise<void>;
  /** Stories: the picker's search instead of the API. */
  search?: TargetPickerProps['search'];
}

/** The detail page's content, without any query. */
export function DiscountPageView({
  discount,
  editing,
  onEditing,
  subjects,
  levels,
  optionsError,
  onRetryOptions,
  onEdit,
  onSetActive,
  onAssign,
  onRemove,
  onReload,
  search,
}: DiscountPageViewProps) {
  const { t } = useTranslation();
  const labels = useDiscountLabels();
  const [toggling, setToggling] = useState(false);
  const [toggleError, setToggleError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState('');

  async function confirmToggle() {
    setToggleError(null);
    const next = !discount.is_active;
    try {
      await onSetActive(next);
      setToggling(false);
      setAnnouncement(
        next
          ? t('billing.discounts.activated')
          : t('billing.discounts.deactivated'),
      );
    } catch (caught) {
      setToggleError(caught);
    }
  }

  const conflict =
    toggleError instanceof ApiError && toggleError.code === 'version_conflict';

  const terms: [string, ReactNode][] = [
    [
      t('billing.discounts.columns.kind'),
      <DiscountKindBadge key="kind" kind={discount.kind} />,
    ],
    [t('billing.discounts.columns.value'), labels.value(discount)],
    [t('billing.discounts.columns.who'), labels.who(discount)],
    [t('billing.discounts.columns.criteria'), labels.criteria(discount)],
    [t('billing.discounts.columns.validity'), labels.validity(discount)],
    [
      t('billing.discounts.columns.status'),
      <DiscountStatusBadge key="status" status={discount.status} />,
    ],
  ];

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-3">
        <BackLink />
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-semibold break-words">
            {discount.name}
          </h1>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              onClick={() => {
                setAnnouncement('');
                onEditing(true);
              }}
            >
              {t('billing.discounts.edit')}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setAnnouncement('');
                setToggleError(null);
                setToggling(true);
              }}
            >
              {discount.is_active
                ? t('billing.discounts.deactivate')
                : t('billing.discounts.activate')}
            </Button>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          {t('billing.discounts.intro')}
        </p>
        <p role="status" className="text-sm">
          {announcement}
        </p>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
          {terms.map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="font-medium">{term}</dt>
              <dd className="break-words">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
      <DiscountAssignments
        discount={discount}
        onAssign={onAssign}
        onRemove={onRemove}
        search={search}
      />
      <DiscountDialog
        open={editing}
        discount={discount}
        subjects={subjects}
        levels={levels}
        optionsError={optionsError}
        onRetryOptions={onRetryOptions}
        onSubmit={async (values, initial) => {
          await onEdit(values, initial);
          onEditing(false);
          setAnnouncement(t('billing.discounts.saved'));
        }}
        onReload={() => void onReload()}
        onClose={() => onEditing(false)}
      />
      <ConfirmDialog
        open={toggling}
        title={
          discount.is_active
            ? t('billing.discounts.deactivateDialog.title')
            : t('billing.discounts.activateDialog.title')
        }
        body={
          discount.is_active
            ? t('billing.discounts.deactivateDialog.body', {
                name: discount.name,
              })
            : t('billing.discounts.activateDialog.body', {
                name: discount.name,
              })
        }
        confirmLabel={
          discount.is_active
            ? t('billing.discounts.deactivate')
            : t('billing.discounts.activate')
        }
        destructive={discount.is_active}
        alert={
          toggleError ? (
            <ErrorAlert
              error={toggleError}
              onRetry={
                conflict
                  ? () => {
                      setToggling(false);
                      setToggleError(null);
                      void onReload();
                    }
                  : undefined
              }
              retryLabel={conflict ? t('billing.prices.reload') : undefined}
            />
          ) : null
        }
        onConfirm={confirmToggle}
        onCancel={() => {
          setToggling(false);
          setToggleError(null);
        }}
      />
    </div>
  );
}
