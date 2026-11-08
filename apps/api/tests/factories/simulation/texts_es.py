"""The Spanish text pool of the simulation (#543 E11): fixed, curated
strings. Same keys as ``texts_en`` (a unit test checks the parity). All
names, addresses and texts are invented."""

from typing import Final

TEXTS: Final[dict[str, object]] = {
    "center_name": "Centro de Tutorías Horizonte",
    "contact_email": "contacto@example.com",
    "address": "Calle de los Almendros 45, Colonia Jardines, 01000 Ciudad de México",
    "family_name": "Familia {surnames}",
    "academic_period": "Ciclo escolar 2026–2027",
    "education_levels": {
        "elementary": "Primaria",
        "middle_school": "Secundaria",
        "high_school": "Preparatoria",
        "university": "Universidad",
    },
    "expense_categories": {
        "rent": "Renta",
        "utilities": "Servicios",
        "supplies": "Insumos",
        "equipment": "Equipo",
        "maintenance": "Mantenimiento",
    },
    "subjects": {
        "math_e": "Matemáticas",
        "reading_e": "Lectura",
        "english_e": "Inglés",
        "math_m": "Matemáticas",
        "english_m": "Inglés",
        "science_m": "Ciencias",
        "math_h": "Matemáticas",
        "physics_h": "Física",
        "chemistry_h": "Química",
        "english_h": "Inglés",
        "calculus_u": "Cálculo",
        "programming_u": "Programación",
    },
    "classrooms": {
        "room1": "Aula 1",
        "room2": "Aula 2",
        "room3": "Aula 3",
        "room4": "Sala de grupos",
        "room5": "Laboratorio",
    },
    "closures": {
        "independence": "Día de la Independencia",
        "day_of_the_dead": "Día de Muertos",
        "revolution": "Día de la Revolución",
    },
    "contact_relationships": [
        "Tía",
        "Tío",
        "Abuela",
        "Abuelo",
        "Vecina",
        "Madrina",
        "Hermana mayor",
    ],
    "professional_experience": [
        (
            "Licenciada en Matemáticas con seis años de experiencia en primaria y "
            "secundaria. Prepara a sus alumnos para exámenes de admisión."
        ),
        (
            "Ingeniero físico con cuatro años como tutor de preparatoria. Le gusta "
            "explicar con experimentos sencillos."
        ),
        (
            "Maestro en Ciencias de la Computación; enseña cálculo y programación a"
            " estudiantes universitarios desde hace cinco años."
        ),
        (
            "Licenciado en Educación Secundaria con especialidad en matemáticas. "
            "Ocho años frente a grupo y tres como tutor particular."
        ),
        (
            "Licenciada en Enseñanza del Inglés, certificada C2. Diez años de "
            "experiencia con niños y adolescentes."
        ),
        (
            "Pedagoga especializada en lectoescritura y matemáticas para los "
            "primeros grados de primaria."
        ),
        (
            "Actuario con experiencia docente en cálculo diferencial e integral y "
            "en introducción a la programación."
        ),
        (
            "Bióloga con maestría en educación; da clases de ciencias y matemáticas"
            " en secundaria desde hace siete años."
        ),
        (
            "Químico farmacobiólogo; asesora a estudiantes de preparatoria en "
            "física, química y matemáticas."
        ),
        (
            "Traductora y profesora de inglés con nueve años de experiencia; "
            "también imparte talleres de lectura."
        ),
        (
            "Licenciado en Matemáticas Aplicadas; combina la tutoría de primaria y "
            "secundaria con la preparación para olimpiadas."
        ),
        (
            "Ingeniera química con tres años de experiencia como tutora de ciencias"
            " y de programación básica."
        ),
    ],
    "rejection_reasons": [
        "Falta el comprobante de estudios de la materia.",
    ],
    "revocation_reasons": [
        "La materia se reasignó tras la evaluación docente de octubre.",
    ],
    "exception_notes": [
        "Cita médica",
        "Trámite personal",
        "Congreso de docentes",
        "Horas extra para regularización",
        "Viaje familiar",
        "Taller de actualización",
        "Sábado de repaso",
    ],
}
