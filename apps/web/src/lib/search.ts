// The type-ahead rule of the combobox's local filtering (#545 A6): the
// same term rule as the API's search (families/search.py `every_term`),
// without its typo tolerance. Case and accents are ignored ("jose" finds
// "José", "nunez" finds "Ñúñez"); every typed term must appear somewhere
// in the text, in any order.

/** The API's length limit of a search (`SearchText`, 100 characters). */
export const SEARCH_MAX_LENGTH = 100;
/** The API's word limit of a search (`SearchText`, at most 5 words). */
export const SEARCH_MAX_TERMS = 5;

/** Lower case, with the accents and other combining marks removed. */
export function normalizeSearchText(text: string): string {
  return text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
}

/** The normalized whitespace terms of `query` (at most SEARCH_MAX_TERMS). */
export function searchTerms(query: string): string[] {
  return normalizeSearchText(query)
    .split(/\s+/)
    .filter((term) => term !== '')
    .slice(0, SEARCH_MAX_TERMS);
}

/** Whether every term of `query` is a substring of `text` (blank: true). */
export function matchesSearch(text: string, query: string): boolean {
  const haystack = normalizeSearchText(text);
  return searchTerms(query).every((term) => haystack.includes(term));
}
