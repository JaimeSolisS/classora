import type { Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { studentPlansPath } from './api';
import type { PlanRefusal } from './rules';

// The failure region of the learning plan pages (#310 C6, D6), focusable
// (tabIndex -1) so focus moves to it after a refused write:
// - a version conflict (the plan's or a milestone's) or an unknown id:
//   "This plan changed elsewhere." with Reload;
// - active_plan_exists: "{subject} already has an active plan…" with a link
//   to the student's plans;
// - plan_closed / plan_not_draft / plan_not_active / plan_subject_not_assigned
//   / plan_read_only: their message with Reload;
// - a milestone gone meanwhile: "This milestone no longer exists." + Reload;
// - anything else: the error's own message.

export interface PlanFailureState {
  refusal: PlanRefusal;
  error: unknown;
}

interface PlanFailureProps {
  failure: PlanFailureState;
  /** The plan's subject (active_plan_exists). */
  subjectName: string;
  studentId: string;
  onReload: () => void;
  ref?: Ref<HTMLDivElement>;
}

export function PlanFailure({
  failure,
  subjectName,
  studentId,
  onReload,
  ref,
}: PlanFailureProps) {
  const { t } = useTranslation();
  const { refusal, error } = failure;
  let content;
  if (refusal === 'conflict') {
    content = (
      <ErrorAlert
        error={error}
        message={t('learningPlans.conflict')}
        retryLabel={t('academics.reload')}
        onRetry={onReload}
      />
    );
  } else if (refusal === 'milestone_missing') {
    content = (
      <ErrorAlert
        error={error}
        message={t('learningPlans.milestoneMissing')}
        retryLabel={t('academics.reload')}
        onRetry={onReload}
      />
    );
  } else if (refusal === 'active_plan') {
    content = (
      <div className="flex min-w-0 flex-col gap-2">
        <ErrorAlert
          error={error}
          message={t('learningPlans.activePlanExists', {
            subject: subjectName,
          })}
        />
        <Link
          to={studentPlansPath(studentId)}
          className={`${SMALL_LINK_CLASS} self-start`}
        >
          {t('learningPlans.toStudentPlans')}
        </Link>
      </div>
    );
  } else if (refusal === 'state' || refusal === 'forbidden') {
    content = (
      <ErrorAlert
        error={error}
        retryLabel={t('academics.reload')}
        onRetry={onReload}
      />
    );
  } else {
    content = <ErrorAlert error={error} />;
  }
  return (
    <div ref={ref} tabIndex={-1} className="min-w-0 outline-none">
      {content}
    </div>
  );
}
