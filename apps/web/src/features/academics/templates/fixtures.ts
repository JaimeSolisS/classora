import type { SubjectOption } from '@/features/courses/api';
import type { EvaluationTemplate } from './api';

// Sample templates for the template pages' tests and stories (#308 G1,
// G2).

export const PHYSICS_ID = '00000000-0000-4000-8000-000000308a01';
export const MATH_ID = '00000000-0000-4000-8000-000000308a02';
export const TEMPLATE_ID = '00000000-0000-4000-8000-000000308b01';

export const SUBJECT_OPTIONS: SubjectOption[] = [
  { id: PHYSICS_ID, name: 'Física', levelName: 'Secundaria' },
  { id: MATH_ID, name: 'Matemáticas', levelName: 'Secundaria' },
];

const AT = '2026-10-20T15:00:00Z';

export const TEMPLATE: EvaluationTemplate = {
  id: TEMPLATE_ID,
  version: 3,
  name: 'Diagnóstico de física',
  kind: 'diagnostic',
  subject: { id: PHYSICS_ID, name: 'Física' },
  description: 'Primer diagnóstico del ciclo.',
  is_active: true,
  criteria: [
    {
      position: 1,
      title: 'Comprensión',
      description: 'Explica los conceptos.',
      max_score: '10.00',
    },
    {
      position: 2,
      title: 'Resolución',
      description: null,
      max_score: '10.00',
    },
  ],
  created_at: AT,
  updated_at: AT,
};

export const ANY_SUBJECT: EvaluationTemplate = {
  ...TEMPLATE,
  id: '00000000-0000-4000-8000-000000308b02',
  name: 'Seguimiento general',
  kind: 'ongoing',
  subject: null,
  description: null,
  criteria: [
    {
      position: 1,
      title: 'Participación',
      description: null,
      max_score: '5.00',
    },
  ],
};

export const INACTIVE: EvaluationTemplate = {
  ...TEMPLATE,
  id: '00000000-0000-4000-8000-000000308b03',
  name: 'Diagnóstico anterior',
  is_active: false,
};

/** A template with the most criteria a template can have (20). */
export const TWENTY: EvaluationTemplate = {
  ...TEMPLATE,
  id: '00000000-0000-4000-8000-000000308b04',
  name: 'Rúbrica completa',
  criteria: Array.from({ length: 20 }, (_, index) => ({
    position: index + 1,
    title: `Criterio ${index + 1}`,
    description: null,
    max_score: '5.00',
  })),
};

export const TEMPLATES: EvaluationTemplate[] = [
  TEMPLATE,
  ANY_SUBJECT,
  INACTIVE,
];
