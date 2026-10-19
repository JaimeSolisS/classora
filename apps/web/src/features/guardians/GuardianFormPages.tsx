import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import {
  createGuardian,
  fetchGuardian,
  guardianDetailKey,
  GUARDIANS_LIST_QUERY_KEY,
  updateGuardian,
  type GuardianDetail,
} from './api';
import { GuardianForm } from './GuardianForm';
import {
  createBody,
  EMPTY_GUARDIAN,
  fullName,
  updateBody,
  valuesOf,
} from './rules';

// /guardians/new and /guardians/:guardianId/edit (#34 I2), both for
// family.manage holders. Create sends POST and goes to the detail; edit sends
// `version` plus the changed fields and goes back to the detail, which then
// says "Changes saved." (polite status).

/** Navigation state the detail page reads to announce a save. */
export interface SavedState {
  saved: true;
}

function useStore() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return (guardian: GuardianDetail) => {
    queryClient.setQueryData(guardianDetailKey(guardian.id), guardian);
    void queryClient.invalidateQueries({ queryKey: GUARDIANS_LIST_QUERY_KEY });
    const state: SavedState = { saved: true };
    void navigate(`/guardians/${guardian.id}`, { state });
  };
}

export function NewGuardianPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('guardians.add'));
  const titleId = useId();
  const store = useStore();

  return (
    <div className="flex flex-col gap-6">
      <p>
        <Link to="/guardians" className={TEXT_LINK_CLASS}>
          {t('guardians.back')}
        </Link>
      </p>
      <h1 id={titleId} className="text-2xl font-semibold">
        {t('guardians.add')}
      </h1>
      <GuardianForm
        defaultValues={EMPTY_GUARDIAN}
        cancelTo="/guardians"
        labelledBy={titleId}
        onSubmit={async (values) => {
          store(await createGuardian(createBody(values)));
        }}
      />
    </div>
  );
}

export function EditGuardianPage() {
  const { t } = useTranslation();
  const { guardianId = '' } = useParams();
  const titleId = useId();
  const key = guardianDetailKey(guardianId);
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchGuardian(guardianId, signal),
  });
  const store = useStore();
  useDocumentTitle(t('guardians.edit'));

  if (detail.isError) {
    const notFound =
      detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('guardians.notFound') : t('guardians.edit')}
        </h1>
        {notFound ? null : (
          <ErrorAlert
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
        <p>
          <Link to="/guardians" className={TEXT_LINK_CLASS}>
            {t('guardians.back')}
          </Link>
        </p>
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }
  const guardian = detail.data;
  return (
    <div className="flex flex-col gap-6">
      <p>
        <Link to={`/guardians/${guardian.id}`} className={TEXT_LINK_CLASS}>
          {t('guardians.backToGuardian')}
        </Link>
      </p>
      <h1 id={titleId} className="text-2xl font-semibold break-words">
        {t('guardians.editNamed', { name: fullName(guardian) })}
      </h1>
      <GuardianForm
        key={guardian.version}
        defaultValues={valuesOf(guardian)}
        cancelTo={`/guardians/${guardian.id}`}
        labelledBy={titleId}
        onSubmit={async (values) => {
          const current =
            queryClient.getQueryData<GuardianDetail>(key) ?? guardian;
          store(await updateGuardian(guardian.id, updateBody(current, values)));
        }}
        onReload={async () => {
          await detail.refetch();
        }}
      />
    </div>
  );
}
