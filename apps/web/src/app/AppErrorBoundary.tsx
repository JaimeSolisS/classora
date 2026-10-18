import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { i18n } from '@/i18n';
import { reportError } from './errors';

interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  hasError: boolean;
}

// Last-resort boundary around the providers and the router: an error outside
// the router (for example in a provider) shows this fallback instead of a
// blank page. The fallback uses only tokens and plain elements (no router or
// query hooks) and reads its text from the i18n instance directly. It never
// shows the error message or stack.
function AppErrorFallback() {
  return (
    <main className="flex min-h-svh flex-col items-start gap-4 p-4">
      <h1 className="text-2xl font-semibold">{i18n.t('appError.title')}</h1>
      <p className="text-muted-foreground">{i18n.t('appError.message')}</p>
      <Button type="button" onClick={() => window.location.reload()}>
        {i18n.t('appError.reload')}
      </Button>
    </main>
  );
}

export class AppErrorBoundary extends Component<
  AppErrorBoundaryProps,
  AppErrorBoundaryState
> {
  state: AppErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    reportError(error, { source: 'app', componentStack: info.componentStack });
  }

  render(): ReactNode {
    return this.state.hasError ? <AppErrorFallback /> : this.props.children;
  }
}
