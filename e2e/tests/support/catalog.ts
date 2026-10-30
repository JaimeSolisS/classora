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
    courses: string;
    offers: string;
    groups: string;
    sessionRequests: string;
    // #67 G8: the phone menu and My schedule.
    toggle: string;
    mySchedule: string;
    // #62b E1: My change requests.
    changeRequests: string;
    // #310 G3: the family's learning plans.
    learningPlans: string;
    // #322 G3: the family's progress page.
    progress: string;
    // #323: the family's courses ("Mis cursos").
    myCourses: string;
    // #363 F4: the price catalog.
    billing: string;
  };
  // #363 F4, F5: the price catalog; #366 E3: the Unbilled and Settings tabs;
  // #367 G3, G4: discounts and custom prices; #374 H3: tuition plans;
  // #377 G3: packages; #379 G3: the family accounts and payments.
  billing: {
    tabs: Record<
      | 'label'
      | 'accounts'
      | 'overdue'
      | 'prices'
      | 'discounts'
      | 'customPrices'
      | 'tuitionPlans'
      | 'packages'
      | 'unbilled'
      | 'settings',
      string
    >;
    accounts: {
      list: { title: string; search: string };
      page: { title: string };
      summary: { title: string; accountCredit: string };
      charges: { title: string; empty: string };
      ledger: { title: string; empty: string };
      payments: { title: string; empty: string };
      record: {
        action: string;
        title: string;
        submit: string;
        fields: Record<'amount' | 'method', string>;
        modes: Record<'oldest_first' | 'explicit' | 'none', string>;
      };
      credit: { action: string };
      // #98 H3: the payment row's receipt link.
      receipt: { download: string; downloadFor: string; recorded: string };
    };
    // #382 F3: the overdue accounts and the due-date dialogs.
    overdue: {
      title: string;
      filters: { minDays: string };
    };
    dueDates: {
      offset: {
        editLabel: string;
        title: string;
        field: string;
        save: string;
        label_other: string;
      };
      change: {
        action: string;
        title: string;
        newDate: string;
        reason: string;
        submit: string;
      };
    };
    paymentMethods: Record<'cash' | 'transfer' | 'other', string>;
    packages: {
      units: { sessionsBalance_other: string };
      status: Record<'active' | 'exhausted' | 'expired', string>;
      nav: Record<'sold' | 'products', string>;
      fields: Record<
        | 'name'
        | 'quantitySessions'
        | 'validity'
        | 'amount'
        | 'student'
        | 'product',
        string
      >;
      unitNames: Record<'sessions' | 'minutes', string>;
      products: { title: string; add: string; create: string };
      product: { activate: string; deactivate: string };
      sold: {
        title: string;
        filters: { searchStudent: string };
      };
      sell: { action: string; title: string; submit: string };
      history: { kinds: Record<'grant', string> };
    };
    tuition: {
      title: string;
      add: string;
      create: string;
      back: string;
      view: string;
      delete: string;
      emptyFiltered: string;
      openEnded: string;
      untilResumed: string;
      columns: Record<'endsOn', string>;
      fields: Record<'student' | 'price' | 'startsOn', string>;
      status: Record<'scheduled' | 'active' | 'paused' | 'ended', string>;
      filters: { status: string; searchStudent: string };
      periodStates: Record<'paused', string>;
      // #379 G3: a paid charge; #382 F3: an overdue one.
      chargeStatus: Record<'pending' | 'paid' | 'overdue', string>;
      pauses: { empty: string };
      pause: Record<'action' | 'title' | 'submit' | 'from', string>;
      resume: Record<'action' | 'title' | 'submit', string>;
      end: Record<'action' | 'title' | 'submit' | 'lastDay', string>;
    };
    targets: {
      kinds: Record<'student' | 'family', string>;
      search: Record<'student' | 'family', string>;
    };
    discounts: {
      title: string;
      add: string;
      save: string;
      back: string;
      deactivate: string;
      kinds: Record<
        'discount' | 'scholarship' | 'sibling' | 'promotion',
        string
      >;
      audience: Record<'all' | 'assigned', string>;
      status: Record<'current' | 'upcoming' | 'expired' | 'inactive', string>;
      fields: Record<
        'name' | 'percent' | 'minFamilyStudents' | 'subjects',
        string
      >;
      filters: { q: string };
      assign: { title: string; submit: string };
      assignments: { title: string; remove: string; removeFor: string };
    };
    customPrices: {
      title: string;
      add: string;
      delete: string;
      deleteFor: string;
      filters: { status: string };
      fields: Record<'service' | 'amount' | 'effectiveFrom', string>;
      overlap: Record<'student' | 'family', string>;
    };
    settings: {
      title: string;
      change: string;
      save: string;
      saved: string;
      status: Record<'off' | 'on', string>;
      dialog: {
        title: string;
        startsOn: string;
        modes: Record<'on' | 'off', string>;
      };
    };
    unbilled: {
      title: string;
      emptyOn: string;
      filters: { reason: string };
    };
    prices: {
      title: string;
      add: string;
      save: string;
      cancel: string;
      back: string;
      viewExisting: string;
      fields: Record<
        | 'classType'
        | 'pricingModel'
        | 'periodFrequency'
        | 'subject'
        | 'duration'
        | 'effectiveFrom'
        | 'amount',
        string
      >;
      status: Record<'current' | 'upcoming' | 'past', string>;
      versions: { title: string };
      schedule: { title: string; submit: string };
      deleteDialog: { confirm: string };
    };
  };
  // #62b E1: the request dialog and My change requests.
  changeRequests: {
    title: string;
    action: Record<'request' | 'pending' | 'sent', string>;
    dialog: Record<'title' | 'cancel' | 'next' | 'send', string>;
    preview: Record<'heading' | 'estimate', string>;
    list: Record<'statusText' | 'approvedException', string>;
  };
  mySchedule: {
    title: string;
    regionLabel: string;
    tutor: string;
    states: Record<
      'offered' | 'confirmed' | 'completed' | 'cancelled' | 'skipped_closure',
      string
    >;
    whose: { label: string };
  };
  // #79 I8: the tutor's notes page and the family's dialog section.
  notes: {
    title: string;
    open: string;
    badges: Record<'internal' | 'shared', string>;
    composer: Record<'label' | 'save' | 'saved', string>;
    actions: Record<'share', string>;
    share: Record<'title' | 'confirm', string>;
    family: Record<'title', string>;
  };
  attendance: {
    title: string;
    statuses: Record<'present' | 'late' | 'absent', string>;
    save: string;
    saved: string;
    take: string;
    history_one: string;
    reason: string;
    section: {
      heading: string;
      correctedBadge: string;
      correctFor: string;
      correctedNotice: string;
    };
    correctDialog: {
      title: string;
      status: string;
      reason: string;
      submit: string;
    };
  };
  closeout: {
    title: string;
    closeOut: string;
    next: string;
    actualMinutes: string;
    minutesOfScheduled: string;
    fields: Record<
      'topics' | 'performance' | 'homework' | 'next_steps',
      string
    >;
    submit: string;
    submitted: string;
    submittedNotice: string;
    section: { heading: string };
    /** #78. */
    review: {
      correct: string;
      approve: string;
      approvedShort: string;
      correctDialog: { reason: string; submit: string };
      approveDialog: { minutes: string; submit: string };
      history: { minutes: string };
      /** #305: the owner's "Request changes". */
      requestChanges: string;
      changesRequested: string;
      requestDialog: { reason: string; submit: string };
    };
    /** #301b: a reviewer's closeout on the tutor's behalf. */
    onBehalf: Record<
      | 'action'
      | 'attendance'
      | 'complete'
      | 'reason'
      | 'submit'
      | 'marker'
      | 'reasonLine',
      string
    >;
    /** #305: the tutor's status views and edit modes. */
    tutorView: Record<
      | 'changesRequested'
      | 'underReview'
      | 'resubmit'
      | 'resubmitSubmit'
      | 'resubmittedNotice'
      | 'badge'
      | 'fix',
      string
    >;
  };
  sessionRequests: {
    status: Record<'pending' | 'confirmed' | 'rejected' | 'withdrawn', string>;
    new: {
      title: string;
      subject: string;
      length: string;
      nextWeek: string;
      slot: string;
      requestedSlot: string;
      sent: string;
      viewRequests: string;
    };
    confirm: { note: string; send: string; notBooking: string };
    list: { title: string; requestOne: string; confirmedHelp: string };
    minutes: string;
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
    conflicts: {
      student_overlap: string;
      group_capacity_exceeded: string;
      cohort_capacity_exceeded: string;
    };
    suggestions: { heading: string; slot: string };
    override: {
      action: string;
      title: string;
      reason: string;
      submit: string;
      scheduledWith: string;
    };
    calendar: {
      title: string;
      regionLabel: string;
      scheduleSession: string;
      views: Record<'day' | 'week' | 'month' | 'list', string>;
      filters: { student: string };
      // #58: rescheduling.
      move: Record<
        'moved' | 'movedWithOverride' | 'notSaved' | 'overrideAction' | 'close',
        string
      >;
      reschedule: Record<'action' | 'title' | 'submit', string>;
    };
    states: Record<'offered' | 'confirmed' | 'unassigned', string> &
      Record<string, string>;
    // #51: suggested tutors (#60 uses them on the review page).
    tutorSuggestions: { button: string; chooseTutor: string };
    // #62 G1 (used by #62b E1): the staff review of a change request.
    changeRequest: {
      title: string;
      statuses: Record<
        'pending' | 'approved' | 'rejected' | 'withdrawn',
        string
      >;
      reason: string;
      makeupUntil: string;
      approvedExceptionText: string;
      decide: { exception: string };
      exception: { submit: string };
    };
    // #60: the staff review of a session request.
    requestReview: {
      title: string;
      confirmedTo: string;
      openSession: string;
      confirm: { submit: string };
      reject: Record<'open' | 'description' | 'reason' | 'submit', string>;
    };
    markers: Record<string, { label: string }>;
    // #301b: the overdue closeouts page and its Schedule link.
    overdue: Record<'title' | 'link' | 'linkCount' | 'open', string>;
    session: {
      title: string;
      history: string;
      noTutor: string;
      seriesOfferedTo: string;
      reassignment: { title: string };
    };
    offer: {
      submit: string;
      series: Record<'series' | 'session', string>;
    };
    // #55: substitutions and cancellations.
    substitution: Record<
      | 'heading'
      | 'tutor'
      | 'reason'
      | 'submit'
      | 'overrideAction'
      | 'historyHeading'
      | 'pendingText'
      | 'withdraw'
      | 'withdrawTitle',
      string
    >;
    cancel: Record<'action' | 'title' | 'reason' | 'submit' | 'banner', string>;
    // #54b: weekly series.
    series: {
      repeats: string;
      startsOn: string;
      count: string;
      untilDate: string;
      submit: string;
      endsOptions: Record<'never' | 'until' | 'count', string>;
      success: { heading: string; openSeries: string };
      scope: Record<'following' | 'fromDate' | 'all', string>;
      occurrence: { title: string; submit: string };
      edit: { title: string; submit: string };
      cancel: {
        title: string;
        reason: string;
        submit: string;
        done_other: string;
      };
      summary: { moved_other: string };
      // #236: series overrides.
      override: { scheduledWith_other: string };
      page: Record<
        | 'title'
        | 'ended'
        | 'edited'
        | 'editOccurrence'
        | 'editSeries'
        | 'cancelSeries'
        | 'continuesFrom'
        | 'overridden',
        string
      >;
    };
  };
  // #234: tutoring groups.
  groups: {
    title: string;
    new: string;
    fields: Record<'name' | 'max', string>;
    capacity: { taken: string };
    page: { enroll: string };
    next: { heading: string };
    roster: Record<
      'overridden' | 'withdrawOf' | 'withdrawn_one' | 'noReason',
      string
    >;
    conflicts: { wholeGroup: string };
    enroll: Record<
      'title' | 'student' | 'submit' | 'overrideAction' | 'doneOverride',
      string
    >;
    withdraw: Record<'title' | 'reason' | 'submit' | 'done', string>;
    create: Record<
      'title' | 'addStudent' | 'remove' | 'submit' | 'done_other',
      string
    >;
  };
  offers: {
    title: string;
    tabs: Record<'label' | 'pending' | 'answered', string>;
    status: Record<
      'pending' | 'accepted' | 'declined' | 'expired' | 'withdrawn',
      string
    >;
    card: { note: string };
    accept: { title: string; submit: string; action: string };
    series: { scheduled_other: string; acceptTitle: string };
    decline: {
      title: string;
      note: string;
      submit: string;
      action: string;
    };
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
      // #63: changes that affect scheduled classes.
      changes: { sent_one: string; pendingTitle: string };
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
    weekdays: Record<'1' | '2' | '3' | '4' | '5' | '6' | '7', string>;
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
    summary: {
      qualification: string;
      reassignmentDeclined: string;
      sessionRequest: string;
    };
    reassignment: { openSession: string };
    closeout: {
      review: string;
      kinds: Record<'first' | 'resubmission' | 'correction', string>;
      /** #301b. */
      onBehalf: string;
    };
    availabilityChange: { affectedTitle: string };
    // #315 H3: the inbox link of a progress report.
    progressReport: { reviewFor: string };
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
      scheduling_session_cancelled: string;
      scheduling_session_request_confirmed: string;
      scheduling_session_request_rejected: string;
      // #62b E1: the requester's approval.
      scheduling_change_request_approved_cancellation_makeup: string;
      // #305: the tutor's closeout sent back.
      academics_closeout_changes_requested: string;
      // #79 I8: a note the tutor shared with the family.
      academics_learning_note_published: string;
      // #308 G3: a finalized evaluation (the guardian's bell).
      academics_evaluation_finalized: string;
      // #310 G3: an activated learning plan (the guardian's bell).
      academics_learning_plan_activated: string;
      // #313 G3: set and handed-in homework (the student's and tutor's bells).
      academics_assignment_set: string;
      academics_assignment_submitted: string;
      // #315 H3: the tutor's and the guardian's progress report bells.
      academics_progress_report_changes_requested: string;
      academics_progress_report_approved: string;
    };
  };
  // #308 G3: the evaluation pages.
  academics: {
    // #310 G3: a subject option with its level.
    subjectOption: string;
    kinds: Record<'diagnostic' | 'ongoing', string>;
    entry: Record<'historyOf' | 'evaluateName', string>;
    templates: Record<'title', string>;
    evaluations: {
      templateOption: string;
      fields: Record<'template' | 'score' | 'feedback', string>;
      actions: Record<'saveDraft' | 'finalize', string>;
      notices: Record<'draftSaved' | 'finalized', string>;
      status: Record<'draft' | 'final', string>;
      finalize: Record<'title' | 'description' | 'confirm', string>;
      read: Record<'total', string>;
    };
    history: {
      title: string;
      newEvaluation: string;
      views: Record<'grouped' | 'list', string>;
    };
  };
  // #310 G3: the learning plan pages.
  learningPlans: {
    new: string;
    statusOf: string;
    progress_other: string;
    status: Record<'draft' | 'active', string>;
    actions: Record<'activate' | 'edit', string>;
    activate: Record<'title' | 'description' | 'confirm', string>;
    notices: Record<'draftSaved' | 'activated', string>;
    entry: Record<'linkOf', string>;
    family: Record<'whose', string>;
    editor: Record<
      | 'subject'
      | 'title'
      | 'objectiveTitle'
      | 'objectiveNamed'
      | 'milestoneTitle'
      | 'milestoneUnnamed'
      | 'dueOn'
      | 'addMilestoneTo'
      | 'remove'
      | 'planCounter'
      | 'save',
      string
    >;
  };
  // #315 H3: the progress report pages.
  progressReports: {
    new: string;
    writtenBy: string;
    kinds: Record<'on_demand', string>;
    status: Record<
      'draft' | 'submitted' | 'changesRequested' | 'approved' | 'retracted',
      string
    >;
    fields: Record<'summary', string>;
    entry: Record<'linkOf', string>;
    actions: Record<
      'save' | 'submit' | 'resubmit' | 'approve' | 'requestChanges' | 'retract',
      string
    >;
    notices: Record<'saved' | 'submitted' | 'approved', string>;
    start: Record<'submit', string>;
    student: Record<'noApproved', string>;
    submit: Record<'title' | 'titleResubmit', string>;
    approve: Record<'title', string>;
    changes: Record<'title', string>;
    retract: Record<'title' | 'warning', string>;
    review: Record<'reason', string>;
    facts: { evaluations: Record<'subjectAverage_one', string> };
    family: Record<'reportMissing', string>;
  };
  // #322 G3: the progress pages.
  progress: {
    percent: string;
    family: Record<'title', string>;
    tiles: Record<
      | 'attendance'
      | 'homeworkGrades'
      | 'gradedCount_one'
      | 'gradedCount_other'
      | 'attended_one',
      string
    >;
    chart: Record<'table', string>;
    grades: Record<'heading', string>;
    log: { heading: string; fields: Record<'topics', string> };
  };
  // #313 G3: the homework pages.
  assignments: {
    states: Record<'pending' | 'submitted' | 'graded', string>;
    scoreWithPercent: string;
    entry: Record<'setHomework', string>;
    form: Record<
      'title' | 'instructions' | 'dueOn' | 'maxScore' | 'save',
      string
    >;
    notices: Record<'created_one' | 'handedIn', string>;
    actions: Record<'gradeFor', string>;
    grade: Record<'title' | 'score' | 'feedbackOptional' | 'save', string>;
    submit: Record<'text' | 'link' | 'handIn', string>;
  };
  courses: {
    // #243: the Courses tab bar and the cohort pages.
    tabs: Record<'label' | 'templates' | 'cohorts', string>;
    cohorts: Record<'title' | 'new', string> & {
      fields: Record<'name' | 'template' | 'max', string>;
      create: Record<'title' | 'submit' | 'done', string>;
      syllabus: Record<'heading', string>;
      next: Record<'heading', string>;
      tutor: Record<'heading', string>;
      assignment: Record<'noTutor', string>;
      // #245: the Students panel and the enrollment dialogs.
      enrollment: Record<
        | 'heading'
        | 'capacity'
        | 'full'
        | 'enroll'
        | 'overridden'
        | 'showWithdrawn'
        | 'withdrawOf'
        | 'withdrawn_one',
        string
      > & {
        dialog: Record<
          'title' | 'submit' | 'overrideAction' | 'wholeCohort' | 'done',
          string
        >;
        withdrawDialog: Record<'title' | 'reason' | 'submit' | 'done', string>;
      };
      // #323: course completion on the cohort page.
      completion: Record<
        | 'heading'
        | 'myHeading'
        | 'detailsFor'
        | 'assessmentsCount'
        | 'modulesCount',
        string
      > & {
        status: Record<'in_progress' | 'completed' | 'not_completed', string>;
        criterion: Record<'pending', string>;
        outcome: Record<'not_evaluated', string>;
        attendance: Record<'ofHeld', string>;
        detail: Record<'required', string>;
      };
    };
    templates: Record<
      | 'title'
      | 'new'
      | 'newTitle'
      | 'name'
      | 'sessionCount'
      | 'sessionMinutes'
      | 'subjectOption'
      | 'moduleTitleLabel'
      | 'moduleHeading'
      | 'plannedSessionsLabel'
      | 'addModule'
      | 'moveModuleUpLabel'
      | 'addAssessment'
      | 'assessmentTitleLabel'
      | 'assessmentModuleLabel'
      | 'minAttendance'
      | 'save'
      | 'saved'
      | 'deactivate'
      | 'statusFilter',
      string
    > & {
      statusOptions: Record<'active' | 'inactive' | 'all', string>;
      validation: Record<'plannedExceedTotal' | 'nameTaken', string>;
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
  service_price_exists: string;
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
