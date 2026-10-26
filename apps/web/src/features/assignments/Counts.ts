import { useTranslation } from 'react-i18next';
import type { AssignmentCounts } from './api';

// An assignment's counts as text (#313 C1, C2): "{n} handed in · {n}
// graded · {n} missing · {n} pending, of {n} students", plus "· {n} late"
// when any work came in late. Every part has its plural forms.

export function useCountsText() {
  const { t } = useTranslation();
  return (counts: AssignmentCounts): string => {
    const parts = [
      t('assignments.counts.submitted', { count: counts.submitted }),
      t('assignments.counts.graded', { count: counts.graded }),
      t('assignments.counts.missing', { count: counts.missing }),
      t('assignments.counts.pending', { count: counts.pending }),
    ].join(' · ');
    const text = t('assignments.counts.of', {
      parts,
      count: counts.recipients,
    });
    return counts.late > 0
      ? `${text} · ${t('assignments.counts.late', { count: counts.late })}`
      : text;
  };
}
