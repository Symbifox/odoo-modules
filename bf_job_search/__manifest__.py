# -*- coding: utf-8 -*-
{
    "name": "Job Search",
    "version": "18.0.1.0.2",
    "category": "Productivity",
    "summary": "Track your job applications with the good habits built in: "
               "follow-ups, interviews, thank-you notes, referees and a job search log",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "auto_install": False,
    # `mail` pour le fil et les activités (les rappels), `calendar` pour mettre une
    # entrevue à son agenda. Pas de `crm` : une candidature n'est pas une vente.
    "depends": ["mail", "calendar"],
    "description": """
Job Search
==========

A personal tracker for one's own job applications, written for the person who
applies, not for a sales team.

* **Applications**: spotted, preparing, applied, interview, offer, accepted. Closing
  one says who closed it (the employer, you, or nobody: no response), which is
  what makes the response rate readable. The full advert is kept, since job
  boards remove it within weeks.
* **Follow-ups**: a first follow-up 10 days after applying (or the day after the
  date the employer promised), a second one 14 days later, then an offer to
  close the application as "no response". Delays are yours to set.
* **Interviews**: preparation checklist (research, STAR stories, questions,
  referees, logistics), a private event in your calendar, and a thank-you note
  due the day after, one per interviewer, drafted for you.
* **Offers**: answer deadline, written offer, negotiate before accepting.
* **Referees**: who agreed to be one, for which application they were named,
  a reminder to warn them before they are called.
* **My criteria**: salary floor, hours, duration, employers and words to avoid.
  An application that does not meet them is flagged, never dropped.
* **Dashboard and log**: applications per week against a goal, response rate by
  channel, median time to a reply; a dated log to export (Employment Insurance
  asks for one, kept six years), with a purge of the sensitive details.

Who sees what
-------------
Everything is **private**: each person sees only their own applications,
interviews, referees and criteria, administrators included. The thread of an
application holds internal notes only and notifies nobody. A system
administrator can still change the security rules themselves: the privacy holds
between people, not against whoever administers the database.

Labels are written in English; French (Canada) comes from ``i18n/fr_CA.po``.
""",
    "data": [
        "security/ir.model.access.csv",
        "security/job_search_rules.xml",
        "data/mail_activity_type_data.xml",
        "views/job_application_views.xml",
        "views/job_interview_views.xml",
        "views/job_referee_views.xml",
        "views/job_criteria_views.xml",
        "views/job_stats_views.xml",
        "wizard/job_close_wizard_views.xml",
        "wizard/job_journal_wizard_views.xml",
        "views/job_search_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_job_search/static/src/scss/job_search.scss",
        ],
    },
}
