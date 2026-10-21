import { readFileSync } from 'node:fs';

// UI text read from the web app's own translation files, so tests never
// repeat a string. The configured locale is es-MX → the `es` catalogs; the
// English test reads `en`.

type Common = {
  apiStatus: { live: string };
  theme: {
    label: string;
    options: Record<'light' | 'dark' | 'system', string>;
  };
  language: { label: string; options: Record<'es' | 'en', string> };
  notFound: { title: string };
  nav: {
    home: string;
    users: string;
    invitations: string;
    passwordResets: string;
    approvals: string;
    catalog: string;
    settings: string;
    students: string;
    guardians: string;
    families: string;
    tutors: string;
    schedule: string;
  };
  roles: Record<string, string>;
  scheduling: {
    title: string;
    fields: Record<
      'student' | 'subject' | 'tutor' | 'classroom' | 'date' | 'start' | 'end',
      string
    >;
    submit: string;
    success: { heading: string; unassigned: string; another: string };
    severity: { hard: string; soft: string };
    conflicts: { student_overlap: string };
  };
  delegations: Record<string, { label: string; description: string }>;
  auth: {
    login: {
      title: string;
      username: string;
      password: string;
      submit: string;
      invitationRedeemed: string;
      passwordReset: string;
      requestAccount: string;
    };
    reauth: {
      title: string;
      password: string;
      confirm: string;
      cancel: string;
    };
    userMenu: { signedInAs: string; signOut: string };
  };
  invitations: {
    title: string;
    form: {
      username: string;
      displayName: string;
      role: string;
      submit: string;
    };
    result: { link: string };
  };
  oneTimeLink: { onceWarning: string };
  passwordResets: {
    title: string;
    form: { username: string; submit: string; self: string };
    result: { link: string };
  };
  reset: { title: string };
  account: {
    password: {
      title: string;
      link: string;
      current: string;
      new: string;
      confirm: string;
      submit: string;
      done: string;
    };
  };
  register: {
    title: string;
    role: { legend: string; guardian: string; student: string };
    displayName: string;
    username: string;
    password: string;
    confirmPassword: string;
    submit: string;
    sent: string;
    backToLogin: string;
  };
  users: {
    title: string;
    list: { search: string };
    status: {
      suspend: string;
      archive: string;
      reactivate: string;
      suspendTitle: string;
      reactivateTitle: string;
    };
    roles: { title: string; save: string; saved: string };
    delegations: { title: string; save: string; saved: string };
  };
  userStates: Record<'pending' | 'active' | 'suspended' | 'archived', string>;
  tutors: {
    title: string;
    list: { search: string };
    profile: {
      displayName: string;
      experience: string;
      edit: string;
      saved: string;
    };
    form: { phone: string; experience: string; save: string };
    qualifications: {
      title: string;
      teachingTitle: string;
      empty: string;
      statuses: Record<
        'pending' | 'approved' | 'rejected' | 'cancelled' | 'revoked',
        string
      >;
      request: string;
      levelLabel: string;
      subjectLabel: string;
      send: string;
      close: string;
      requested: string;
      revokeFor: string;
      revokeReason: string;
      revokeConfirm: string;
      revoked: string;
    };
    availability: {
      title: string;
      save: string;
      saved: string;
      exceptionsEmpty: string;
      kinds: Record<'unavailable' | 'available', string>;
      add: string;
      delete: string;
      deleteConfirm: string;
      deleted: string;
      added: string;
      kindLabel: string;
      startsOn: string;
      endsOn: string;
      noteLabel: string;
      send: string;
      settings: {
        daily: string;
        weekly: string;
        break: string;
        noLimit: string;
        noBreak: string;
        hoursLabel: string;
        minutesLabel: string;
        save: string;
        saved: string;
      };
    };
  };
  catalog: {
    title: string;
    levels: {
      add: string;
      edit: string;
      name: string;
      inactiveNote: string;
      back: string;
    };
    subjects: { title: string; add: string; edit: string };
    status: { active: string; inactive: string };
    actions: {
      editNamed: string;
      deactivate: string;
      save: string;
      cancel: string;
    };
    saved: string;
    tabs: {
      label: string;
      levels: string;
      periods: string;
      closures: string;
      classrooms: string;
    };
    range: { name: string; type: string; startsOn: string; endsOn: string };
    periods: { title: string; add: string };
    closures: { title: string; add: string; edit: string; year: string };
    periodTypes: Record<'school_year' | 'semester' | 'term', string>;
    closureTypes: Record<'holiday' | 'closure' | 'vacation', string>;
    classrooms: {
      title: string;
      add: string;
      edit: string;
      name: string;
      capacity: string;
      validation: { capacityRange: string };
    };
  };
  weeklyWindows: {
    add: string;
    weekdayLabel: string;
    startsAtLabel: string;
    endsAtLabel: string;
    validation: { overlap: string };
  };
  students: {
    title: string;
    add: string;
    edit: string;
    notFound: string;
    save: string;
    saved: string;
    list: {
      search: string;
      status: string;
      statusAll: string;
      empty: string;
      noResults: string;
    };
    fields: {
      givenNames: string;
      familyNames: string;
      birthdate: string;
      level: string;
    };
    contacts: {
      nameLabel: string;
      relationshipLabel: string;
      phoneLabel: string;
      add: string;
      moveUpNamed: string;
      removeNamed: string;
    };
    badge: { minor: string; adult: string };
    status: { active: string; inactive: string };
    deactivate: { action: string; title: string };
    guardians: {
      title: string;
      link: string;
      linkAction: string;
      linkTitle: string;
      guardian: string;
      relationship: string;
      unlink: string;
      unlinkNamed: string;
      unlinkTitle: string;
      linked: string;
      unlinked: string;
    };
    login: { title: string };
  };
  consent: {
    title: string;
    active: string;
    allowLogin: string;
    loginGiven: string;
    withdraw: string;
    share: string;
    stopSharing: string;
    confirm: string;
    revokeTitle: string;
    revokeWarning: string;
    recorded: string;
    revokedStatus: string;
  };
  guardians: {
    title: string;
    add: string;
    save: string;
    saved: string;
    fields: {
      givenNames: string;
      familyNames: string;
      phone: string;
    };
    account: { search: string };
    list: { empty: string };
  };
  families: {
    title: string;
    add: string;
    save: string;
    saved: string;
    cancel: string;
    notFound: string;
    fields: { name: string };
    addAction: string;
    remove: string;
    removeNamed: string;
    removeTitle: string;
    students: {
      title: string;
      add: string;
      addTitle: string;
      student: string;
      conflict: string;
      added: string;
      empty: string;
    };
    guardians: {
      title: string;
      add: string;
      addTitle: string;
      guardian: string;
      added: string;
      removed: string;
    };
  };
  settings: {
    title: string;
    save: string;
    saved: string;
    brand: { centerName: string };
    hours: { openOn: string; dayOpensAt: string; dayClosesAt: string };
    timeZone: { label: string };
    weekdays: Record<
      | 'monday'
      | 'tuesday'
      | 'wednesday'
      | 'thursday'
      | 'friday'
      | 'saturday'
      | 'sunday',
      string
    >;
    tabs: { label: string; general: string; schedulingPolicies: string };
    schedulingPolicies: {
      title: string;
      current: { title: string };
      rules: { changeNotice: string; response: string };
      units: { hours: string };
      form: { save: string };
      fields: { changeNoticeHours: string };
      takesEffect: { date: string; dateLabel: string };
      saved: string;
      deleted: string;
      upcoming: { title: string; empty: string; deleteFor: string };
      deleteDialog: { title: string; confirm: string };
    };
  };
  relationships: Record<string, string>;
  approvals: {
    title: string;
    actions: {
      approve: string;
      approveFor: string;
      reject: string;
      rejectFor: string;
    };
    summary: { qualification: string };
    dialog: { reasonRequiredLabel: string };
    done: { approved: string; rejected: string };
  };
  invite: {
    title: string;
    username: string;
    newPassword: string;
    confirmPassword: string;
    submit: string;
    incomplete: string;
  };
  privacyNotice: {
    title: string;
    version: string;
    intro: string;
    textLabel: string;
    accept: string;
    accepted: string;
    changed: string;
    menu: string;
  };
  notifications: {
    bell: string;
    bellUnread: string;
    title: string;
    markAll: string;
    empty: string;
    unread: string;
    read: string;
    unknown: string;
    types: {
      dev_demo: string;
      staff_qualification_approved: string;
      staff_qualification_rejected: string;
      staff_qualification_revoked: string;
    };
  };
};

type Errors = {
  invalid_credentials: string;
  token_invalid: string;
  current_password_invalid: string;
  name_taken: string;
  education_level_has_active_subjects: string;
  period_overlap: string;
  too_many_streams: string;
  guardian_consent_required: string;
  reauth_required: string;
  qualification_exists: string;
};

function read<T>(path: string): T {
  return JSON.parse(
    readFileSync(
      new URL(`../../../apps/web/src/i18n/locales/${path}`, import.meta.url),
      'utf8',
    ),
  ) as T;
}

export const common = read<Common>('es/common.json');
export const commonEn = read<Common>('en/common.json');
export const errors = read<Errors>('es/errors.json');

/** A catalog text with `{{key}}` placeholders filled in. */
export function fill(
  text: string,
  values: Record<string, string | number>,
): string {
  return text.replace(/\{\{(\w+)\}\}/g, (_, key: string) =>
    String(values[key]),
  );
}

/** `Signed in as {{name}}` with the name filled in. */
export function signedInAs(catalog: Common, name: string): string {
  return catalog.auth.userMenu.signedInAs.replace('{{name}}', name);
}
