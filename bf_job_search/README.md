# Job Search

A personal tracker for your own job applications, with the good habits built in.
Part of Symbifox Personal: it is written for the person who applies, not for a
sales team.

## What it does

- **Applications.** Spotted, preparing, applied, interview, offer, accepted. Keep
  the full advert (job boards remove it within weeks), the channel, the pay, and
  the exact CV and letter you sent.
- **Who closed it.** Closing an application says who ended it: the employer
  (refusal, position cancelled), you (not pursued, withdrawn, offer declined) or
  nobody (no response). A role you dropped is not a refusal, so the response
  rate stays readable.
- **Follow-ups.** A first follow-up 10 days after applying, or the day after the
  date the employer promised a reply, and never while the advert is still open.
  A second one 14 days later, then a question: close it as "no response"?
  Delays are yours to change in *My criteria*.
- **Interviews.** A preparation checklist (research, STAR stories, questions to
  ask, referees, logistics) due the day before, a private event in your
  calendar with no invitation sent, and a thank-you note due the day after, with
  one draft per interviewer.
- **Offers.** Answer deadline, written offer, negotiate before accepting, do not
  resign before the agreement is signed.
- **Referees.** Who agreed to be one and when, for which application they were
  named, and a reminder to warn them when an interview comes.
- **My criteria.** Salary floor, minimum hours and contract length, employers
  and words to avoid. An application that does not meet them is flagged, never
  dropped.
- **Dashboard.** Applications per week against your goal, response and
  interview rates by channel, median days to a first response, who closed.
- **Log.** A dated CSV of your applications (date, employer, position, channel,
  result) for a period. Employment Insurance in Canada asks claimants to keep
  such a record for six years. A purge removes the details of old closed
  applications (advert, pay, notes, files, interviews) and keeps the log.

## Where the defaults come from

- Thank-you note by email the next day, to each interviewer: Tahatū (New
  Zealand careers service), Robert Half, Québec.ca. Surveys of hiring managers
  say many take it into account; no controlled study measures its effect.
- Follow-up delays: Québec.ca (two weeks without a stated date), a Canadian
  survey of managers (most expect a follow-up within two weeks), career advisers
  (at most two follow-ups).
- Referees: warn them before they are called (Tahatū). In New Zealand an
  employer may only contact the referees you named, unless you consent (Office
  of the Privacy Commissioner).
- Job search log: Employment and Social Development Canada, rights and
  responsibilities of Employment Insurance claimants.

## Who sees what

Everything is private: each person sees only their own applications,
interviews, referees, criteria and dashboard, administrators included. The
thread of an application holds internal notes only and notifies nobody; only
its owner can follow it; its activities stay with its owner; a file cannot be
moved onto someone else's application. A system administrator can still change
the security rules themselves: the privacy holds between people, not against
whoever administers the database.

## Technical notes

- Depends on `mail` and `calendar` only.
- Models: `bf.job.application`, `bf.job.interview`, `bf.job.referee`,
  `bf.job.reference`, `bf.job.criteria`, and the transient `bf.job.stats`,
  `bf.job.close.wizard`, `bf.job.journal.wizard`. Every model has a global
  record rule on its owner.
- Labels are written in English; French (Canada) comes from `i18n/fr_CA.po`.
- Tests: `--test-tags /bf_job_search`.

## Changelog

- **18.0.1.0.1**: official Symbifox app icon (PNG, and the same image as SVG for the installable app).
- **18.0.1.0.0**: first release.

## License

LGPL-3. Les services de consultation Blue Fox, Inc.
