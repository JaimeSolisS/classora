import { useTranslation } from 'react-i18next';

// A live character counter under a text field (#308): the trimmed length
// and the limit, in the destructive colour (and over the limit) once it is
// too long. Linked to its field with aria-describedby.

export function Counter({
  id,
  value,
  max,
}: {
  id: string;
  value: string;
  max: number;
}) {
  const { t } = useTranslation();
  const length = value.trim().length;
  return (
    <p
      id={id}
      className={
        length > max
          ? 'text-sm text-destructive'
          : 'text-sm text-muted-foreground'
      }
    >
      {t('academics.counter', { length, max })}
    </p>
  );
}
