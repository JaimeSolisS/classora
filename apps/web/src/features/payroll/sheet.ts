// A payroll form dialog (#425 G1): a full-screen sheet below 768 px, a
// centred dialog above (the #388 sheet; `sm:max-w-none` replaces the base
// `sm:max-w-md`, which would otherwise win between 640 and 767 px).
export const SHEET_CLASS =
  'overflow-y-auto [&>*]:min-w-0 [&>[data-slot=dialog-close]]:size-11 max-md:top-0 max-md:left-0 max-md:h-dvh max-md:w-full max-md:max-w-none max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none sm:max-w-none md:max-h-[90vh] md:max-w-lg';

/** Native selects at the app's height, 44 px below 768 px. */
export const SELECT_CLASS =
  'h-11 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:opacity-50 md:h-9 md:text-sm';

/** Inputs at 44 px below 768 px. */
export const INPUT_CLASS = 'h-11 md:h-9';
