import type { ReactNode } from 'react';
import { FieldError } from '@/features/auth/FieldError';

// One labelled field of an expense dialog (M7-12b D): the label, the control,
// an optional hint and counter, and the field's error, tied to the control
// with aria-describedby and aria-invalid.

export interface FieldControlProps {
  id: string;
  'aria-describedby': string | undefined;
  'aria-invalid': true | undefined;
}

export function FormField({
  id,
  label,
  hint,
  counter,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  counter?: string;
  /** The translated message, if any. */
  error?: string;
  children: (control: FieldControlProps) => ReactNode;
}) {
  const ids = {
    hint: `${id}-hint`,
    counter: `${id}-counter`,
    error: `${id}-error`,
  };
  const describedBy =
    [
      hint ? ids.hint : undefined,
      counter ? ids.counter : undefined,
      error ? ids.error : undefined,
    ]
      .filter(Boolean)
      .join(' ') || undefined;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children({
        id,
        'aria-describedby': describedBy,
        'aria-invalid': error ? true : undefined,
      })}
      {hint ? (
        <p id={ids.hint} className="text-sm text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {counter ? (
        <p id={ids.counter} className="text-sm text-muted-foreground">
          {counter}
        </p>
      ) : null}
      <FieldError id={ids.error} message={error} />
    </div>
  );
}
