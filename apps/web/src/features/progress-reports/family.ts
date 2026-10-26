import { useQuery } from '@tanstack/react-query';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { FAMILY_STUDENTS_KEY } from '@/features/academics/history/api';
import { uuidParam } from '@/features/academics/history/urlState';
import {
  fetchFamilyStudents,
  type FamilyStudent,
} from '@/features/my-schedule/api';

// The family pages' student (#315 F4, F5; #308's picker): GET
// /schedule/family-students, and the ?student= choice. An unknown or
// foreign value falls back to the first student, with no request for it.

export function useFamilyStudent() {
  const [searchParams, setSearchParams] = useSearchParams();
  const students = useQuery({
    queryKey: FAMILY_STUDENTS_KEY,
    queryFn: ({ signal }) => fetchFamilyStudents(signal),
  });
  const items = students.data?.items ?? [];
  const requested = uuidParam(searchParams.get('student'));
  const selected =
    items.find((student) => student.student_id === requested) ?? items[0];
  return {
    students,
    items,
    selected,
    choose: (studentId: string) =>
      setSearchParams(new URLSearchParams({ student: studentId })),
  };
}

/** "My reports" for the student themselves, otherwise the child's name
 * (marked when an adult hasn't shared their records). */
export function pickerLabel(
  student: FamilyStudent,
  t: TFunction<'common'>,
): string {
  const name =
    student.relation === 'self'
      ? t('progressReports.family.mine')
      : student.display_name;
  return student.schedule_access === 'consent_required'
    ? t('mySchedule.whose.consentRequired', { name })
    : name;
}

export function usePickerLabel() {
  const { t } = useTranslation();
  return (student: FamilyStudent) => pickerLabel(student, t);
}
