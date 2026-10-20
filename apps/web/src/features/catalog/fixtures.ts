import type {
  AcademicPeriod,
  Classroom,
  Closure,
  EducationLevel,
  Subject,
} from './api';

// Fixtures of the catalog pages (#32 I3, J4): the seeded levels plus an
// inactive one, and subjects. No request.

const AT = '2026-10-19T15:00:00Z';

function level(
  index: number,
  name: string,
  sortOrder: number,
  overrides: Partial<EducationLevel> = {},
): EducationLevel {
  return {
    id: `00000000-0000-4000-8000-00000000c0${String(index).padStart(2, '0')}`,
    name,
    sort_order: sortOrder,
    is_active: true,
    active_subject_count: 0,
    version: 1,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  };
}

export const PRIMARIA = level(1, 'Primaria', 10, { active_subject_count: 3 });
export const SECUNDARIA = level(2, 'Secundaria', 20, {
  active_subject_count: 1,
  version: 4,
});
export const PREPARATORIA = level(3, 'Preparatoria', 30);
export const POSGRADO = level(4, 'Posgrado', 50, {
  is_active: false,
  version: 2,
});

export const LEVELS: EducationLevel[] = [
  PRIMARIA,
  SECUNDARIA,
  PREPARATORIA,
  POSGRADO,
];

export function subject(
  index: number,
  name: string,
  of: EducationLevel = SECUNDARIA,
  overrides: Partial<Subject> = {},
): Subject {
  return {
    id: `00000000-0000-4000-8000-00000000d0${String(index).padStart(2, '0')}`,
    education_level_id: of.id,
    education_level_name: of.name,
    name,
    is_active: true,
    version: 1,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  };
}

export const ALGEBRA = subject(1, 'Álgebra');
export const HISTORIA = subject(2, 'Historia', SECUNDARIA, {
  is_active: false,
  version: 3,
});
export const POSGRADO_SUBJECT = subject(3, 'Métodos', POSGRADO, {
  is_active: false,
});

// --- Academic periods and closures (#38 E4, E6) ---------------------------

export function period(
  index: number,
  name: string,
  periodType: AcademicPeriod['period_type'],
  startsOn: string,
  endsOn: string,
  overrides: Partial<AcademicPeriod> = {},
): AcademicPeriod {
  return {
    id: `00000000-0000-4000-8000-00000000e0${String(index).padStart(2, '0')}`,
    name,
    period_type: periodType,
    starts_on: startsOn,
    ends_on: endsOn,
    is_active: true,
    version: 1,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  };
}

export const SCHOOL_YEAR = period(
  1,
  'Ciclo 2026-2027',
  'school_year',
  '2026-08-01',
  '2027-07-15',
  { version: 3 },
);
export const SEMESTER = period(
  2,
  'Semestre otoño 2026',
  'semester',
  '2026-08-01',
  '2026-12-20',
);
export const OLD_YEAR = period(
  3,
  'Ciclo 2025-2026',
  'school_year',
  '2025-08-01',
  '2026-07-15',
  { is_active: false, version: 2 },
);
export const PERIODS: AcademicPeriod[] = [SCHOOL_YEAR, SEMESTER, OLD_YEAR];

export function closure(
  index: number,
  name: string,
  closureType: Closure['closure_type'],
  startsOn: string,
  endsOn: string = startsOn,
  overrides: Partial<Closure> = {},
): Closure {
  return {
    id: `00000000-0000-4000-8000-00000000f0${String(index).padStart(2, '0')}`,
    name,
    closure_type: closureType,
    starts_on: startsOn,
    ends_on: endsOn,
    is_active: true,
    version: 1,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  };
}

export const INDEPENDENCE = closure(
  1,
  'Independencia',
  'holiday',
  '2026-09-16',
);
export const WINTER = closure(
  2,
  'Vacaciones de invierno',
  'vacation',
  '2026-12-19',
  '2027-01-06',
  { version: 2 },
);
export const WORKS = closure(
  3,
  'Obras',
  'closure',
  '2026-11-02',
  '2026-11-03',
  {
    is_active: false,
  },
);
export const CLOSURES: Closure[] = [INDEPENDENCE, WORKS, WINTER];

// --- Classrooms (#39 D5, D6) -----------------------------------------------

export function classroom(
  index: number,
  name: string,
  capacity: number,
  availability: Classroom['availability'] = [],
  overrides: Partial<Classroom> = {},
): Classroom {
  return {
    id: `00000000-0000-4000-8000-00000000f0${String(index).padStart(2, '0')}`,
    name,
    capacity,
    is_active: true,
    availability,
    version: 1,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  };
}

const WEEKDAYS_9_TO_8 = [1, 2, 3, 4, 5].map((weekday) => ({
  weekday,
  starts_at: '09:00',
  ends_at: '20:00',
}));

export const AULA_1 = classroom(1, 'Aula 1', 12, [
  ...WEEKDAYS_9_TO_8,
  { weekday: 6, starts_at: '09:00', ends_at: '14:00' },
]);
export const LABORATORIO = classroom(2, 'Laboratorio', 20, [], { version: 3 });
export const BODEGA = classroom(
  3,
  'Sala de juntas',
  6,
  [
    { weekday: 1, starts_at: '09:00', ends_at: '12:00' },
    { weekday: 1, starts_at: '12:00', ends_at: '14:00' },
  ],
  { is_active: false, version: 2 },
);

export const CLASSROOMS: Classroom[] = [AULA_1, LABORATORIO, BODEGA];
