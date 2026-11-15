import { useQuery } from '@tanstack/react-query';
import type { TFunction } from 'i18next';
import { useSearchParams } from 'react-router';
import { FAMILY_STUDENTS_KEY } from '@/features/academics/history/api';
import { uuidParam } from '@/features/academics/history/urlState';
import {
  fetchFamilyStudents,
  type FamilyStudent,
} from '@/features/my-schedule/api';

// The family pages' student (#313 E1, E4): the family's students (GET
// /schedule/family-students, #308's picker source) and the one ?student=
// names, else the first; a foreign or unknown value is never requested.

export function familyLabel(
  student: FamilyStudent,
  t: TFunction<'common'>,
): string {
  const name =
    student.relation === 'self'
      ? t('assignments.family.mine')
      : student.display_name;
  return student.schedule_access === 'consent_required'
    ? t('mySchedule.whose.consentRequired', { name })
    : name;
}

/** The family's students and the one `?student=` selects (else the first). */
export function useFamilyStudent() {
  const [searchParams] = useSearchParams();
  const students = useQuery({
    queryKey: FAMILY_STUDENTS_KEY,
    queryFn: ({ signal }) => fetchFamilyStudents(signal),
  });
  const items = students.data?.items ?? [];
  const requested = uuidParam(searchParams.get('student'));
  const selected =
    items.find((student) => student.student_id === requested) ?? items[0];
  return { students, items, selected };
}
