// Waits shared by the specs (#252).

/**
 * The first visible element after loading a page in a fresh context (a new
 * browser context, a `goto`, or a navigation to another route). Under
 * machine load, the first pages of a cold dev server (and the session
 * request before a guarded page renders) can take longer than the default
 * 5 s `expect` wait. Later assertions on an already rendered page keep the
 * default. Use it inside a longer test timeout (90 s in the specs that do).
 */
export const FIRST_RENDER_TIMEOUT_MS = 60_000;
