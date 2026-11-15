import { JOSE } from '@/features/students/fixtures';
import type {
  ConsentOverview,
  ConsentRecordItem,
  LoginAccountOption,
  StudentLogin,
} from './api';

// Fixtures of the consent and login sections (#35 I4, J3): synthetic people
// only, the #34 guardians of José (Laura, active with a login; Ramón,
// inactive) plus Marta (active, no login). No request.

const AT = '2026-10-19T15:00:00Z';
const LATER = '2026-10-20T16:30:00Z';

export const LAURA_GUARDIAN_ID = '00000000-0000-4000-8000-0000000f0001';
export const RAMON_GUARDIAN_ID = '00000000-0000-4000-8000-0000000f0002';
export const MARTA_GUARDIAN_ID = '00000000-0000-4000-8000-0000000f0003';

export const LAURA_OPTION = {
  guardian_id: LAURA_GUARDIAN_ID,
  given_names: 'Laura',
  family_names: 'Ñúñez Prieto',
  relationship: 'mother',
  is_active: true,
} as const;
export const RAMON_OPTION = {
  guardian_id: RAMON_GUARDIAN_ID,
  given_names: 'Ramón',
  family_names: 'Pérez Solís',
  relationship: 'grandparent',
  is_active: false,
} as const;
export const MARTA_OPTION = {
  guardian_id: MARTA_GUARDIAN_ID,
  given_names: 'Marta',
  family_names: 'Solís Ruiz',
  relationship: 'legal_guardian',
  is_active: true,
} as const;

export const LAURA_LOGIN_CONSENT: ConsentRecordItem = {
  id: '00000000-0000-4000-8000-0000000c0001',
  scope: 'student_login',
  guardian_id: LAURA_GUARDIAN_ID,
  guardian_given_names: 'Laura',
  guardian_family_names: 'Ñúñez Prieto',
  method: 'in_app',
  notice_version: 1,
  granted_at: LATER,
  revoked_at: null,
  revocation_cause: null,
  can_revoke: true,
  suspends_login_on_revoke: true,
};

export const MARTA_REVOKED_CONSENT: ConsentRecordItem = {
  id: '00000000-0000-4000-8000-0000000c0002',
  scope: 'student_login',
  guardian_id: MARTA_GUARDIAN_ID,
  guardian_given_names: 'Marta',
  guardian_family_names: 'Solís Ruiz',
  method: 'paper',
  notice_version: 1,
  granted_at: AT,
  revoked_at: LATER,
  revocation_cause: 'revoked',
  can_revoke: false,
  suspends_login_on_revoke: false,
};

/** Staff on José (a minor with an active login and records). */
export const STAFF_MINOR: ConsentOverview = {
  student_id: JOSE.id,
  is_minor: true,
  login_state: 'active',
  current_notice_version: 1,
  guardians: [LAURA_OPTION, RAMON_OPTION, MARTA_OPTION],
  consents: [LAURA_LOGIN_CONSENT, MARTA_REVOKED_CONSENT],
  can_record: ['student_login'],
};

/** Staff on an adult (no records yet, no login). */
export const STAFF_ADULT: ConsentOverview = {
  ...STAFF_MINOR,
  is_minor: false,
  login_state: null,
  consents: [],
  can_record: ['guardian_academic_access'],
};

/** Laura (guardian) on José before she allows the login. */
export const GUARDIAN_NOT_GIVEN: ConsentOverview = {
  student_id: JOSE.id,
  is_minor: true,
  login_state: 'pending',
  current_notice_version: 1,
  guardians: [LAURA_OPTION],
  consents: [],
  can_record: ['student_login'],
};

/** Laura (guardian) on José after she allowed it. */
export const GUARDIAN_GIVEN: ConsentOverview = {
  ...GUARDIAN_NOT_GIVEN,
  login_state: 'active',
  consents: [LAURA_LOGIN_CONSENT],
};

export const LAURA_ACCESS: ConsentRecordItem = {
  ...LAURA_LOGIN_CONSENT,
  id: '00000000-0000-4000-8000-0000000c0003',
  scope: 'guardian_academic_access',
  suspends_login_on_revoke: false,
};

/** An adult student viewing their own consents (two active guardians). */
export const STUDENT_ADULT: ConsentOverview = {
  student_id: JOSE.id,
  is_minor: false,
  login_state: 'active',
  current_notice_version: 2,
  guardians: [LAURA_OPTION, MARTA_OPTION],
  consents: [LAURA_ACCESS],
  can_record: ['guardian_academic_access'],
};

export const LINKED_LOGIN: StudentLogin = {
  user_id: '00000000-0000-4000-8000-0000000a0001',
  username: 'jose.nunez',
  display_name: 'José Ñúñez',
  state: 'pending',
};

export const LOGIN_OPTIONS: LoginAccountOption[] = [
  {
    id: LINKED_LOGIN.user_id,
    username: 'jose.nunez',
    display_name: 'José Ñúñez',
    state: 'pending',
    roles: ['student'],
    created_at: AT,
  },
  {
    id: '00000000-0000-4000-8000-0000000a0002',
    username: 'jose.old',
    display_name: 'José (anterior)',
    state: 'archived',
    roles: ['student'],
    created_at: AT,
  },
];
