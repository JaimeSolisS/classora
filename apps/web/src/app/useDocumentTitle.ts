import { useEffect } from 'react';

/** The brand name: the only UI text that is not translated. */
export const APP_NAME = 'Classora';

/** `Classora` for the home page, `<page name> · Classora` for the others. */
export function documentTitle(pageName?: string): string {
  return pageName ? `${pageName} · ${APP_NAME}` : APP_NAME;
}

/**
 * Sets the document title for a page. Pass the translated page name (or
 * nothing on the home page); it follows the active language.
 */
export function useDocumentTitle(pageName?: string): void {
  useEffect(() => {
    document.title = documentTitle(pageName);
  }, [pageName]);
}
