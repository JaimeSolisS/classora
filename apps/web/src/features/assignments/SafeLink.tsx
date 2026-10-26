import { useTranslation } from 'react-i18next';
import { isSafeHttpUrl } from './rules';

// A user-supplied link (#313 C6): an anchor only when the #82 A3 rule
// accepts it (absolute http or https with a host, no user-info, no
// whitespace or control characters, at most 2000 characters), opened in a
// new tab with rel="noopener noreferrer nofollow", the full URL as its
// visible text (wrapping anywhere) and a visually hidden "(opens in a new
// tab)". Anything else is plain text, never an anchor.

export function SafeLink({ url }: { url: string }) {
  const { t } = useTranslation();
  if (!isSafeHttpUrl(url)) {
    return <span className="min-w-0 break-all">{url}</span>;
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="min-w-0 break-all underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      {url}
      <span className="sr-only"> {t('assignments.newTab')}</span>
    </a>
  );
}
