"""The English text pool of the simulation (#543 E11): fixed, curated
strings. Same keys as ``texts_es`` (a unit test checks the parity). All
names, addresses and texts are invented; no value uses an accented letter."""

from typing import Final

TEXTS: Final[dict[str, object]] = {
    "center_name": "Horizon Tutoring Center",
    "contact_email": "contact@example.com",
    "address": "45 Almond Tree Street, Jardines, 01000 Mexico City",
    "family_name": "The {surnames} family",
    "academic_period": "2026–2027 school year",
    "education_levels": {
        "elementary": "Elementary school",
        "middle_school": "Middle school",
        "high_school": "High school",
        "university": "University",
    },
    "expense_categories": {
        "rent": "Rent",
        "utilities": "Utilities",
        "supplies": "Supplies",
        "equipment": "Equipment",
        "maintenance": "Maintenance",
    },
    "subjects": {
        "math_e": "Mathematics",
        "reading_e": "Reading",
        "english_e": "English",
        "math_m": "Mathematics",
        "english_m": "English",
        "science_m": "Science",
        "math_h": "Mathematics",
        "physics_h": "Physics",
        "chemistry_h": "Chemistry",
        "english_h": "English",
        "calculus_u": "Calculus",
        "programming_u": "Programming",
    },
    "classrooms": {
        "room1": "Room 1",
        "room2": "Room 2",
        "room3": "Room 3",
        "room4": "Group room",
        "room5": "Lab",
    },
    "closures": {
        "independence": "Independence Day",
        "day_of_the_dead": "Day of the Dead",
        "revolution": "Revolution Day",
    },
    "contact_relationships": [
        "Aunt",
        "Uncle",
        "Grandmother",
        "Grandfather",
        "Neighbor",
        "Godmother",
        "Older sister",
    ],
    "professional_experience": [
        (
            "Mathematics graduate with six years of experience in elementary and "
            "middle school. Prepares students for entrance exams."
        ),
        (
            "Engineering physicist with four years as a high school tutor. Likes to"
            " explain with simple experiments."
        ),
        (
            "Master's degree in computer science; has taught calculus and "
            "programming to university students for five years."
        ),
        (
            "Secondary education graduate specializing in mathematics. Eight years "
            "of classroom teaching and three as a private tutor."
        ),
        (
            "English teaching graduate with a C2 certificate. Ten years of "
            "experience with children and teenagers."
        ),
        (
            "Education specialist in early reading, writing and mathematics for the"
            " first grades of elementary school."
        ),
        (
            "Actuary with teaching experience in differential and integral calculus"
            " and in introductory programming."
        ),
        (
            "Biologist with a master's degree in education; has taught middle "
            "school science and mathematics for seven years."
        ),
        (
            "Pharmaceutical chemist; coaches high school students in physics, "
            "chemistry and mathematics."
        ),
        (
            "Translator and English teacher with nine years of experience; also "
            "runs reading workshops."
        ),
        (
            "Applied mathematics graduate; combines elementary and middle school "
            "tutoring with olympiad training."
        ),
        (
            "Chemical engineer with three years of experience as a science and "
            "beginner programming tutor."
        ),
    ],
    "rejection_reasons": [
        "The proof of studies for this subject is missing.",
    ],
    "revocation_reasons": [
        "The subject was reassigned after the October teaching review.",
    ],
    "exception_notes": [
        "Medical appointment",
        "Personal errand",
        "Teachers' conference",
        "Extra hours for catch-up classes",
        "Family trip",
        "Training workshop",
        "Saturday review session",
    ],
}
