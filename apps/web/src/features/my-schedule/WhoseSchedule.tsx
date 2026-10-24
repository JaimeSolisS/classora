import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { SELECT_CLASS } from '@/features/students/styles';
import { entryLabel, type WhoseEntry } from './whose';

// "Whose schedule" (#67 E2): a labelled select above the calendar when
// there is more than one entry, plain text for a single one.

export function WhoseSchedule({
  entries,
  selected,
  onChange,
}: {
  entries: readonly WhoseEntry[];
  selected: WhoseEntry;
  onChange: (key: string) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  if (entries.length === 1) {
    return (
      <p className="text-sm">
        <span className="font-medium">{t('mySchedule.whose.label')}</span>{' '}
        {entryLabel(selected, t)}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {t('mySchedule.whose.label')}
      </label>
      <select
        id={id}
        value={selected.key}
        onChange={(event) => onChange(event.target.value)}
        className={`${SELECT_CLASS} w-full max-w-sm`}
      >
        {entries.map((entry) => (
          <option key={entry.key} value={entry.key}>
            {entry.kind === 'student' &&
            entry.student.schedule_access === 'consent_required'
              ? t('mySchedule.whose.consentRequired', {
                  name: entryLabel(entry, t),
                })
              : entryLabel(entry, t)}
          </option>
        ))}
      </select>
    </div>
  );
}
