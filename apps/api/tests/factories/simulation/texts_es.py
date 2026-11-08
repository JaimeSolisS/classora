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
    # --- HF-01b (#550): the schedule and the courses ---
    "group_names": {
        "g1": "Club de Matemáticas de secundaria",
        "g2": "Conversación en inglés de preparatoria",
        "g3": "Taller de lectura de primaria",
    },
    "course_names": {
        "exam": "Preparación para el examen de ingreso a preparatoria",
        "physics": "Taller de física para la olimpiada",
    },
    "course_descriptions": {
        "exam": (
            "Repaso guiado de aritmética, álgebra y geometría con simulacros del"
            " examen."
        ),
        "physics": (
            "Problemas de mecánica y energía con prácticas sencillas de laboratorio."
        ),
    },
    "course_modules": {
        "exam": [
            "Aritmética y fracciones",
            "Álgebra básica",
            "Geometría y medición",
            "Simulacro final",
        ],
        "physics": [
            "Cinemática",
            "Leyes de Newton",
            "Trabajo y energía",
        ],
    },
    "course_materials": {
        "exam": [
            "Cuadernillo de ejercicios 1",
            "Cuadernillo de ejercicios 2",
            "Juego de geometría",
            "Examen de práctica impreso",
        ],
        "physics": [
            "Hojas de problemas de cinemática",
            "Carritos y poleas del laboratorio",
            "Guía de problemas de energía",
        ],
    },
    "course_assessments": {
        "exam": ["Examen diagnóstico", "Simulacro de examen"],
        "physics": ["Examen de mecánica", "Reporte de práctica"],
    },
    "cohort_names": {
        "exam": "Examen de ingreso, otoño 2026",
        "physics": "Olimpiada de física, grupo de noviembre",
    },
    "closeout_topics": {
        "math_e": [
            "Sumas y restas con reagrupación",
            "Tablas de multiplicar del 6 al 9",
            "Problemas de fracciones con dibujos",
        ],
        "reading_e": [
            "Lectura en voz alta y comprensión de un cuento",
            "Ideas principales de un texto informativo",
            "Vocabulario nuevo y uso del diccionario",
        ],
        "english_e": [
            "Colores, números y saludos en inglés",
            "Verbo to be con oraciones cortas",
            "Canción y vocabulario de animales",
        ],
        "math_m": [
            "Ecuaciones de primer grado",
            "Proporcionalidad y porcentajes",
            "Áreas y perímetros de figuras compuestas",
        ],
        "english_m": [
            "Presente simple y rutinas diarias",
            "Pasado simple con verbos regulares",
            "Comprensión auditiva de un diálogo",
        ],
        "science_m": [
            "Célula y sus partes",
            "Mezclas y métodos de separación",
            "Fuerzas y movimiento en la vida diaria",
        ],
        "math_h": [
            "Funciones lineales y cuadráticas",
            "Trigonometría en triángulos rectángulos",
            "Sistemas de ecuaciones",
        ],
        "physics_h": [
            "Movimiento rectilíneo uniforme",
            "Segunda ley de Newton",
            "Conservación de la energía",
        ],
        "chemistry_h": [
            "Nomenclatura de compuestos inorgánicos",
            "Balanceo de ecuaciones químicas",
            "Estequiometría básica",
        ],
        "english_h": [
            "Presente perfecto en conversación",
            "Redacción de un correo formal",
            "Lectura de un artículo y resumen oral",
        ],
        "calculus_u": [
            "Límites y continuidad",
            "Derivadas por regla de la cadena",
            "Integrales por sustitución",
        ],
        "programming_u": [
            "Ciclos y condicionales en Python",
            "Funciones y listas",
            "Depuración de un programa con errores",
        ],
    },
    "closeout_performance": [
        "Trabajó con buena disposición y resolvió casi todos los ejercicios.",
        "Necesitó apoyo al inicio, pero terminó la práctica sin ayuda.",
        "Participó mucho y explicó sus respuestas con claridad.",
        "Se distrajo un poco; avanzó más en la segunda mitad.",
        "Mostró avances claros respecto a la clase anterior.",
        "Comprendió el tema; aún comete errores de cálculo.",
    ],
    "closeout_homework": [
        "Resolver la hoja de ejercicios de la página 12.",
        "Leer el texto asignado y anotar tres dudas.",
        "Practicar diez ejercicios del tema visto.",
        "Repasar el vocabulario de la clase.",
    ],
    "closeout_next_steps": [
        "Repasar el tema con más ejercicios antes de avanzar.",
        "Iniciar el siguiente tema del programa.",
        "Revisar la tarea al inicio de la próxima clase.",
        "Preparar un ejercicio tipo examen.",
    ],
    "closeout_change_reasons": [
        "Por favor detalla los temas vistos en la clase.",
        "Los minutos reportados no coinciden con el horario; revísalos.",
    ],
    "closeout_correction_reasons": [
        "La clase terminó diez minutos antes de lo reportado.",
        "Ajuste de minutos tras revisar la bitácora de la recepción.",
    ],
    "attendance_correction_reasons": [
        "La familia avisó que el alumno llegó tarde por el tráfico.",
        "Corrección tras revisar el registro de entrada.",
        "El tutor marcó por error la asistencia.",
    ],
    "family_cancellation_notes": [
        "Tiene una actividad escolar ese día.",
        "El alumno está enfermo.",
        "Salimos de viaje familiar.",
        "Tiene una cita médica a esa hora.",
        "Se le juntó con un examen en la escuela.",
    ],
    "tutor_cancellation_notes": [
        "Tengo un compromiso académico que no puedo mover.",
        "Me surgió un trámite urgente.",
        "Tengo una capacitación obligatoria ese día.",
    ],
    "center_cancellation_reasons": [
        "Mantenimiento eléctrico en el centro.",
        "Fumigación programada del edificio.",
        "Corte de agua en la zona.",
    ],
    "exception_reasons": [
        "Enfermedad con justificante médico.",
        "Emergencia familiar comprobada.",
    ],
    "reschedule_notes": [
        "¿Podemos cambiar la clase a otro horario esa semana?",
        "Tiene entrenamiento a esa hora; pedimos otro horario.",
        "Necesito mover la clase por un compromiso.",
        "Preferimos un horario más tarde ese día.",
    ],
    "change_rejection_reasons": [
        "La solicitud llegó fuera del plazo de la política.",
        "No hay disponibilidad del tutor en el horario propuesto.",
        "La clase es parte de un plan de regularización que no conviene interrumpir.",
    ],
    "time_off_notes": [
        "Consulta médica",
        "Trámite en el banco",
        "Examen de posgrado",
        "Asunto familiar",
    ],
    "availability_rejection_reasons": [
        "No hay un tutor sustituto disponible para esas clases.",
        "El aviso llegó con muy poca anticipación.",
    ],
    "substitution_reasons": [
        "El tutor tiene un permiso aprobado.",
        "Cobertura por ausencia del tutor titular.",
        "El tutor titular no está disponible ese día.",
    ],
    "offer_decline_notes": [
        "No tengo disponibilidad ese día.",
        "Ya tengo otra clase cerca de esa hora.",
        "Prefiero no tomar clases extra esta semana.",
    ],
    "session_request_notes": [
        "Necesita repasar antes de su examen.",
        "Quisiéramos una clase extra de práctica.",
        "Tiene dudas con la tarea de esta semana.",
        "Le gustaría reforzar el tema antes del parcial.",
        "Faltó a la escuela y necesita ponerse al corriente.",
    ],
    "session_request_rejection_reasons": [
        "No hay tutor disponible en ese horario.",
        "El alumno ya tiene una clase muy cerca de esa hora.",
        "Ese día el centro tiene un evento interno.",
    ],
    "notice_override_reasons": [
        "Solicitud revisada fuera de horario; la familia ya estaba avisada.",
        "Confirmación acordada por teléfono con la familia.",
    ],
}
