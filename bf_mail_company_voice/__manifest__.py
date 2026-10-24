# -*- coding: utf-8 -*-
{
    'name': 'Company Voice for Automatic Notices',
    # 18.0.1.0.2 : société fixe en paramètre (une base dont l'usager public ou les
    #   crons appartiennent à une seconde société qui ne doit pas parler).
    # 18.0.1.0.1 : l'avis d'activité gardait le nom mais perdait sa phrase (xpath replace).
    # 18.0.1.0.0 : sorti de bf_onboarding_base pour poser sans remonter
    #   les 159 modules qui dépendent de ce dernier.
    'version': '18.0.1.0.2',
    'summary': 'Automatic notices speak for the company, never as « Public user » or a bot.',
    'description': """
Automatic notices speak for the company.

- What the system writes while a public page is served (a document signed, a
  date poll answered, a slot booked, a backup report pushed by an agent) is
  credited to the company's contact instead of « Public user ». A comment a
  visitor typed keeps its author.
- A message sent from the company's address with no name, or under a bot's
  name (OdooBot, the public user), leaves under the company's name. A name
  someone chose (a newsletter sender, a team signature) is kept.
- The « X has just assigned you an activity » notice names the company when
  the activity was created by a public page.

System parameter ``bf_mail_company_voice.company_voice``: ``record`` (default,
the record's company speaks), ``session`` (the session's company), or a company
id (that one company always speaks).
""",
    'category': 'Productivity/Discuss',
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': ['mail'],
    'data': [
        'views/mail_activity_assigned.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
