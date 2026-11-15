import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { Language } from '@/i18n/language';
import type { ThemeChoice } from '@/theme/theme';

// The signed-in user's stored language and theme (#36):
// SessionUser.preferences (read with login, /auth/me and the password change)
// and PATCH /api/v1/auth/me/preferences (write).

/** `null` means the user never stored the value. */
export type UserPreferences = components['schemas']['UserPreferences'];
/** One or both values; a field that is not sent stays as it is. */
export type PreferencesUpdate = components['schemas']['UserPreferencesUpdate'];

// F4: `tsc` fails when the API's unions and the web lists differ
// (LANGUAGES in i18n/language.ts, THEME_CHOICES in theme/theme.ts).
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Assert<T extends true> = T;
export type LanguagesMatchTheApi = Assert<
  Same<NonNullable<UserPreferences['language']>, Language>
>;
export type ThemesMatchTheApi = Assert<
  Same<NonNullable<UserPreferences['theme']>, ThemeChoice>
>;

/**
 * Saves `body` for the signed-in user and returns both stored values. A 401
 * ends the session and `csrf_failed` is retried once, as for every call
 * (`apiCall`, #28).
 */
export function savePreferences(
  body: PreferencesUpdate,
): Promise<UserPreferences> {
  return apiCall(() => api.PATCH('/api/v1/auth/me/preferences', { body }));
}
