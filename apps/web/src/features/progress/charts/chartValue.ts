// Chart geometry only (#322 B5): the y position of a point from the API's
// decimal text ("66.67" → 66.67). Never shown as text: labels, tooltips,
// summaries and tables use the API string itself (lib/decimal's
// decimalText). `null` (no value that month) leaves a gap.

export function chartValue(text: string | null): number | null {
  if (text === null) {
    return null;
  }
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}
