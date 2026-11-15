import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { useCan } from '@/features/auth/useSession';
import { CatalogStatusBadge } from '@/features/catalog/CatalogStatusBadge';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import {
  createTemplate,
  fetchSubjectOptions,
  fetchTemplate,
  replaceTemplate,
  setTemplateActive,
  SUBJECT_OPTIONS_KEY,
  templateKey,
  TEMPLATES_QUERY_KEY,
  type CourseTemplate,
} from './api';
import {
  CourseTemplateForm,
  type SubjectOptionsState,
} from './CourseTemplateForm';
import { CoursesTabs } from './CoursesTabs';
import { CourseTemplateView } from './CourseTemplateView';

// /courses/templates/new (#64 G1, G3; course.manage): the editor for a new
// template; after the create the page goes to the template, which says
// "Saved.". /courses/templates/:templateId (course.view): the template;
// holders of course.manage edit it (and deactivate or reactivate it),
// others (Finance Staff) get the read-only view (G5). A missing template is
// "This course template doesn't exist."

function useSubjectOptions(enabled: boolean): SubjectOptionsState {
  const query = useQuery({
    queryKey: SUBJECT_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled,
  });
  if (query.data) {
    return { status: 'ready', items: query.data };
  }
  if (query.isError) {
    return {
      status: 'error',
      error: query.error,
      onRetry: () => void query.refetch(),
    };
  }
  return { status: 'loading' };
}

function BackLink() {
  const { t } = useTranslation();
  return (
    <Link to="/courses" className={`${TEXT_LINK_CLASS} self-start text-sm`}>
      {t('courses.templates.back')}
    </Link>
  );
}

export function NewCourseTemplatePage() {
  const { t } = useTranslation();
  useDocumentTitle(t('courses.templates.newTitle'));
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const subjects = useSubjectOptions(true);

  return (
    <div className="flex flex-col gap-6">
      <CoursesTabs />
      <BackLink />
      <h1 className="text-2xl font-semibold">
        {t('courses.templates.newTitle')}
      </h1>
      <CourseTemplateForm
        subjects={subjects}
        onSave={async (body) => {
          const created = await createTemplate(body);
          queryClient.setQueryData(templateKey(created.id), created);
          await queryClient.invalidateQueries({
            queryKey: [...TEMPLATES_QUERY_KEY, 'list'],
          });
          void navigate(`/courses/templates/${created.id}`, {
            state: { saved: true },
          });
        }}
      />
    </div>
  );
}

export function CourseTemplateEditPage() {
  const { t } = useTranslation();
  const { templateId = '' } = useParams();
  const location = useLocation();
  const queryClient = useQueryClient();
  const canManage = useCan('course.manage');
  const key = templateKey(templateId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchTemplate(templateId, signal),
  });
  const subjects = useSubjectOptions(canManage);
  const notFound =
    detail.isError &&
    detail.error instanceof ApiError &&
    detail.error.status === 404;
  useDocumentTitle(
    detail.data?.name ??
      (notFound
        ? t('courses.templates.notFound')
        : t('courses.templates.title')),
  );
  const justCreated =
    (location.state as { saved?: boolean } | null)?.saved === true;

  async function stored(template: CourseTemplate) {
    queryClient.setQueryData(key, template);
    await queryClient.invalidateQueries({
      queryKey: [...TEMPLATES_QUERY_KEY, 'list'],
    });
  }

  if (notFound) {
    return (
      <div className="flex flex-col gap-6">
        <CoursesTabs />
        <BackLink />
        <h1 className="text-2xl font-semibold">
          {t('courses.templates.notFound')}
        </h1>
      </div>
    );
  }
  if (detail.isError) {
    return (
      <div className="flex flex-col gap-6">
        <CoursesTabs />
        <BackLink />
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </div>
    );
  }
  const template = detail.data;
  if (!template) {
    return (
      <div className="flex flex-col gap-6">
        <CoursesTabs />
        <LoadingState layout="page" />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-6">
      <CoursesTabs />
      <BackLink />
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold break-words">{template.name}</h1>
        <CatalogStatusBadge active={template.is_active} />
      </div>
      {canManage ? (
        <>
          <StatusControl template={template} onStored={stored} />
          <CourseTemplateForm
            template={template}
            subjects={subjects}
            initialStatus={justCreated ? t('courses.templates.saved') : ''}
            onSave={async (body) => {
              await stored(
                await replaceTemplate(template.id, {
                  ...body,
                  version: template.version,
                }),
              );
            }}
            onReload={async () => {
              await detail.refetch();
            }}
          />
        </>
      ) : (
        <CourseTemplateView template={template} />
      )}
    </div>
  );
}

/** "Deactivate" / "Reactivate" with the help line; a version conflict
 * offers "Reload". */
function StatusControl({
  template,
  onStored,
}: {
  template: CourseTemplate;
  onStored: (template: CourseTemplate) => Promise<void>;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      await onStored(
        await setTemplateActive(
          template.id,
          template.version,
          !template.is_active,
        ),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={
            isConflict
              ? () => {
                  setError(null);
                  void queryClient.invalidateQueries({
                    queryKey: templateKey(template.id),
                  });
                }
              : undefined
          }
          retryLabel={isConflict ? t('courses.templates.reload') : undefined}
        />
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => void toggle()}
        >
          {template.is_active
            ? t('courses.templates.deactivate')
            : t('courses.templates.reactivate')}
        </Button>
        <p className="text-sm text-muted-foreground">
          {t('courses.templates.statusHelp')}
        </p>
      </div>
    </div>
  );
}
