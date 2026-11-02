import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useSession, useCan } from '@/features/auth/useSession';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import type { LocalDate } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import {
  compensationKey,
  createPayTerms,
  createRate,
  deletePayTerms,
  deleteRate,
  fetchCompensation,
  PAYROLL_TUTORS_KEY,
  type CompensationRate,
  type PayTerms,
  type TutorCompensation,
} from './api';
import { CompensationView } from './CompensationView';
import { usePayLabels } from './labels';
import { PayItemsSection } from './PayItemsSection';
import { PayrollTabs } from './PayrollTabs';
import { PayTermsDialog } from './PayTermsDialog';
import { RateDialog } from './RateDialog';
import {
  rateCreateBody,
  termsCreateBody,
  type RateFormValues,
  type TermsFormValues,
} from './rules';

// /payroll/tutors/:tutorUserId (#425 C): a tutor's terms and rates, with the
// add dialogs and delete behind a confirmation for payroll.manage holders.
// A non-Owner's own page is read-only with a note (#105 E5; the API refuses
// too). There is no edit: a typo is a delete and an add. A version that is
// gone by the time it's deleted refreshes the page; a paid one stays, with
// the API's message in the confirmation. After a change the tutor queries
// (this page and the list) are invalidated. The bonuses and deductions are
// their own section after the rates (#454 B): CompensationView stays as it is,
// because /my-pay shares it.

function BackLink() {
  const { t } = useTranslation();
  return (
    <p>
      <Link
        to="/payroll/tutors"
        data-payroll-back
        className="inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6"
      >
        {t('payroll.back')}
      </Link>
    </p>
  );
}

export function TutorPayPage() {
  const { t } = useTranslation();
  const { tutorUserId = '' } = useParams();
  const queryClient = useQueryClient();
  const session = useSession();
  const canManage = useCan('payroll.manage');
  const detail = useQuery({
    queryKey: compensationKey(tutorUserId),
    queryFn: ({ signal }) => fetchCompensation(tutorUserId, signal),
  });
  const notFound =
    detail.error instanceof ApiError && detail.error.status === 404;
  useDocumentTitle(detail.data?.tutor.display_name ?? t('payroll.list.title'));

  if (notFound) {
    return <NotFoundPage />;
  }
  if (detail.isError) {
    return (
      <div className="flex flex-col gap-8">
        <PayrollTabs />
        <div className="flex flex-col gap-4">
          <h1 className="text-2xl font-semibold">{t('payroll.list.title')}</h1>
          <ErrorAlert
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
          <BackLink />
        </div>
      </div>
    );
  }
  if (!detail.data) {
    return (
      <div className="flex flex-col gap-8">
        <PayrollTabs />
        <LoadingState />
      </div>
    );
  }

  const user = session.status === 'signedIn' ? session.user : null;
  const ownPay =
    user?.id === tutorUserId && !user.roles.includes('owner') && canManage;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: PAYROLL_TUTORS_KEY });
  }

  return (
    <TutorPayPageView
      compensation={detail.data}
      canEdit={canManage && !ownPay}
      ownPay={ownPay}
      onAddTerms={async (values) => {
        await createPayTerms(tutorUserId, termsCreateBody(values));
        await refresh();
      }}
      onAddRate={async (values) => {
        await createRate(tutorUserId, rateCreateBody(values));
        await refresh();
      }}
      onDelete={async (version) => {
        await (version.kind === 'terms'
          ? deletePayTerms(tutorUserId, version.item.id)
          : deleteRate(tutorUserId, version.item.id));
        await refresh();
      }}
      onReload={refresh}
      items={
        <PayItemsSection
          tutorUserId={tutorUserId}
          canEdit={canManage && !ownPay}
          today={detail.data.today as LocalDate}
        />
      }
    />
  );
}

export type DeletedVersion =
  { kind: 'terms'; item: PayTerms } | { kind: 'rate'; item: CompensationRate };

interface TutorPayPageViewProps {
  compensation: TutorCompensation;
  /** payroll.manage, and not a non-Owner's own pay. */
  canEdit: boolean;
  /** Shows "Someone else must change your pay" (C9). */
  ownPay: boolean;
  /** Each rejects with the error to show. */
  onAddTerms: (values: TermsFormValues) => Promise<void>;
  onAddRate: (values: RateFormValues) => Promise<void>;
  onDelete: (version: DeletedVersion) => Promise<void>;
  onReload: () => Promise<void>;
  /** The bonuses and deductions section. */
  items?: ReactNode;
}

/** The page's content, without any query. */
export function TutorPayPageView({
  compensation,
  canEdit,
  ownPay,
  onAddTerms,
  onAddRate,
  onDelete,
  onReload,
  items,
}: TutorPayPageViewProps) {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const [adding, setAdding] = useState<'terms' | 'rate' | null>(null);
  const [deleting, setDeleting] = useState<DeletedVersion | null>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState('');
  const today = compensation.today as LocalDate;

  function startDelete(version: DeletedVersion) {
    setAnnouncement('');
    setDeleteError(null);
    setDeleting(version);
  }

  function startAdd(kind: 'terms' | 'rate') {
    setAnnouncement('');
    setAdding(kind);
  }

  async function confirmDelete() {
    if (!deleting) {
      return;
    }
    setDeleteError(null);
    try {
      await onDelete(deleting);
      setDeleting(null);
      setAnnouncement(t('payroll.compensation.deleted'));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setDeleting(null);
        setAnnouncement(t('payroll.compensation.gone'));
        await onReload();
        return;
      }
      setDeleteError(caught);
    }
  }

  function closeAdd(saved: boolean) {
    setAdding(null);
    if (saved) {
      setAnnouncement(t('payroll.compensation.saved'));
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <PayrollTabs />
      <div className="flex flex-col gap-3">
        <BackLink />
        <CompensationView
          compensation={compensation}
          note={ownPay ? t('payroll.compensation.ownPay') : undefined}
          actions={
            canEdit
              ? {
                  onAddTerms: () => startAdd('terms'),
                  onAddRate: () => startAdd('rate'),
                  onDeleteTerms: (item) => startDelete({ kind: 'terms', item }),
                  onDeleteRate: (item) => startDelete({ kind: 'rate', item }),
                }
              : undefined
          }
        />
      </div>
      {items}
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {canEdit ? (
        <>
          <PayTermsDialog
            open={adding === 'terms'}
            today={today}
            onSubmit={onAddTerms}
            onClose={closeAdd}
          />
          <RateDialog
            open={adding === 'rate'}
            today={today}
            onSubmit={onAddRate}
            onClose={closeAdd}
          />
          <ConfirmDialog
            open={deleting !== null}
            title={t('payroll.deleteDialog.title')}
            body={
              deleting?.kind === 'terms'
                ? t('payroll.deleteDialog.terms', {
                    date: labels.date(deleting.item),
                    value: labels.terms(deleting.item),
                  })
                : deleting
                  ? t('payroll.deleteDialog.rate', {
                      kind: labels.kind(deleting.item.class_kind),
                      date: labels.date(deleting.item),
                      value: labels.rate(deleting.item),
                    })
                  : ''
            }
            confirmLabel={t('payroll.deleteDialog.confirm')}
            destructive
            alert={deleteError ? <ErrorAlert error={deleteError} /> : null}
            onConfirm={confirmDelete}
            onCancel={() => {
              setDeleting(null);
              setDeleteError(null);
            }}
          />
        </>
      ) : null}
    </div>
  );
}
