import { useTranslation } from 'react-i18next';

/** "Secundaria" or "Secundaria (inactive)". */
export function useLevelLabel() {
  const { t } = useTranslation();
  return (level: { name: string; is_active: boolean } | null | undefined) =>
    level
      ? level.is_active
        ? level.name
        : `${level.name} ${t('students.fields.levelInactive')}`
      : '';
}
