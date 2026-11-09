import { useTranslation } from 'react-i18next';
import type { AccountOption, GuardianAccount } from './api';

/**
 * The account picker's option text and the chosen account's chip (#34 I2):
 * "username · Display Name · State", plus "(archived)" for an archived
 * account.
 */
export function useAccountLabel() {
  const { t } = useTranslation();
  return (account: AccountOption | GuardianAccount) => {
    const base = `${account.username} · ${account.display_name} · ${t(`userStates.${account.state}`)}`;
    return account.state === 'archived'
      ? `${base} ${t('guardians.account.archived')}`
      : base;
  };
}

/**
 * What an account picker's local filter matches (#548 A2): the username and
 * the display name, the fields the server's `q` searches, not the state,
 * role or "(archived)" words of the label.
 */
export function accountSearchText(
  account: AccountOption | GuardianAccount,
): string {
  return `${account.username} ${account.display_name}`;
}
