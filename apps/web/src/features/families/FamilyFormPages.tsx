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
  createFamily,
  familyDetailKey,
  FAMILIES_LIST_QUERY_KEY,
  fetchFamily,
  updateFamily,
  type FamilyDetail,
} from './api';
import { FamilyForm } from './FamilyForm';
import { createBody, EMPTY_FAMILY, updateBody, valuesOf } from './rules';

// /families/new and /families/:familyId/edit (#172 H2), both for
// family.manage holders. Create sends POST and goes to the detail, where
// members are added; edit sends `version` plus the changed name and goes
// back to the detail, which then says "Changes saved." (polite status).

/** Navigation state the detail page reads to announce a save. */
export interface SavedState {
  saved: true;
}

function useStore() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return (family: FamilyDetail) => {
    queryClient.setQueryData(familyDetailKey(family.id), family);
    void queryClient.invalidateQueries({ queryKey: FAMILIES_LIST_QUERY_KEY });
    const state: SavedState = { saved: true };
    void navigate(`/families/${family.id}`, { state });
  };
}

export function NewFamilyPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('families.add'));
  const titleId = useId();
  const store = useStore();

  return (
    <div className="flex flex-col gap-6">
      <p>
        <Link to="/families" className={TEXT_LINK_CLASS}>
          {t('families.back')}
        </Link>
      </p>
      <h1 id={titleId} className="text-2xl font-semibold">
        {t('families.add')}
      </h1>
      <FamilyForm
        defaultValues={EMPTY_FAMILY}
        cancelTo="/families"
        labelledBy={titleId}
        onSubmit={async (values) => {
          store(await createFamily(createBody(values)));
        }}
      />
    </div>
  );
}

export function EditFamilyPage() {
  const { t } = useTranslation();
  const { familyId = '' } = useParams();
  const titleId = useId();
  const key = familyDetailKey(familyId);
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchFamily(familyId, signal),
  });
  const store = useStore();
  useDocumentTitle(t('families.edit'));

  if (detail.isError) {
    const notFound =
      detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('families.notFound') : t('families.edit')}
        </h1>
        {notFound ? null : (
          <ErrorAlert
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
        <p>
          <Link to="/families" className={TEXT_LINK_CLASS}>
            {t('families.back')}
          </Link>
        </p>
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }
  const family = detail.data;
  return (
    <div className="flex flex-col gap-6">
      <p>
        <Link to={`/families/${family.id}`} className={TEXT_LINK_CLASS}>
          {t('families.backToFamily')}
        </Link>
      </p>
      <h1 id={titleId} className="text-2xl font-semibold break-words">
        {t('families.editNamed', { name: family.name })}
      </h1>
      <FamilyForm
        key={family.version}
        defaultValues={valuesOf(family)}
        cancelTo={`/families/${family.id}`}
        labelledBy={titleId}
        onSubmit={async (values) => {
          const current = queryClient.getQueryData<FamilyDetail>(key) ?? family;
          store(await updateFamily(family.id, updateBody(current, values)));
        }}
        onReload={async () => {
          await detail.refetch();
        }}
      />
    </div>
  );
}
