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
    # --- HF-01b (#550): the schedule and the courses ---
    "group_names": {
        "g1": "Middle school math club",
        "g2": "High school English conversation",
        "g3": "Elementary reading workshop",
    },
    "course_names": {
        "exam": "High school entrance exam preparation",
        "physics": "Physics olympiad workshop",
    },
    "course_descriptions": {
        "exam": (
            "Guided review of arithmetic, algebra and geometry with practice exams."
        ),
        "physics": "Mechanics and energy problems with simple lab activities.",
    },
    "course_modules": {
        "exam": [
            "Arithmetic and fractions",
            "Basic algebra",
            "Geometry and measurement",
            "Final practice exam",
        ],
        "physics": [
            "Kinematics",
            "Newton's laws",
            "Work and energy",
        ],
    },
    "course_materials": {
        "exam": [
            "Workbook 1",
            "Workbook 2",
            "Geometry set",
            "Printed practice exam",
        ],
        "physics": [
            "Kinematics problem sheets",
            "Lab carts and pulleys",
            "Energy problem guide",
        ],
    },
    "course_assessments": {
        "exam": ["Diagnostic exam", "Practice entrance exam"],
        "physics": ["Mechanics exam", "Lab report"],
    },
    "cohort_names": {
        "exam": "Entrance exam, fall 2026",
        "physics": "Physics olympiad, November group",
    },
    "closeout_topics": {
        "math_e": [
            "Addition and subtraction with regrouping",
            "Multiplication tables from 6 to 9",
            "Fraction problems with drawings",
        ],
        "reading_e": [
            "Reading a story aloud and comprehension",
            "Main ideas of an informational text",
            "New vocabulary and using the dictionary",
        ],
        "english_e": [
            "Colors, numbers and greetings",
            "The verb to be in short sentences",
            "A song and animal vocabulary",
        ],
        "math_m": [
            "Linear equations",
            "Ratios and percentages",
            "Area and perimeter of composite shapes",
        ],
        "english_m": [
            "Simple present and daily routines",
            "Simple past with regular verbs",
            "Listening comprehension of a dialogue",
        ],
        "science_m": [
            "The cell and its parts",
            "Mixtures and separation methods",
            "Forces and motion in daily life",
        ],
        "math_h": [
            "Linear and quadratic functions",
            "Right triangle trigonometry",
            "Systems of equations",
        ],
        "physics_h": [
            "Uniform linear motion",
            "Newton's second law",
            "Conservation of energy",
        ],
        "chemistry_h": [
            "Naming inorganic compounds",
            "Balancing chemical equations",
            "Basic stoichiometry",
        ],
        "english_h": [
            "Present perfect in conversation",
            "Writing a formal email",
            "Reading an article and an oral summary",
        ],
        "calculus_u": [
            "Limits and continuity",
            "Derivatives with the chain rule",
            "Integration by substitution",
        ],
        "programming_u": [
            "Loops and conditionals in Python",
            "Functions and lists",
            "Debugging a broken program",
        ],
    },
    "closeout_performance": [
        "Worked willingly and solved almost every exercise.",
        "Needed support at first but finished the practice alone.",
        "Participated a lot and explained the answers clearly.",
        "Was a little distracted; did better in the second half.",
        "Showed clear progress since the last class.",
        "Understood the topic; still makes calculation mistakes.",
    ],
    "closeout_homework": [
        "Solve the exercise sheet on page 12.",
        "Read the assigned text and write down three questions.",
        "Practice ten exercises on today's topic.",
        "Review the vocabulary from class.",
    ],
    "closeout_next_steps": [
        "Review the topic with more exercises before moving on.",
        "Start the next topic of the program.",
        "Check the homework at the start of the next class.",
        "Prepare an exam-style exercise.",
    ],
    "closeout_change_reasons": [
        "Please describe the topics covered in more detail.",
        "The reported minutes do not match the schedule; please check them.",
    ],
    "closeout_correction_reasons": [
        "The class ended ten minutes earlier than reported.",
        "Minutes adjusted after checking the front desk log.",
    ],
    "attendance_correction_reasons": [
        "The family said the student arrived late because of traffic.",
        "Corrected after checking the sign-in sheet.",
        "The tutor marked the attendance by mistake.",
    ],
    "family_cancellation_notes": [
        "There is a school activity that day.",
        "The student is sick.",
        "We are going on a family trip.",
        "There is a doctor's appointment at that time.",
        "It clashes with an exam at school.",
    ],
    "tutor_cancellation_notes": [
        "I have an academic commitment I cannot move.",
        "An urgent errand came up.",
        "I have a mandatory training that day.",
    ],
    "center_cancellation_reasons": [
        "Electrical maintenance at the center.",
        "Scheduled pest control in the building.",
        "Water outage in the area.",
    ],
    "exception_reasons": [
        "Illness with a doctor's note.",
        "Documented family emergency.",
    ],
    "reschedule_notes": [
        "Can we move the class to another time that week?",
        "There is practice at that time; we ask for another time.",
        "I need to move the class because of a commitment.",
        "We would prefer a later time that day.",
    ],
    "change_rejection_reasons": [
        "The request arrived outside the policy deadline.",
        "The tutor is not available at the proposed time.",
        "The class is part of a catch-up plan that should not be interrupted.",
    ],
    "time_off_notes": [
        "Medical check-up",
        "Errand at the bank",
        "Graduate school exam",
        "Family matter",
    ],
    "availability_rejection_reasons": [
        "No substitute tutor is available for those classes.",
        "The notice came too late.",
    ],
    "substitution_reasons": [
        "The tutor has approved time off.",
        "Covering for the regular tutor's absence.",
        "The regular tutor is not available that day.",
    ],
    "offer_decline_notes": [
        "I am not available that day.",
        "I already have another class close to that time.",
        "I prefer not to take extra classes this week.",
    ],
    "session_request_notes": [
        "Needs to review before an exam.",
        "We would like an extra practice class.",
        "Has questions about this week's homework.",
        "Would like to reinforce the topic before the midterm.",
        "Missed school and needs to catch up.",
    ],
    "session_request_rejection_reasons": [
        "No tutor is available at that time.",
        "The student already has a class close to that time.",
        "The center has an internal event that day.",
    ],
    "notice_override_reasons": [
        "Request reviewed after hours; the family had already been told.",
        "Confirmation agreed with the family by phone.",
    ],
    # HF-01c (#551): academics.
    "note_progress": [
        "Worked attentively and solved almost everything without help.",
        "Struggled at first but improved with the examples.",
        "Participated a lot and explained the answers clearly.",
        "Got a little distracted; worth reviewing at home.",
        "Moved faster than last week.",
        "Still mixes up a few steps, though improving.",
    ],
    "note_next_steps": [
        "Next class we will review this topic with more exercises.",
        "I suggest fifteen minutes of practice at home every day.",
        "In the next session we will move on to the next topic.",
        "Will bring questions from school so we can solve them together.",
        "We will have a short quiz next week.",
    ],
    "evaluation_templates": {
        "diagnostic": "Initial assessment",
        "monthly": "Monthly evaluation",
        "course": "Course assessment",
    },
    "evaluation_template_descriptions": {
        "diagnostic": "The student's starting point in the subject when classes begin.",
        "monthly": "A check on the student's progress at the end of each month.",
        "course": "The assessments planned in the course syllabus.",
    },
    "evaluation_criteria": {
        "diagnostic": [
            "Prior knowledge",
            "Following instructions",
            "Problem solving",
            "Study habits",
        ],
        "monthly": [
            "Command of the month's topics",
            "Class participation",
            "Homework completion",
        ],
        "course": [
            "Command of the content",
            "Practical application",
        ],
    },
    "evaluation_comments": [
        "Shows a solid foundation here.",
        "Needs to reinforce this point with guided practice.",
        "Clearly improved since last month.",
        "Responds well to concrete examples.",
        "Worth reviewing again before the school exam.",
        "Meets expectations for the level.",
    ],
    "evaluation_feedback": [
        "Good start; we will work first on the hardest topics.",
        "Steady progress; keep up the weekly practice.",
        "Results above expectations; ready for harder challenges.",
        "Some topics are pending; we will add extra exercises.",
        "Great attitude; a few procedures still need work.",
        "A satisfactory result in the evaluation.",
    ],
    "plan_titles": [
        "{subject} support plan",
        "{subject} progress plan",
        "{subject} exam preparation",
    ],
    "plan_descriptions": [
        "A plan agreed with the family to strengthen the basics of the subject.",
        "Goals for the term to keep pace with the school program.",
        "Step-by-step preparation for the school exams.",
    ],
    "plan_objectives": [
        "Consolidate the basic concepts",
        "Solve exercises independently",
        "Understand word problems better",
        "Get ready for the midterm exam",
        "Gain speed and accuracy",
        "Organize study time at home",
    ],
    "plan_milestones": [
        "Finish the review worksheet",
        "Solve a practice set without help",
        "Explain the procedure in own words",
        "Score eight or more on the weekly practice",
        "Complete the problem booklet",
        "Take a practice exam",
        "Correct the mistakes from the last exam",
        "Keep a daily study log",
    ],
    "plan_activities": [
        "Guided exercises in class and a practice sheet at home.",
        "Read the topic and write a summary in the notebook.",
        "Timed practice at the end of each class.",
        "Review the most common mistakes together.",
        "Applied problems with everyday situations.",
    ],
    "plan_discontinue_reasons": [
        "Plan stopped: the family asked to pause classes in this subject.",
        "Plan stopped: the student changed goals with the school.",
        "Plan stopped: a new plan with a different focus was agreed.",
    ],
    "homework_titles": [
        "Practice: {topic}",
        "Review homework: {topic}",
        "Exercises: {topic}",
    ],
    "homework_instructions": [
        "Solve the exercises on the sheet and show all your work.",
        "Read the class material and answer the questions at the end.",
        "Do ten exercises on the topic and mark the ones that were hardest.",
        "Write a one-page summary of the key points.",
    ],
    "homework_answers": [
        "I finished all the exercises; I had questions on the last one.",
        "Here is my summary of the topic.",
        "I solved the whole sheet and checked my answers.",
        "I did the exercises and wrote down my questions for class.",
    ],
    "homework_feedback": [
        "Very well done; take care with how you present your work.",
        "Good work; check the two exercises I marked.",
        "Complete and correct. Excellent!",
        "Good progress; practice the harder cases a bit more.",
        "Some answers needed more justification; we will see it in class.",
        "Good effort; fix the calculation mistakes.",
    ],
    "report_summaries": [
        "The student attended regularly and made steady progress this period.",
        "A period with a good work pace and better understanding of the topics.",
        "Uneven progress: good results in class, but incomplete homework.",
        "The student consolidated the main topics and gained confidence.",
        "A period of adjustment with visible progress in the last weeks.",
    ],
    "report_strengths": [
        "Participates eagerly and asks good questions.",
        "Hands in homework on time and with care.",
        "Reasons through problems well and explains the answers.",
        "Is consistent and makes good use of class time.",
        "Applies what was learned to new exercises.",
    ],
    "report_improvements": [
        "Check the answers before handing work in.",
        "Spend more time practicing at home.",
        "Read long problem statements carefully.",
        "Keep notes better organized.",
        "Ask for help when something is unclear.",
    ],
    "report_next_steps": [
        "Continue the syllabus and reinforce the pending topics.",
        "Practice with past exams.",
        "Keep the weekly homework and review mistakes in class.",
        "Start the next unit of the school program.",
        "Work on harder exercises.",
    ],
    "report_change_reasons": [
        "Add concrete examples of the progress in the period.",
        "Check the summary; it does not match the attendance in the period.",
    ],
    # HF-01d (#552): billing.
    "billing_products": {
        "pack8": "8-class package",
        "pack12": "12-class package",
        "intensive": "Intensive 10-class package",
        "subject4": "4-class {subject} package",
    },
    "billing_discounts": {
        "sibling": "Sibling discount",
        "scholarship_a": "Academic scholarship",
        "scholarship_b": "Family support scholarship",
        "promotion": "Back-to-school promotion",
        "upcoming": "December early payment discount",
    },
    "manual_charges": {
        "materials": "Term study materials",
        "exam": "Practice exam fee",
    },
    "adjustment_reasons": [
        "The materials were delivered incomplete; the missing part is credited.",
    ],
    "due_date_reasons": [
        "The family asked to pay after mid-month payday.",
        "Extension agreed because of a delayed paycheck.",
    ],
    "reversal_reasons": [
        "The bank returned the transfer for insufficient funds.",
    ],
    "refund_reasons": [
        "Refund of the cancelled package at the family's request.",
    ],
    "package_cancellation_reasons": [
        "The family switches to the monthly plan from November.",
    ],
    "withdrawal_reasons": [
        "The package subject never started; the family asked to withdraw it.",
    ],
    # HF-01e (#553): payroll.
    "pay_item_types": {
        "bonus": "Punctuality bonus",
        "deduction": "Lost materials deduction",
    },
    "pay_item_descriptions": {
        "bonus": "Fortnightly bonus for arriving on time to every class",
        "deduction": "Replacement of a lost workbook",
    },
    "payroll_manual_reasons": {
        "advance": "Pay advance given when joining the center",
        "event": "Support at the science fair on Saturday, October 24",
    },
    "payroll_reversal_reasons": [
        "The bank returned the transfer: the account number was entered wrong.",
    ],
    # HF-01e (#553): operating expenses.
    "teaching_materials_category": "Teaching materials",
    "vendors": {
        "landlord": "Ash Grove Properties",
        "power": "Valley Power and Water",
        "internet": "Hummingbird Telecom",
        "cleaning": "Sparkle Cleaning Services",
        "software": "Cloud Classroom Software",
        "insurer": "High Mountain Insurance",
        "stationery": "Blue Pen Stationery",
        "hardware": "The Bolt Hardware and Repairs",
        "furniture": "Schoolroom Furniture Co.",
    },
    "vendor_contacts": {
        "landlord": "Rose Mendez, billing@ashgrove.example",
        "power": "Customer service, customers@valleypower.example",
        "internet": "Support, support@hummingbirdtelecom.example",
        "cleaning": "Scheduling, service@sparklecleaning.example",
        "software": "Billing, invoices@cloudclassroom.example",
        "insurer": "Agent George Sales, policies@highmountain.example",
        "stationery": "Sales, sales@bluepen.example",
        "hardware": "Front desk, orders@thebolt.example",
        "furniture": "Orders, orders@schoolroomfurniture.example",
    },
    "expense_templates": {
        "rent": "Monthly rent of the premises",
        "power": "Monthly electricity and water",
        "cleaning": "Weekly cleaning of the center",
        "software": "Virtual classroom platform subscription",
        "insurance": "Annual liability insurance",
    },
    "expense_descriptions": {
        "geometry": "Geometry sets and calculators for room 1",
        "paper": "Paper, toner and whiteboard markers",
        "air": "Air conditioning repair in room 2",
        "projector": "Projector for room 4",
        "readers": "Reading books for elementary school",
        "paint": "Paint for the hallway and the front desk",
        "cleaning_supplies": "Cleaning supplies for the month",
        "whiteboard": "Whiteboard for room 5",
        "lab": "Supplies for the chemistry practicals",
        "water": "Bottled drinking water",
        "lock": "New lock for the front door",
        "meeting": "Coffee and cookies for the family meeting",
    },
    "expense_adjustment_reasons": {
        "shipping": "Shipping and installation fee not on the invoice",
        "power": "The October bill came in higher than the template amount",
        "void": "Expense entered twice; the duplicate is voided",
    },
    "expense_reversal_reasons": [
        "The bank rejected the transfer because of a wrong reference.",
    ],
    # HF-01f (#554): administration and reporting.
    "contact_email_updated": "frontdesk@example.com",
    "invalid_email": "no-at-sign",
    "import_files": {
        "students": "new-students-september.csv",
        "balances": "opening-balances.csv",
        "schedules": "schedules-november.csv",
        "tutors": "tutors-december.csv",
    },
    "opening_balance_descriptions": [
        "Balance carried over from last school year",
        "Unpaid books from last school year",
    ],
    "report_setups": {
        "admin": "Active students",
        "coordinator": "Middle school students",
        "finance": "Monthly expenses",
    },
    "deleted_records": {
        "plan_title": "Review plan (draft)",
        "plan_objective": "Review the first term's topics",
        "plan_milestone": "Work through a practice sheet",
        "note": "Duplicate note entered by mistake; the details are in the closeout.",
    },
}
