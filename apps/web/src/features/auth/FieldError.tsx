// The error text under a form field (design-system "Forms"): the field sets
// aria-invalid="true" and points at this element with aria-describedby.
export function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) {
    return null;
  }
  return (
    <p id={id} className="text-sm text-destructive">
      {message}
    </p>
  );
}
