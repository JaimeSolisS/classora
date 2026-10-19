import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';

// One-time links (#29 invitations, #30 password resets) carry their token in
// the URL fragment (`/invite#token=…`), so it never reaches a server log, a
// proxy log or a Referer header. The page reads it once, keeps it in
// component state only, and removes the fragment from the address bar right
// away (a history replace that keeps the path and query). After a reload the
// fragment is gone, so the page has no token.

/** The `token` parameter of a URL fragment (`#token=…`), or null. */
export function tokenFromFragment(hash: string): string | null {
  const fragment = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!fragment) {
    return null;
  }
  const token = new URLSearchParams(fragment).get('token');
  return token ? token : null;
}

/**
 * The token of the page's URL fragment, read once on mount. Removes the
 * fragment from the URL (and from the router's location) at once. Never
 * stored anywhere else.
 */
export function useFragmentToken(): string | null {
  const location = useLocation();
  const navigate = useNavigate();
  const [token] = useState(() => tokenFromFragment(location.hash));
  const hasFragment = location.hash !== '';

  useEffect(() => {
    if (hasFragment) {
      // A replace without router state: Back never returns to the fragment.
      void navigate(
        { pathname: location.pathname, search: location.search, hash: '' },
        { replace: true },
      );
    }
  }, [hasFragment, location.pathname, location.search, navigate]);

  return token;
}
