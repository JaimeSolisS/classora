import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import type { FamilyReference } from './api';

// The "Family" line of the student and guardian details (#172 H4): a link
// to the family, only when the server sent `family` (staff with
// guardian.view; a guardian on their own profile).

export function FamilyLine({ family }: { family: FamilyReference | null }) {
  const { t } = useTranslation();
  if (!family) {
    return null;
  }
  return (
    <p data-slot="family-line" className="text-sm break-words">
      <span className="font-medium">{t('families.familyLine')}:</span>{' '}
      <Link to={`/families/${family.id}`} className={TEXT_LINK_CLASS}>
        {family.name}
      </Link>
    </p>
  );
}
