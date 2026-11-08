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
    # HF-01c (#551): academics.
    "note_progress": [
        "Trabajó con atención y resolvió casi todo sin ayuda.",
        "Le costó al inicio, pero mejoró con los ejemplos.",
        "Participó mucho y explicó sus respuestas con claridad.",
        "Se distrajo un poco; conviene repasar en casa.",
        "Avanzó más rápido que la semana pasada.",
        "Todavía confunde algunos pasos, aunque va mejorando.",
    ],
    "note_next_steps": [
        "La próxima clase repasaremos este tema con más ejercicios.",
        "Sugiero practicar quince minutos diarios en casa.",
        "En la siguiente sesión pasaremos al tema siguiente.",
        "Traerá sus dudas de la escuela para resolverlas juntos.",
        "Haremos una pequeña evaluación la próxima semana.",
    ],
    "evaluation_templates": {
        "diagnostic": "Diagnóstico inicial",
        "monthly": "Evaluación mensual",
        "course": "Evaluación de curso",
    },
    "evaluation_template_descriptions": {
        "diagnostic": "Punto de partida del alumno en la materia al iniciar clases.",
        "monthly": "Seguimiento del avance del alumno al cierre de cada mes.",
        "course": "Evaluaciones previstas en el temario de los cursos.",
    },
    "evaluation_criteria": {
        "diagnostic": [
            "Conocimientos previos",
            "Comprensión de instrucciones",
            "Resolución de problemas",
            "Hábitos de estudio",
        ],
        "monthly": [
            "Dominio de los temas del mes",
            "Participación en clase",
            "Cumplimiento de tareas",
        ],
        "course": [
            "Dominio del contenido",
            "Aplicación práctica",
        ],
    },
    "evaluation_comments": [
        "Muestra una base sólida en este aspecto.",
        "Necesita reforzar este punto con práctica guiada.",
        "Mejoró de forma notable respecto al mes anterior.",
        "Responde bien cuando se le dan ejemplos concretos.",
        "Conviene revisarlo de nuevo antes del examen escolar.",
        "Cumple con lo esperado para su nivel.",
    ],
    "evaluation_feedback": [
        "Buen inicio; trabajaremos primero los temas con más dificultad.",
        "Avance constante; mantener la práctica semanal.",
        "Resultados por encima de lo esperado; puede subir el nivel de reto.",
        "Hay temas pendientes; reforzaremos con ejercicios adicionales.",
        "Muy buena actitud; falta consolidar algunos procedimientos.",
        "Resultado satisfactorio en la evaluación.",
    ],
    "plan_titles": [
        "Plan de refuerzo de {subject}",
        "Plan de avance en {subject}",
        "Preparación de exámenes de {subject}",
    ],
    "plan_descriptions": [
        "Plan acordado con la familia para fortalecer las bases de la materia.",
        "Metas del periodo para avanzar al ritmo del programa escolar.",
        "Preparación por etapas para los exámenes de la escuela.",
    ],
    "plan_objectives": [
        "Consolidar los conceptos básicos",
        "Resolver ejercicios de forma autónoma",
        "Mejorar la comprensión de problemas escritos",
        "Prepararse para el examen parcial",
        "Ganar rapidez y precisión",
        "Organizar el estudio en casa",
    ],
    "plan_milestones": [
        "Completar la guía de ejercicios de repaso",
        "Resolver una práctica sin ayuda",
        "Explicar el procedimiento con sus palabras",
        "Obtener ocho o más en la práctica semanal",
        "Terminar el cuadernillo de problemas",
        "Presentar un examen de práctica",
        "Corregir los errores del examen anterior",
        "Llevar un registro de estudio diario",
    ],
    "plan_activities": [
        "Ejercicios guiados en clase y una hoja de práctica en casa.",
        "Lectura del tema y resumen en el cuaderno.",
        "Práctica cronometrada al final de cada clase.",
        "Revisión conjunta de los errores frecuentes.",
        "Problemas de aplicación con situaciones cotidianas.",
    ],
    "plan_discontinue_reasons": [
        "Plan suspendido: la familia pidió pausar las clases de esta materia.",
        "Plan suspendido: el alumno cambió de objetivos con la escuela.",
        "Plan suspendido: se acordó un nuevo plan con otro enfoque.",
    ],
    "homework_titles": [
        "Práctica: {topic}",
        "Tarea de repaso: {topic}",
        "Ejercicios: {topic}",
    ],
    "homework_instructions": [
        "Resuelve los ejercicios de la hoja y escribe el procedimiento completo.",
        "Lee el material de la clase y responde las preguntas del final.",
        "Haz diez ejercicios del tema y marca los que te costaron más trabajo.",
        "Prepara un resumen de una página con los puntos más importantes.",
    ],
    "homework_answers": [
        "Terminé todos los ejercicios; tuve dudas en el último.",
        "Aquí está mi resumen del tema.",
        "Resolví la hoja completa y revisé mis respuestas.",
        "Hice los ejercicios y anoté mis dudas para la clase.",
    ],
    "homework_feedback": [
        "Muy bien resuelto; cuida la presentación del procedimiento.",
        "Buen trabajo; revisa los dos ejercicios marcados.",
        "Completo y correcto. ¡Excelente!",
        "Vas bien; practica un poco más los casos difíciles.",
        "Faltó justificar algunas respuestas; lo vemos en clase.",
        "Buen esfuerzo; corrige los errores de cálculo.",
    ],
    "report_summaries": [
        "El alumno asistió con regularidad y mostró un avance constante en el periodo.",
        "Periodo con buen ritmo de trabajo y mejor comprensión de los temas.",
        "Avance desigual: buenos resultados en clase, pero tareas incompletas.",
        "El alumno consolidó los temas principales y ganó confianza.",
        "Periodo de adaptación con progreso visible en las últimas semanas.",
    ],
    "report_strengths": [
        "Participa con entusiasmo y hace buenas preguntas.",
        "Entrega las tareas a tiempo y con cuidado.",
        "Razona bien los problemas y explica sus respuestas.",
        "Es constante y aprovecha bien el tiempo de clase.",
        "Aplica lo aprendido en ejercicios nuevos.",
    ],
    "report_improvements": [
        "Revisar sus respuestas antes de entregar.",
        "Dedicar más tiempo a la práctica en casa.",
        "Leer con atención los enunciados largos.",
        "Organizar mejor sus apuntes.",
        "Pedir ayuda cuando algo no le queda claro.",
    ],
    "report_next_steps": [
        "Continuar con el temario y reforzar los temas pendientes.",
        "Practicar con exámenes de años anteriores.",
        "Mantener la tarea semanal y revisar errores en clase.",
        "Iniciar el siguiente bloque del programa escolar.",
        "Trabajar ejercicios de mayor dificultad.",
    ],
    "report_change_reasons": [
        "Agrega ejemplos concretos del avance en el periodo.",
        "Revisa el resumen; no coincide con la asistencia del periodo.",
    ],
    # HF-01d (#552): billing.
    "billing_products": {
        "pack8": "Paquete de 8 clases",
        "pack12": "Paquete de 12 clases",
        "intensive": "Paquete intensivo de 10 clases",
        "subject4": "Paquete de 4 clases de {subject}",
    },
    "billing_discounts": {
        "sibling": "Descuento por hermanos",
        "scholarship_a": "Beca académica",
        "scholarship_b": "Beca de apoyo familiar",
        "promotion": "Promoción de regreso a clases",
        "upcoming": "Descuento por pronto pago de diciembre",
    },
    "manual_charges": {
        "materials": "Material didáctico del trimestre",
        "exam": "Cuota del examen de práctica",
    },
    "adjustment_reasons": [
        "El material se entregó incompleto; se descuenta la parte faltante.",
    ],
    "due_date_reasons": [
        "La familia pidió pagar después de la quincena.",
        "Prórroga acordada por un retraso en el pago de nómina.",
    ],
    "reversal_reasons": [
        "El banco rechazó la transferencia por falta de fondos.",
    ],
    "refund_reasons": [
        "Devolución del paquete cancelado a solicitud de la familia.",
    ],
    "package_cancellation_reasons": [
        "La familia cambia a la mensualidad a partir de noviembre.",
    ],
    "withdrawal_reasons": [
        "No se empezó la materia del paquete; la familia pidió darlo de baja.",
    ],
    # HF-01e (#553): payroll.
    "pay_item_types": {
        "bonus": "Bono de puntualidad",
        "deduction": "Descuento por material extraviado",
    },
    "pay_item_descriptions": {
        "bonus": "Bono quincenal por llegar puntual a todas sus clases",
        "deduction": "Reposición de un libro de ejercicios extraviado",
    },
    "payroll_manual_reasons": {
        "advance": "Anticipo de nómina entregado al ingresar al centro",
        "event": "Apoyo en la feria de ciencias del sábado 24 de octubre",
    },
    "payroll_reversal_reasons": [
        "El banco devolvió la transferencia: la cuenta CLABE estaba mal capturada.",
    ],
    # HF-01e (#553): operating expenses.
    "teaching_materials_category": "Material didáctico",
    "vendors": {
        "landlord": "Inmobiliaria Los Fresnos",
        "power": "Servicios Eléctricos del Valle",
        "internet": "Red Colibrí Telecomunicaciones",
        "cleaning": "Limpieza Integral Brillo",
        "software": "Aula Digital en la Nube",
        "insurer": "Aseguradora Monte Alto",
        "stationery": "Papelería La Pluma Azul",
        "hardware": "Ferretería y Mantenimiento El Tornillo",
        "furniture": "Mobiliario Escolar Tlacuache",
    },
    "vendor_contacts": {
        "landlord": "Lic. Rosa Méndez, cobranza@losfresnos.example",
        "power": "Atención a clientes, clientes@electricosdelvalle.example",
        "internet": "Soporte, soporte@redcolibri.example",
        "cleaning": "Coordinación, servicio@limpiezabrillo.example",
        "software": "Facturación, facturas@auladigital.example",
        "insurer": "Agente Jorge Salas, polizas@montealto.example",
        "stationery": "Ventas, ventas@plumaazul.example",
        "hardware": "Mostrador, pedidos@eltornillo.example",
        "furniture": "Pedidos, pedidos@tlacuache.example",
    },
    "expense_templates": {
        "rent": "Renta mensual del local",
        "power": "Luz y agua del mes",
        "cleaning": "Limpieza semanal del centro",
        "software": "Suscripción a la plataforma de aulas virtuales",
        "insurance": "Seguro anual de responsabilidad civil",
    },
    "expense_descriptions": {
        "geometry": "Juegos de geometría y calculadoras para el aula 1",
        "paper": "Papel, tóner y marcadores para pizarrón",
        "air": "Reparación del aire acondicionado del aula 2",
        "projector": "Proyector para el aula 4",
        "readers": "Libros de lectura para primaria",
        "paint": "Pintura del pasillo y de la recepción",
        "cleaning_supplies": "Artículos de limpieza para el mes",
        "whiteboard": "Pizarrón blanco para el aula 5",
        "lab": "Material para las prácticas de química",
        "water": "Garrafones de agua purificada",
        "lock": "Cambio de la chapa de la entrada",
        "meeting": "Café y galletas para la junta con familias",
    },
    "expense_adjustment_reasons": {
        "shipping": "Cargo de envío e instalación no incluido en la nota",
        "power": "El recibo de octubre llegó más alto que el monto de la plantilla",
        "void": "Gasto capturado dos veces; se anula el duplicado",
    },
    "expense_reversal_reasons": [
        "El banco rechazó la transferencia por un error en la referencia.",
    ],
}
