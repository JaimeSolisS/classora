import { ApiError, isSessionEnd } from '@/app/api';
import type { Language } from '@/i18n/language';
import {
  applyTheme,
  readStoredChoice,
  resolveTheme,
  systemPrefersDark,
  writeStoredChoice,
  type ThemeChoice,
} from '@/theme/theme';
import type { PreferencesUpdate, UserPreferences } from './api';

// The account's language and theme in the web app (#36 F2–F5), free of
// React so the auth provider can call it from the session query function
// (before the signed-in page renders) and tests can drive it directly.
//
// - Sign-in (`fromServer` with a new user): the account's non-null values
//   win over the browser's local ones and are applied and stored locally. No
//   request.
// - Later /auth/me responses for the same user apply a value only when it
//   differs from the one last applied, no save of that field is pending and
//   none started since the response was requested.
// - `migrate`: values the account never stored (`null`) are filled with what
//   the browser shows, in one PATCH, at most once per user per page load (and
//   sign-in). A failure is silent; the next sign-in or reload tries again.
// - `change` (a switcher while signed in): applied and stored locally at once,
//   then saved with only that field. Saves run one after another; a response
//   updates the cached session user and is never applied again.
// - A failed save (other than a session end or csrf_failed, which apiCall
//   handles) keeps the local choice and reports a `SaveFailure`; `retry`
//   resends the latest value of the failed fields.

export type PreferenceField = 'language' | 'theme';

const FIELDS: readonly PreferenceField[] = ['language', 'theme'];

export interface SaveFailure {
  error: unknown;
  fields: readonly PreferenceField[];
}

/** The theme provider's choice and setter (`useTheme()`), bound on render. */
export interface ThemeAccess {
  choice: ThemeChoice;
  setChoice: (theme: ThemeChoice) => void;
}

export interface PreferencesDeps {
  /** Applies the language and stores it locally (as the switcher does). */
  applyLanguage: (language: Language) => void;
  /** The language the app shows now. */
  currentLanguage: () => Language;
  /** PATCH /api/v1/auth/me/preferences. */
  save: (body: PreferencesUpdate) => Promise<UserPreferences>;
  /** Writes saved values into the cached session user of `userId`. */
  storeSaved: (userId: string, saved: UserPreferences) => void;
  /** The save failure to show, or null. */
  onFailureChange: (failure: SaveFailure | null) => void;
}

/** A session user's id and stored preferences. */
export interface PreferencesOwner {
  id: string;
  preferences: UserPreferences;
}

/** Change counters, taken before a session request (see `fromServer`). */
export type ChangeSnapshot = Readonly<Record<PreferenceField, number>>;

interface Applied {
  userId: string;
  language: Language | null;
  theme: ThemeChoice | null;
}

/** Whether a failed save shows the alert (apiCall handles the others). */
function reportable(error: unknown): boolean {
  if (isSessionEnd(error)) {
    return false;
  }
  return !(error instanceof ApiError && error.code === 'csrf_failed');
}

export function createPreferencesController(deps: PreferencesDeps) {
  /** The theme provider, bound by `bindTheme` (see the auth provider). */
  let theme: ThemeAccess | null = null;
  /** What the app last applied, and for which user; null when signed out. */
  let applied: Applied | null = null;
  /** Users whose null values were already sent once since sign-in. */
  const migrated = new Set<string>();
  const changes: Record<PreferenceField, number> = { language: 0, theme: 0 };
  const pending: Record<PreferenceField, number> = { language: 0, theme: 0 };
  let queue: Promise<void> = Promise.resolve();
  let failure: SaveFailure | null = null;

  function setFailure(next: SaveFailure | null): void {
    failure = next;
    deps.onFailureChange(next);
  }

  function currentTheme(): ThemeChoice {
    return theme?.choice ?? readStoredChoice();
  }

  function apply(field: PreferenceField, value: string): void {
    if (field === 'language') {
      deps.applyLanguage(value as Language);
      return;
    }
    const choice = value as ThemeChoice;
    if (theme !== null) {
      // Stored and kept by the provider; its value is current from now on.
      theme.setChoice(choice);
      theme = { ...theme, choice };
    } else {
      writeStoredChoice(choice);
    }
    // At once, not on the provider's next render (F2: before the page paints).
    applyTheme(resolveTheme(choice, systemPrefersDark()));
  }

  function current(field: PreferenceField): Language | ThemeChoice {
    return field === 'language' ? deps.currentLanguage() : currentTheme();
  }

  function remember(field: PreferenceField, value: Language | ThemeChoice) {
    if (applied === null) {
      return;
    }
    if (field === 'language') {
      applied.language = value as Language;
    } else {
      applied.theme = value as ThemeChoice;
    }
  }

  /** Queues one save; saves never overlap, so the last choice wins. */
  function enqueue(
    userId: string,
    body: PreferencesUpdate,
    { silent }: { silent: boolean },
  ): Promise<void> {
    const fields = FIELDS.filter((field) => body[field] !== undefined);
    for (const field of fields) {
      pending[field] += 1;
    }
    const run = async () => {
      try {
        const saved = await deps.save(body);
        deps.storeSaved(userId, saved);
        if (failure !== null) {
          const left = failure.fields.filter((f) => !fields.includes(f));
          setFailure(left.length > 0 ? { ...failure, fields: left } : null);
        }
      } catch (error) {
        if (!silent && reportable(error) && applied?.userId === userId) {
          const merged = new Set([...(failure?.fields ?? []), ...fields]);
          setFailure({
            error,
            fields: FIELDS.filter((field) => merged.has(field)),
          });
        }
      } finally {
        for (const field of fields) {
          pending[field] -= 1;
        }
      }
    };
    queue = queue.then(run, run);
    return queue;
  }

  return {
    /** The theme provider's current choice and setter (on every render). */
    bindTheme(access: ThemeAccess): void {
      theme = access;
    },

    /** The change counters, to pass back to `fromServer` with a response. */
    snapshot(): ChangeSnapshot {
      return { ...changes };
    },

    /**
     * A session user from the server: the login or password-change response
     * (no snapshot) or an /auth/me response (with the snapshot taken before
     * the request). Applies values as described above; sends nothing.
     */
    fromServer(user: PreferencesOwner, before?: ChangeSnapshot): void {
      const first = applied === null || applied.userId !== user.id;
      if (first) {
        applied = { userId: user.id, language: null, theme: null };
        if (failure !== null) {
          setFailure(null);
        }
      }
      for (const field of FIELDS) {
        const value = user.preferences[field];
        if (value === null || value === undefined) {
          continue;
        }
        if (!first) {
          const unchanged = value === applied?.[field];
          const saving = pending[field] > 0;
          const stale =
            before !== undefined && before[field] !== changes[field];
          if (unchanged || saving || stale) {
            continue;
          }
        }
        apply(field, value);
        remember(field, value);
      }
    },

    /**
     * Sends the browser's values for the fields the account never stored,
     * once per user per page load (F3). Call it once the session data is in
     * the cache.
     */
    migrate(user: PreferencesOwner): Promise<void> {
      if (migrated.has(user.id) || applied?.userId !== user.id) {
        return Promise.resolve();
      }
      migrated.add(user.id);
      const body: PreferencesUpdate = {};
      for (const field of FIELDS) {
        if (user.preferences[field] === null) {
          const value = current(field);
          remember(field, value);
          if (field === 'language') {
            body.language = value as Language;
          } else {
            body.theme = value as ThemeChoice;
          }
        }
      }
      if (body.language === undefined && body.theme === undefined) {
        return Promise.resolve();
      }
      return enqueue(user.id, body, { silent: true });
    },

    /**
     * A switcher choice: applied and stored locally at once; saved to the
     * account when `userId` is signed in (F4), never when signed out (F6).
     */
    change(
      field: PreferenceField,
      value: Language | ThemeChoice,
      userId: string | null,
    ): Promise<void> {
      apply(field, value);
      changes[field] += 1;
      if (userId === null || applied?.userId !== userId) {
        return Promise.resolve();
      }
      remember(field, value);
      const body: PreferencesUpdate =
        field === 'language'
          ? { language: value as Language }
          : { theme: value as ThemeChoice };
      return enqueue(userId, body, { silent: false });
    },

    /** "Try again": resends the latest value of each failed field. */
    retry(): Promise<void> {
      if (failure === null || applied === null) {
        return Promise.resolve();
      }
      const body: PreferencesUpdate = {};
      for (const field of failure.fields) {
        if (field === 'language') {
          body.language = deps.currentLanguage();
        } else {
          body.theme = currentTheme();
        }
      }
      return enqueue(applied.userId, body, { silent: false });
    },

    /**
     * Signed out: the next sign-in applies the account's values again and,
     * for a value still never stored, tries the migration again.
     */
    signedOut(): void {
      applied = null;
      migrated.clear();
      if (failure !== null) {
        setFailure(null);
      }
    },
  };
}

export type PreferencesController = ReturnType<
  typeof createPreferencesController
>;
