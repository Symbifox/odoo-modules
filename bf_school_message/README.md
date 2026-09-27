# Symbifox École: announcements to families (`bf_school_message`)

Bridge between the staff noticeboard (`bf_babillard`) and the family portal.

## Features

- A new noticeboard audience, **School families**: a whole school, or chosen groups
  of the current year.
- The families reached are computed on the day from enrolments and guardian links:
  every adult who receives notices for an enrolled student.
- One email per adult, in their language, whose subject names their children
  ("Léa, Tom : Sortie au musée"). A parent of three receives one email, not three.
- The email goes to every such adult with an address, portal account or not. Adults
  without an address are counted in the announcement's chatter.
- **School announcements** on the portal (`/my/school/news`). An announcement that
  asks for confirmation carries an "I have read it" button; the school sees who
  confirmed and when, through the noticeboard's own read receipts. Opening the email
  or its link never counts as a confirmation.
- Another family's announcement answers 404, never 403.

## Security

- The school administration edits the noticeboard (it implies the editor group) and
  writes to any family of the school.
- A teacher writes to the families of the groups they teach, every group of the
  announcement being theirs, and to nobody else. They read the family announcements
  of their groups, edit only their own, and see who has not confirmed yet. A record
  rule says "one of the groups is mine"; a constraint, checked after the write, says
  "all of them" (a teacher could otherwise add a colleague's group, or turn an
  announcement into an "all staff" one).
- Other internal users (neither editors nor teachers of the groups) do not see family
  announcements.
- Portal users have no access right on the noticeboard models; the portal reads
  through `_school_news_for()`, held equal to the recipient list by a test.
- If the module is uninstalled, family announcements fall back to an audience that
  reaches nobody, never to "all staff".

## What has not been confirmed

- No daily digest and no quiet hours yet: every announcement is emailed when published.
- No push notification to parents yet.
