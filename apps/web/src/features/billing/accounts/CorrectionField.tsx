import type { ReactNode } from 'react';
import { FieldError } from '@/features/auth/FieldError';
import { useFieldMessage } from '@/features/scheduling/series/hooks';

// One labelled field of the correction dialogs (#386): the label, the
// control (given the ids to wire), an optional help text and the error.
// The control sets aria-invalid and aria-describedby from `wiring`.

export interface FieldWiring {
  id: string;
  'aria-invalid': true | undefined;
  'aria-describedby': string | undefined;
}

export function CorrectionField({
  id,
  label,
  help,
  error,
  className = 'flex min-w-0 flex-col gap-2',
  children,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  /** A message key (`errors:` prefix for the errors namespace). */
  error?: string;
  className?: string;
  children: (wiring: FieldWiring) => ReactNode;
}) {
  const message = useFieldMessage();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy =
    [help ? helpId : undefined, error ? errorId : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  return (
    <div className={className}>
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children({
        id,
        'aria-invalid': error ? true : undefined,
        'aria-describedby': describedBy,
      })}
      {help ? (
        <p id={helpId} className="text-sm text-muted-foreground">
          {help}
        </p>
      ) : null}
      <FieldError id={errorId} message={message(error)} />
    </div>
  );
}
