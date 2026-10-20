import type { TutorDetail, TutorListItem, TutorListPage } from './api';

// Sample tutor profiles for the tests and stories (#43). Placeholder
// contact data only.

export const TUTOR_ID = '00000000-0000-4000-8000-0000000000c1';

export const TUTOR_ITEM: TutorListItem = {
  user_id: TUTOR_ID,
  username: 'lucia.tutora',
  display_name: 'Lucía Méndez',
  state: 'active',
  email: 'lucia@example.com',
  phone: '55 1234 5678',
};

export const TUTOR_ITEMS: TutorListItem[] = [
  TUTOR_ITEM,
  {
    user_id: '00000000-0000-4000-8000-0000000000c2',
    username: 'pablo.tutor',
    display_name: 'Pablo Ortega',
    state: 'suspended',
    email: null,
    phone: '55 8765 4321',
  },
  {
    user_id: '00000000-0000-4000-8000-0000000000c3',
    username: 'invited.tutor',
    display_name: 'Invited Tutor',
    state: 'pending',
    email: 'invited@example.com',
    phone: null,
  },
];

export function tutorPage(
  items: TutorListItem[] = TUTOR_ITEMS,
  total = items.length,
  offset = 0,
): TutorListPage {
  return { items, total, limit: 25, offset };
}

/** As an Owner or Administrator sees it: every field editable. */
export const TUTOR_AS_ADMIN: TutorDetail = {
  ...TUTOR_ITEM,
  professional_experience:
    'Eight years teaching mathematics.\nOlympiad coach since 2021.',
  version: 2,
  editable_fields: [
    'display_name',
    'email',
    'phone',
    'professional_experience',
  ],
};

/** As the tutor sees their own profile: no display name. */
export const TUTOR_AS_SELF: TutorDetail = {
  ...TUTOR_AS_ADMIN,
  editable_fields: ['email', 'phone', 'professional_experience'],
};

/** As an Academic Coordinator sees it: read only. */
export const TUTOR_READ_ONLY: TutorDetail = {
  ...TUTOR_AS_ADMIN,
  editable_fields: [],
};

/** A tutor without a stored profile (version 0). */
export const NEW_TUTOR: TutorDetail = {
  ...TUTOR_ITEM,
  email: null,
  professional_experience: null,
  version: 0,
  editable_fields: [
    'display_name',
    'email',
    'phone',
    'professional_experience',
  ],
};
