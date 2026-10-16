# Symbifox École: gradebook (`bf_school_gradebook`)

Evaluations and grades by course, the result of each competency and discipline computed from
them, the 20/20/60 weighting over the three terms, the ministerial exams and the 60 % threshold.

## Features

- **Subjects** of the Québec curriculum, each with its way of giving results, as the Education
  Regulation asks (I-13.3, r. 8, s. 30.1): by competency (language of instruction, second
  language, mathematics), by theory and practice component (science and technology), or a
  single discipline result. The elementary subjects and those of the first cycle of secondary
  school (with their units, s. 22 and 23) ship as data.
- **Terms**: the three terms of the school year with their report card deadlines (November 20,
  March 15, July 10), the first written communication (October 15) and the 20/20/60 weighting,
  all data. A deadline later than the regulation's is refused unless the year records the order
  that moves it, with its reference. The weights of the three terms always total 100 %: one weight
  cannot be changed alone (a new decree moves them in one save of the year).
- **Courses**: a subject taught to a group, with its teachers (taken from the group), its
  ministry course code and, for each competency or component, the weight the evaluation
  framework gives it.
- **Evaluations and grades**: graded out of any maximum, weighted among those that judge the same
  competency. A teacher gives the grades of their own courses and nobody else's; a closed term
  takes no grade from a teacher.
- **Results**: the result of each competency, of the discipline and the group's average are
  computed from the grades, rounded half up to whole percentages at each level (so a parent
  can recompute a report card from its figures), and never typed twice. The arithmetic is exact
  (fractions, not floating point: 74.5 is 75 and never 74), and a weight that changes, or the rule of
  the third term, recomputes the results at once. A student who left is in neither the group's
  average nor the ranks; the group's average is read when it is shown, never kept, so it cannot go
  stale when a student leaves. An evaluation cannot be marked out of less than a grade already entered.
- **Final results**: 20/20/60 over the terms, the ministerial exam worth a share of the final result
  (see below), the success threshold (s. 28.1), the units earned at the secondary level and the
  group's final average. The result of the third term can rest on that term's own evaluations or
  on the whole year's (a choice per course: the regulation calls it a summary of the program).
- **Ministerial exams**: a student's exam goes from "to take" to "taken" (the school marks what it
  marks itself), then "result received" when the Ministry's is in, or "absent". The two last
  states, and the percentage the Ministry gave, are entered by the school administration only (a
  teacher keeps "taken" and the raw scores); a result received is entered with its percentage (a
  real 0 % is entered by typing another figure first, then 0). Until the Ministry's result is in,
  **the course has no final result**: the figure is the school's alone, said to be provisional, and
  the student neither passes nor earns the units. The raw scores and the rank in fifths are for the
  Ministry.
- **The share of the exam** (a course's "Exam share"): the regulation says 20 % of the final result
  (s. 30.3), "subject to s. 34", under which the Minister counts 50 % of the school's evaluation
  for the programs of secondary school that lead to the diploma. Reading s. 32 (the units that
  count towards the diploma are those of the 4th and 5th years), the module proposes **50 % for a
  group whose levels are all Secondary IV or V and 20 % elsewhere** (elementary school and the
  first three years of secondary school). A group of several cycles, or with no level, keeps 20 %
  and the course says to check: the text does not settle those cases and the share stays editable.
  The Ministry's own sanction of a program (s. 34) is the Ministry's, not computed here.
- **Units**: a course takes the units of its subject, which ship with the first cycle's (s. 23). A
  group with a student of the second cycle (Secondary III to V) gets **no units by default**: the table
  of the second cycle (s. 23.1) is not shipped, so the course says to enter its units and is listed
  (and filterable) as "Units to enter" until it has them.

## Law

- Results are percentages (s. 30.2); the final result of a competency or component counts the
  three terms for 20 %, 20 % and 60 %; the discipline result is computed from the weighting of the
  competencies set by the evaluation framework.
- A term or a component with no evaluation does not count and the others are re-weighted: the
  regulation asks for detailed results only for what was evaluated (s. 30.1).

## Security

Teachers read the courses of their groups and give the grades of the courses they teach; a teacher
listed on a group reads all the results of its courses. The office sees and corrects everything of
its own company. A closed third term also closes the exam results to teachers. Computed results are
written by the system only, a grade cannot be moved to another student or evaluation, an evaluation
cannot be moved to another course, and the state of a term moves by its buttons, by RPC too.

Several companies in one database are kept apart: the results, the results by competency, the
courses and their components, the evaluations and the grades of one company are never read by the
office of another. The subjects that ship with the module have no company: every school reads them
and **only the system administration changes them** (a change reaches every school's courses); a
subject a school adds carries its company and is that office's own. A school changes the weights and
the units of its own courses, not those of the shared subjects.

## What has not been confirmed

- **The weights of the evaluation framework** (for example how much "Read" counts in French) are
  not shipped: they are the Ministry's figures per subject and level, and none could be read from a
  primary source here. The school enters them on each course; a course does not compute a discipline
  result on weights nobody entered.
- The second cycle of secondary school (s. 23.1): its table of subjects and units could not be
  read as text. The school adds them, and enters the units of each of its second-cycle courses.
- The success threshold (60 %, s. 28.1) is a setting of the school year the school can change; the
  module does not require a reason for it. A single deadline exception lifts the ceiling of the three
  report cards and of the first communication of that year.
- The pass rules of a cycle (s. 28) are the school's: this module computes results, it does not
  decide who moves on.
