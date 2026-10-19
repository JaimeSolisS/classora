import type { ReactNode } from 'react';
import type { Session } from '@/features/auth/session';
import { AuthContext } from '@/features/auth/useSession';

// Storybook only: a fixed auth context, so app components that read the
// session render without the AuthProvider (and without any request).
export function StoryAuth({
  session,
  children,
}: {
  session: Session;
  children: ReactNode;
}) {
  return (
    <AuthContext
      value={{
        session,
        refetchSession: () => {},
        signIn: () => {},
        signOut: () => new Promise<void>(() => {}),
      }}
    >
      {children}
    </AuthContext>
  );
}
