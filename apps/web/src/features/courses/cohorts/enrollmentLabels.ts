import { useTranslation } from 'react-i18next';
import type { EnrollDialogLabels } from '@/features/groups/EnrollDialog';
import type { WithdrawDialogLabels } from '@/features/groups/WithdrawDialog';

// The cohort's texts for the shared #234 enroll and withdraw dialogs
// (#245 B, C), for the page and its stories.

export function useEnrollLabels(cohortName: string): EnrollDialogLabels {
  const { t } = useTranslation();
  return {
    title: t('courses.cohorts.enrollment.dialog.title'),
    description: t('courses.cohorts.enrollment.dialog.description', {
      cohort: cohortName,
    }),
    studentHelp: t('courses.cohorts.enrollment.dialog.studentHelp'),
    alreadyEnrolled: (name) =>
      t('courses.cohorts.enrollment.dialog.alreadyEnrolled', { name }),
    submit: t('courses.cohorts.enrollment.dialog.submit'),
    submitting: t('courses.cohorts.enrollment.dialog.submitting'),
    conflictsHeading: (count) =>
      t('courses.cohorts.enrollment.dialog.conflictsHeading', { count }),
    overrideAction: t('courses.cohorts.enrollment.dialog.overrideAction'),
    overrideDescription: t(
      'courses.cohorts.enrollment.dialog.overrideDescription',
    ),
    noDateHeading: t('courses.cohorts.enrollment.dialog.wholeCohort'),
    notYetLabel: t('courses.cohorts.enrollment.dialog.notYetLabel'),
    notYet: t('courses.cohorts.enrollment.dialog.notYet'),
    hardLabel: t('courses.cohorts.enrollment.dialog.hardLabel'),
    needsOverrideLabel: t('courses.cohorts.enrollment.dialog.needsOverride'),
  };
}

export function useWithdrawLabels(cohortName: string): WithdrawDialogLabels {
  const { t } = useTranslation();
  return {
    title: t('courses.cohorts.enrollment.withdrawDialog.title'),
    description: (name) =>
      t('courses.cohorts.enrollment.withdrawDialog.description', {
        name,
        cohort: cohortName,
      }),
    help: t('courses.cohorts.enrollment.withdrawDialog.help'),
    reason: t('courses.cohorts.enrollment.withdrawDialog.reason'),
    reasonTooLong: t('courses.cohorts.enrollment.withdrawDialog.reasonTooLong'),
    keep: t('courses.cohorts.enrollment.withdrawDialog.keep'),
    submit: t('courses.cohorts.enrollment.withdrawDialog.submit'),
    submitting: t('courses.cohorts.enrollment.withdrawDialog.submitting'),
  };
}
