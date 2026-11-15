import type { PrivacyNotice } from './api';

// Privacy-notice fixtures for tests and stories (#174). Fake texts in the
// shape of migration 0017's version 1: the first paragraph is the draft line.

export const NOTICE_V1: PrivacyNotice = {
  version: 1,
  published_at: '2026-10-18T00:00:00Z',
  body_es:
    'Borrador pendiente de revisión legal.\n\nClassora guarda los datos de las cuentas, los alumnos y las familias, los horarios y la facturación.\n\nEl centro es responsable de estos datos.',
  body_en:
    'Draft pending legal review.\n\nClassora stores account, student and family records, schedules and billing.\n\nThe center is responsible for this data.',
  accepted_at: null,
};

export const NOTICE_V2: PrivacyNotice = {
  version: 2,
  published_at: '2026-11-02T15:00:00Z',
  body_es: 'Borrador v2.\n\nTexto nuevo del aviso.',
  body_en: 'Draft v2.\n\nThe new notice text.',
  accepted_at: null,
};

export const NOTICE_V1_ACCEPTED: PrivacyNotice = {
  ...NOTICE_V1,
  accepted_at: '2026-10-19T16:30:00Z',
};
