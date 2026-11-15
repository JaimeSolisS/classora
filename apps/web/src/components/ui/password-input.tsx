import * as React from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

// #664: a password field with a show/hide toggle. Every prop (React Hook
// Form's `register`, `id`, `autoComplete`, `aria-*`, `ref`) reaches the
// <input>. The field starts hidden on every mount and goes back to hidden
// when its form submits, valid or not, so a password is never left shown
// and password managers see a password field. Visibility is local state
// only: never a store, the URL, browser storage or a log.

type PasswordInputProps = Omit<React.ComponentProps<'input'>, 'type'>;

function PasswordInput({
  className,
  id,
  ref,
  disabled,
  readOnly,
  ...props
}: PasswordInputProps) {
  const { t } = useTranslation();
  const fallbackId = React.useId();
  const inputId = id ?? fallbackId;
  const [shown, setShown] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  function setRefs(node: HTMLInputElement | null) {
    inputRef.current = node;
    if (typeof ref === 'function') {
      ref(node);
    } else if (ref) {
      ref.current = node;
    }
  }

  React.useEffect(() => {
    const form = inputRef.current?.form;
    if (!form) {
      return undefined;
    }
    function hide() {
      // Hidden before the submission is handled, not only on the next render.
      if (inputRef.current) {
        inputRef.current.type = 'password';
      }
      setShown(false);
    }
    form.addEventListener('submit', hide);
    return () => form.removeEventListener('submit', hide);
  }, []);

  return (
    <div data-slot="password-input" className="relative w-full">
      <Input
        id={inputId}
        type={shown ? 'text' : 'password'}
        disabled={disabled}
        readOnly={readOnly}
        className={cn('pr-10', className)}
        ref={setRefs}
        {...props}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={shown ? t('common.hidePassword') : t('common.showPassword')}
        aria-controls={inputId}
        disabled={disabled === true || readOnly === true}
        onClick={() => setShown((current) => !current)}
        className="absolute inset-y-0 right-0.5 my-auto text-muted-foreground hover:text-foreground"
      >
        {shown ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
      </Button>
    </div>
  );
}

export { PasswordInput, type PasswordInputProps };
