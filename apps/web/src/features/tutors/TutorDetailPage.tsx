import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { useSession } from '@/features/auth/useSession';
import { StateBadge } from '@/features/users/admin/StateBadge';
import {
  cancelQualification,
  fetchQualifications,
  fetchTutor,
  qualificationsKey,
  requestQualification,
  tutorDetailKey,
  TUTORS_LIST_QUERY_KEY,
  updateTutor,
  type TutorDetail,
  type TutorUpdate,
} from './api';
import { QualificationsSection } from './QualificationsSection';
import { TutorForm } from './TutorForm';

// /tutors/:userId (#43 E3): one tutor profile. The h1 is the tutor's name;
// the contact fields and the professional experience are shown as text, with
// "Edit" when the server's `editable_fields` isn't empty. The form shows only
// those fields. A successful save replaces the cached detail with the
// response and refreshes the list. A tutor the caller may not see is a 404
// like a missing one: "This tutor doesn't exist."
//
// Below the profile, the Qualifications section (#44 F1): the list query of
// the tutor's qualifications and, for the tutor themselves, request and
// cancel. A `#qualifications` hash (the decision notifications' link)
// scrolls to it once the page has loaded.

const LINK_CLASS =
  'font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

export function TutorDetailPage() {
  const { t } = useTranslation();
  const { userId = '' } = useParams();
  const queryClient = useQueryClient();
  const key = tutorDetailKey(userId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchTutor(userId, signal),
  });
  const notFound =
    detail.isError &&
    detail.error instanceof ApiError &&
    detail.error.status === 404;
  useDocumentTitle(
    detail.data?.display_name ??
      (notFound ? t('tutors.detail.notFound') : t('tutors.title')),
  );

  async function save(body: TutorUpdate) {
    const tutor = await updateTutor(userId, body);
    queryClient.setQueryData(key, tutor);
    void queryClient.invalidateQueries({ queryKey: TUTORS_LIST_QUERY_KEY });
  }

  async function reload() {
    await detail.refetch();
  }

  if (detail.isError) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('tutors.detail.notFound') : t('tutors.title')}
        </h1>
        {notFound ? null : (
          <ErrorAlert error={detail.error} onRetry={() => void reload()} />
        )}
        <p>
          <Link to="/tutors" className={LINK_CLASS}>
            {t('tutors.detail.backToList')}
          </Link>
        </p>
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }
  return (
    <TutorDetailView
      key={detail.data.user_id}
      tutor={detail.data}
      onSave={save}
      onReload={reload}
    >
      <TutorQualifications tutor={detail.data} />
    </TutorDetailView>
  );
}

/** The Qualifications section with its query and actions. */
function TutorQualifications({ tutor }: { tutor: TutorDetail }) {
  const session = useSession();
  const queryClient = useQueryClient();
  const location = useLocation();
  const key = qualificationsKey(tutor.user_id);
  const list = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchQualifications(tutor.user_id, signal),
  });
  const isSelf =
    session.status === 'signedIn' && session.user.id === tutor.user_id;
  const scrolled = useRef(false);

  useEffect(() => {
    if (
      !scrolled.current &&
      location.hash === '#qualifications' &&
      list.data !== undefined
    ) {
      scrolled.current = true;
      document.getElementById('qualifications')?.scrollIntoView?.();
    }
  }, [location.hash, list.data]);

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: key });
  }

  return (
    <QualificationsSection
      state={
        list.isError
          ? {
              status: 'error',
              error: list.error,
              onRetry: () => void list.refetch(),
            }
          : list.data
            ? { status: 'ready', items: list.data }
            : { status: 'loading' }
      }
      teachingSubjects={tutor.teaching_subjects}
      canRequest={isSelf && tutor.state === 'active'}
      onRequest={async (subjectId) => {
        await requestQualification(tutor.user_id, subjectId);
        await refresh();
      }}
      onCancel={async (item) => {
        try {
          await cancelQualification(tutor.user_id, item.id);
        } finally {
          await refresh();
        }
      }}
    />
  );
}

interface TutorDetailViewProps {
  tutor: TutorDetail;
  /** Sends the change; resolves after the cache holds the new detail. */
  onSave: (body: TutorUpdate) => Promise<void>;
  onReload: () => Promise<void>;
  /** Opens with the form shown (stories). */
  initiallyEditing?: boolean;
  /** Sections below the profile (the page's Qualifications section). */
  children?: ReactNode;
}

/** The detail page's content, without any query (stories use it). */
export function TutorDetailView({
  tutor,
  onSave,
  onReload,
  initiallyEditing = false,
  children,
}: TutorDetailViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const canEdit = tutor.editable_fields.length > 0;
  const [editing, setEditing] = useState(initiallyEditing && canEdit);
  const [saved, setSaved] = useState(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);
  const notSet = t('tutors.profile.notSet');

  useEffect(() => {
    if (!editing && returnFocus.current) {
      returnFocus.current = false;
      editButton.current?.focus();
    }
  }, [editing]);

  async function submit(body: TutorUpdate) {
    await onSave(body);
    setSaved(true);
    returnFocus.current = true;
    setEditing(false);
  }

  function cancel() {
    returnFocus.current = true;
    setEditing(false);
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <p>
          <Link to="/tutors" className={LINK_CLASS}>
            {t('tutors.detail.backToList')}
          </Link>
        </p>
        <h1 className="text-2xl font-semibold break-words">
          {tutor.display_name}
        </h1>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm">
          <dt className="font-medium">{t('tutors.detail.username')}</dt>
          <dd className="min-w-0 break-words">{tutor.username}</dd>
          <dt className="font-medium">{t('tutors.detail.state')}</dt>
          <dd>
            <StateBadge state={tutor.state} />
          </dd>
        </dl>
      </div>
      {editing ? (
        <TutorForm
          tutor={tutor}
          onSubmit={submit}
          onCancel={cancel}
          onReload={onReload}
        />
      ) : (
        <section
          aria-labelledby={`${id}-title`}
          className="flex flex-col gap-4"
        >
          <h2 id={`${id}-title`} className="text-lg font-semibold">
            {t('tutors.profile.title')}
          </h2>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="font-medium">{t('tutors.profile.email')}</dt>
            <dd className="min-w-0 break-words">{tutor.email ?? notSet}</dd>
            <dt className="font-medium">{t('tutors.profile.phone')}</dt>
            <dd className="min-w-0 break-words">{tutor.phone ?? notSet}</dd>
            <dt className="font-medium">{t('tutors.profile.experience')}</dt>
            <dd className="min-w-0 break-words whitespace-pre-line">
              {tutor.professional_experience ?? notSet}
            </dd>
          </dl>
          {canEdit ? (
            <div className="flex flex-wrap items-center gap-3">
              <Button
                ref={editButton}
                type="button"
                variant="outline"
                onClick={() => {
                  setSaved(false);
                  setEditing(true);
                }}
              >
                {t('tutors.profile.edit')}
              </Button>
              <p role="status" className="text-sm">
                {saved ? t('tutors.profile.saved') : null}
              </p>
            </div>
          ) : null}
        </section>
      )}
      {children}
    </div>
  );
}
