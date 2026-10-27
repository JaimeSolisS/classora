import type { CompletionCriteria, StudentCompletion } from './api';

// Course completion on the cohort page (#323), pure. Who asks for it, and
// the roster's counts. Every status comes from the API: the counts below
// only count the server's statuses, never recompute one (W1 choice).

/** The roster (staff and the cohort's tutor), the family view, or none. */
export type CompletionAudience = 'roster' | 'family' | null;

export interface AudienceFacts {
  /** `evaluation.manage` (Owner, Administrator, Academic Coordinator). */
  canEvaluate: boolean;
  /** The cohort detail has `tutor_roster` (an empty array counts). */
  hasTutorRoster: boolean;
  roles: readonly string[];
}

/**
 * W0: the roster for `evaluation.manage` holders and the cohort's tutor;
 * the family view for a student or a guardian; otherwise null, and no
 * request (Finance, any other course.view-only holder). Roster audiences
 * win over family roles.
 */
export function completionAudience({
  canEvaluate,
  hasTutorRoster,
  roles,
}: AudienceFacts): CompletionAudience {
  if (canEvaluate || hasTutorRoster) {
    return 'roster';
  }
  if (roles.includes('student') || roles.includes('guardian')) {
    return 'family';
  }
  return null;
}

export interface RequiredCount {
  /** Required items the API marks passed (assessments) or met (modules). */
  done: number;
  required: number;
}

/** "{passed} of {required} required passed": optional ones never count. */
export function assessmentCount(student: StudentCompletion): RequiredCount {
  const required = student.assessments.items.filter((item) => item.is_required);
  return {
    done: required.filter((item) => item.status === 'passed').length,
    required: required.length,
  };
}

/** "{met} of {required} required" (the API lists required modules only). */
export function moduleCount(student: StudentCompletion): RequiredCount {
  return {
    done: student.modules.items.filter((item) => item.status === 'met').length,
    required: student.modules.items.length,
  };
}

/** The criteria's required assessments, in the API's order. */
export function requiredAssessments(criteria: CompletionCriteria) {
  return criteria.assessments.filter((assessment) => assessment.is_required);
}

/** Whether the snapshot has no completion criterion at all. */
export function hasNoCriteria(criteria: CompletionCriteria): boolean {
  return (
    criteria.min_attendance_percent === null &&
    criteria.required_modules.length === 0 &&
    requiredAssessments(criteria).length === 0
  );
}

/**
 * W3: "My progress" for a student who isn't also a guardian; otherwise
 * each section is named after its student (the session has no student
 * id, so a student-guardian sees their own name too).
 */
export function familyHeading(roles: readonly string[]): 'mine' | 'named' {
  return roles.includes('student') && !roles.includes('guardian')
    ? 'mine'
    : 'named';
}
