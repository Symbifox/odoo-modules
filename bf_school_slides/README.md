# Symbifox École: course contents (`bf_school_slides`)

Course contents in Odoo's eLearning, given to the students of school groups and to their parents.

## Features

- A course (eLearning) is tied to one or more **school groups**. Their active students are
  its attendees, and, unless the teacher unticks it, the adults who receive the school's
  notices for them. Attendees follow the enrolments: a student who joins a group is added,
  one who leaves is taken away, at once and by a daily job.
- Only the attendees the bridge added are taken away: an attendee the teacher invited by
  hand stays.
- A school course is always **shown to its attendees only**, with **no comments nor
  reviews**, **no karma** (course, ranking, quizzes, also when a slide is edited), **no
  "content published" nor "course completed" email**, and no user group enrolled or allowed
  to upload as a whole. Parents attend without following the course.
- Students earn no karma at all: no rank, no rank email, no badge, no email validation.
- **Renew for another year** copies the course with its contents, their files and their
  quizzes, ties the copy to the groups of the next year, and leaves it unpublished.
- **Portal** (**Courses**): the published school courses the person attends, the student's
  own or, for a parent, their children's.
- A student's portal user cannot publish their eLearning profile on the website.

## Security

- The school staff gets the eLearning **Officer** role: it writes the courses one is
  responsible for and reads the others.
- A teacher ties a course to the groups they teach only; the office to any group.
- Only the person responsible for a course (or the office) renews it.
- A deleted group takes away the attendees it brought, at once and by the daily job.

## What has not been confirmed

- Videos come from YouTube, Vimeo or Google Drive only (eLearning); a video of students
  should not go there.
- eLearning quizzes give no mark; marked quizzes are the homework's online quizzes
  (`bf_school_work`).
