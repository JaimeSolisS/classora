# Classora design system

The web app (`apps/web`) uses Tailwind CSS v4 and shadcn/ui components that
read one central set of design tokens. Storybook catalogs every component.
The target is WCAG 2.2 AA (spec §10, architecture §8).

## Tokens

All tokens live in one file, `apps/web/src/styles/tokens.css`, as CSS custom
properties. Light values are on `:root` and dark values on `.dark`. The `@theme`
blocks in the same file turn them into Tailwind utilities (`bg-background`,
`text-foreground`, `border-input`, `rounded-md`, `p-4`, `text-sm`, ...).
Tailwind's default color palette is removed, so only the semantic colors below
exist as utilities. Components and stories never use raw color values or
arbitrary color classes.

### Colors

| Token | Purpose | Light | Dark |
|---|---|---|---|
| `background` | Page background | `#ffffff` | `#020617` |
| `foreground` | Default text | `#0f172a` | `#f8fafc` |
| `card` | Card surface | `#ffffff` | `#0f172a` |
| `card-foreground` | Text on cards | `#0f172a` | `#f8fafc` |
| `popover` | Dialogs, popovers | `#ffffff` | `#0f172a` |
| `popover-foreground` | Text in dialogs and popovers | `#0f172a` | `#f8fafc` |
| `primary` | Primary action, links | `#1d4ed8` | `#60a5fa` |
| `primary-foreground` | Text on primary | `#ffffff` | `#020617` |
| `secondary` | Secondary action | `#f1f5f9` | `#1e293b` |
| `secondary-foreground` | Text on secondary | `#0f172a` | `#f8fafc` |
| `muted` | Subtle surfaces, skeletons | `#f1f5f9` | `#1e293b` |
| `muted-foreground` | Secondary text, captions | `#475569` | `#94a3b8` |
| `accent` | Hover and selected surfaces | `#f1f5f9` | `#1e293b` |
| `accent-foreground` | Text on accent | `#0f172a` | `#f8fafc` |
| `destructive` | Destructive action, error text | `#b91c1c` | `#f87171` |
| `destructive-foreground` | Text on destructive | `#ffffff` | `#020617` |
| `border` | Decorative borders and dividers | `#cbd5e1` | `#334155` |
| `input` | Form control borders | `#64748b` | `#64748b` |
| `ring` | Keyboard focus ring | `#1d4ed8` | `#60a5fa` |

`border` is decorative only. Borders that identify a control use `input`.

### Contrast pairs enforced by tests

`apps/web/src/styles/tokens.test.ts` (part of `make test`) reads the values
from `tokens.css` and checks them in both themes:

- At least 4.5:1 (SC 1.4.3): `foreground`/`background`, `card-foreground`/`card`,
  `popover-foreground`/`popover`, `primary-foreground`/`primary`,
  `secondary-foreground`/`secondary`, `muted-foreground`/`muted`,
  `muted-foreground`/`background`, `accent-foreground`/`accent`,
  `destructive-foreground`/`destructive`, `destructive`/`background` (error
  text), `primary`/`background` (link text).
- At least 3:1 (SC 1.4.11, non-text): `input`/`background` and
  `ring`/`background`.

### Spacing, typography and radius

- **Spacing:** `--spacing: 0.25rem`. Every spacing utility is a multiple of it
  (`p-4` = 1rem).
- **Fonts:** `--font-sans` and `--font-mono` are system font stacks. No web
  fonts are loaded from third-party origins.
- **Font sizes:** `text-xs` 0.75rem, `text-sm` 0.875rem, `text-base` 1rem,
  `text-lg` 1.125rem, `text-xl` 1.25rem, `text-2xl` 1.5rem, `text-3xl`
  1.875rem, each with a matching line height.
- **Radius:** base `--radius: 0.5rem`; `rounded-sm` = radius − 4px,
  `rounded-md` = radius − 2px, `rounded-lg` = radius, `rounded-xl` = radius + 4px.

## Theming

- Three choices: `light`, `dark` and `system`. `system` follows the
  `prefers-color-scheme` media query and updates live when the OS setting
  changes.
- The resolved theme is applied by toggling the `dark` class on `<html>` and
  setting `color-scheme` to match. Tailwind's `dark:` variant follows that
  class, not the media query.
- Switching changes only that class, so the page is never reloaded and the app
  is not remounted.
- The choice is stored in `localStorage` under `classora.theme` and applied in
  `main.tsx` before the first React render.
- Fallback rules: no stored value, an unknown value or a `localStorage` that
  throws all resolve to `system`, with no error. If the browser has no
  `matchMedia`, `system` resolves to light.
- M2-18 moves the preference to the user's profile in the database; the local
  `classora.theme` value is what it migrates.
- Code: `apps/web/src/theme/` (`theme.ts`, `ThemeProvider.tsx`, `use-theme.ts`,
  `ThemeSwitcher.tsx`). The switcher labels come from the translation
  catalogs (see "Text, dates and money").

## Text, dates and money

Rules for every user-visible string, date and amount in `apps/web`
(spec §16, [ADR-0004](adr/0004-money-time-and-date-representation.md)).

### Translated text

- Every user-visible string comes from the react-i18next catalogs in
  Spanish (`es`) and English (`en`). Only the brand name "Classora" may stay a
  literal.
- Catalogs: `apps/web/src/i18n/locales/{es,en}/{common,errors}.json`, bundled
  with the app (no network request, no Suspense fallback). Namespaces:
  `common` (app text, switchers, shared component text; the default) and
  `errors` (error messages).
- Keys are semantic and nested by area (`apiStatus.live`,
  `theme.options.dark`), never the source sentence. Use `{{name}}`
  placeholders for interpolation and i18next plural suffixes (`_one`,
  `_many`, `_other`; Spanish also needs `_many`).
- To add a string: add the key to **both** languages, then use
  `t('key')` (or `t('errors:key')`). Keys are typed from the Spanish catalog,
  so a misspelled key fails `tsc` in `make lint-web`. `make test-web` runs the
  missing-key check (`src/i18n/check.ts`), which fails on a namespace or key
  present in one language only, an empty or non-string value, different
  placeholders, or a missing plural category.
- React escapes interpolated values. Translations are never rendered as raw
  HTML; rich text uses the `Trans` component.
- Language choice: the value stored in `localStorage` under
  `classora.language` (`es` or `en`), else the first `navigator.languages`
  entry whose primary subtag is `es` or `en`, else `es`. Unknown stored
  values and a throwing `localStorage` fall back the same way. It is applied
  in `main.tsx` before the first render; switching updates the text in place
  with no reload or remount. M2-18 migrates `classora.language` to the user's
  profile.
- `<html lang>` always matches the active language (`index.html` ships
  `lang="es"`). The language switcher shows the endonyms "Español" and
  "English", each with its own `lang` attribute.
- Storybook has a `locale` toolbar (`es` by default, `en`). Stories of app
  components (the switchers) render through the catalogs. Sample copy in
  generic component stories (button labels, table rows) is fixture data and
  may stay English.

### Dates and times (`apps/web/src/lib/time.ts`)

- **Instants** (sessions, timestamps) are ISO 8601 strings with an offset or
  `Z`. Show them with `formatInstant(instant, { timeZone, language, style })`
  (`date`, `time`, `dateTime`; Spanish uses 24-hour time). Get their calendar
  date with `toLocalDate(instant, timeZone)`, never from the UTC date.
- **Calendar dates** (`DATE` columns) are `LocalDate` strings, `YYYY-MM-DD`.
  Show them with `formatLocalDate(localDate, { language })`: it never goes
  through `new Date('YYYY-MM-DD')` or a zone conversion, so every viewer sees
  the same day. `todayInZone(timeZone)` gives today's date in a zone.
- **Center time zone:** `DEFAULT_CENTER_TIME_ZONE` (`America/Mexico_City`) is
  the one source until M3-01 replaces it with the business setting. Every
  helper takes the zone as a required argument; nothing uses the browser's
  zone. In components, `useFormatters()` (`src/lib/formatters.ts`) binds the
  active language and the center zone.
- **Local to instant:** `localDateTimeToInstant(localDate, 'HH:mm', timeZone)`
  and `localDayBounds(localDate, timeZone)` (end exclusive, so 23- and 25-hour
  days work). DST follows Temporal's `'compatible'` rule: a time in a
  spring-forward gap moves forward by the gap, and an ambiguous fall-back
  time takes the earlier instant.
- Parsing is strict: an instant without an offset, an impossible date, a
  wrong shape or an unknown zone throws a `RangeError`.
- ESLint bans `getUTCDate`, `getUTCMonth`, `getUTCFullYear` and
  `.toISOString().slice/substring/substr` outside `time.ts` and tests. Unit
  tests run under a non-UTC host zone (`Pacific/Kiritimati`) unless `TZ` is
  set.

### Money (`apps/web/src/lib/money.ts`)

- Amounts are decimal strings from the API (`NUMERIC(14,2)`, MXN), never
  numbers. `formatMoney('1250.00')` gives `$1,250.00` with
  `Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN' })` in both
  UI languages.
- No `Number()`, `parseFloat` or other numeric conversion of an amount, and no
  money arithmetic in the frontend. Input outside `-?\d{1,12}(\.\d{1,2})?`
  throws a `RangeError` (it is never rounded); `-0.00` shows as `$0.00`.

## Accessibility rules for components

- Target WCAG 2.2 AA.
- Text contrast at least 4.5:1; non-text contrast (control borders, focus
  indicators, meaningful icons) at least 3:1. Add any new token pair to the
  contrast test.
- Every interactive element shows a visible focus indicator on keyboard focus
  (`:focus-visible`), drawn with the `ring` token at full opacity.
- Pointer targets are at least 24 × 24 CSS px (SC 2.5.8). The smallest Button
  sizes (`xs`, `icon-xs`) are exactly 24 px.
- Every input has a visible `<label>` associated with it (`htmlFor`/`id`). An
  error sets `aria-invalid="true"` and is linked with `aria-describedby`.
  Placeholders are never the only label.
- Animations respect `prefers-reduced-motion` (`motion-reduce:` utilities on
  the Dialog, Skeleton and Button).
- Content reflows without horizontal scrolling down to 320 CSS px wide
  (tables scroll inside their own container).
- **Skeleton:** the placeholder shapes are `aria-hidden="true"`. The region
  that is loading sets `aria-busy="true"` and contains a visually hidden
  (`sr-only`) loading message.
- **Checkbox** (`components/ui/checkbox.tsx`, #170; Radix from the pinned
  `radix-ui`): every checkbox has a visible `<label htmlFor>`; a group sits
  in a `fieldset` with a `legend`. A disabled box says why with
  `aria-describedby` (for example "You can't change this role."). Its
  `after:` box widens the pointer target to 40 × 32 px (SC 2.5.8).
- Axe runs on every story in both themes (`make test-storybook`) and a
  violation fails the run. No axe rule is disabled globally. A story-level
  rule exception needs a reason comment in the story and a line in the list
  below.

Story-level axe rule exceptions: none.

## Forms

The form stack (architecture §8), set by the login form and the password
dialog (#28, `apps/web/src/features/auth/`):

- React Hook Form with a Zod schema through `zodResolver`. Import `z` from
  `@/lib/forms`, never from `zod` (ESLint enforces it): that module turns on
  Zod's `jitless` mode, because the Content Security Policy has no
  `'unsafe-eval'`.
- Every field has a visible `<label>` linked with `htmlFor`/`id`, and the
  right `autocomplete` token. Placeholders are never the only label.
- Schema messages are catalog keys, translated when shown, so a language
  switch re-renders them without losing typed values.
- A field error sets `aria-invalid="true"` and is linked with
  `aria-describedby` to the error text under the field. On a failed submit,
  focus moves to the first invalid field (`shouldFocusError`).
- Forms set `noValidate` (messages come from the catalogs) and
  `method="post"`, and the submit handler prevents the native submission.
- The submit button is disabled while the request is pending and shows the
  `Spinner` with a visible label; one submit sends one request.
- A request error shows in one `role="alert"` region (`ErrorAlert`) above the
  fields.
- Passwords stay in the form's own state: never in a store, browser storage,
  a log or the TanStack cache (direct calls, not mutations).

## Feedback

Loading and error primitives in `apps/web/src/components/feedback/`
(Storybook: Feedback).

- **`Spinner`:** an inline indicator for a short wait on one control (a
  button that is submitting). It is decorative (`aria-hidden`) unless it gets
  a `label`, which is screen-reader only or visible with `showLabel`. The
  animation stops under `prefers-reduced-motion`.
- **`LoadingState`:** a region whose content is loading, in a `page`
  (heading and text lines) or `table` (rows) layout. It follows the Skeleton
  rule above: `aria-busy="true"`, a visually hidden loading message and
  `aria-hidden` skeleton shapes. Prefer it to a spinner for page content.
- **`ErrorAlert`:** shows a failed request with `role="alert"`, the
  `destructive` tokens and an `aria-hidden` icon. It shows the translated
  message for the error's code and, when there is one, a "Reference" line
  with the request ID in a selectable monospace element that users can quote
  to support. Pass `onRetry` for a query error to show "Try again" (or, with
  `retryLabel`, another action such as "Reload" after a `version_conflict`).
  The server's `title` and `detail` are never shown.
- **`StateBadge`** (`features/users/admin`, #31): a user's state as a short
  text badge; the border style only reinforces the word, so the state never
  depends on colour alone.
- **`CatalogStatusBadge`** (`features/catalog`, #32): "Active" or
  "Inactive" for a catalog record, the same text-first badge.
- **`CatalogItemDialog`** (`features/catalog`, #32): the add/edit dialog of
  a catalog record (name, and for levels an optional position), with
  Deactivate/Reactivate when editing. A `name_taken` error goes on the name
  field; other errors, `version_conflict` with "Reload", go in the dialog's
  alert; focus returns to the button that opened it. Later catalogs reuse it.
- Render errors are caught by the route error page (inside the layout), the
  root error page and the app-level boundary. None of them shows the error
  message or stack.
- Error messages live in the `errors` namespace, keyed by the Problem
  `code`, and are mapped in `apps/web/src/app/errors.ts` (unknown codes fall
  back to `client_error`, `server_error` or `generic`).

## Role-adaptive layouts

Layouts adapt to the role (spec §10, architecture §8). Tailwind's default
breakpoints are used: `sm` 640px, `md` 768px, `lg` 1024px, `xl` 1280px.

- **Admin tools** (owner, administrator, coordinator, finance): desktop-first.
  Design for `lg` and up (dense tables, side navigation, multi-column forms).
  Below `lg` the layout still works: navigation collapses, columns stack and
  wide tables scroll in their container.
- **Tutor, student and guardian views:** mobile-first. Unprefixed utilities
  target phones; `sm` and `md` add columns and spacing for tablets and
  desktops. Primary actions stay reachable with one hand on small screens.
- Every view reflows down to 320 CSS px.

## Adding a component

1. From `apps/web`, add it with the pinned shadcn CLI (never install the CLI as
   a dependency):

   ```bash
   COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack pnpm dlx shadcn@4.22.0 add <component>
   ```

   `apps/web/components.json` holds the settings. The CLI tries to run
   `pnpm add` for the component's packages: any package not already pinned in
   `apps/web/package.json` needs approval and is added by hand with an exact
   version. Check the generated imports: `cn` comes from `@/lib/utils`.
2. Adapt the generated code only as needed to pass lint, type checks and the
   accessibility rules above (for example a full-opacity `ring` focus
   indicator).
3. Use only token colors.
4. Add a `*.stories.tsx` file next to it covering its variants and states. It
   must pass `make test-storybook` in both themes.
