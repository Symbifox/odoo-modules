{
    "name": "Symbifox École : conduite et suivi mensuel",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "Breaches of the rules of conduct with graduated sanctions, parents' notices "
               "under the law, and the monthly news of Régime pédagogique s. 29.2",
    "description": """
Conduct and monthly follow-up
=============================

- rules of conduct from Regulation I-13.3, r. 10.01 (public and private
  schools alike) and the school's own; bullying, violence and sexual violence
  flagged;
- breaches with the graduated sanctions of r. 10.01 s. 5 and restorative
  measures: the next level after the previous breaches of the same rule this
  school year is suggested, a person decides;
- informing the parents: a suspension gives its reasons and the support
  measures (Education Act s. 96.27, Private Education Act s. 63.6); for sexual
  violence from 14, only with the student's consent;
- the family portal shows a breach once the family has been informed;
- annual report on bullying and violence (Private Education Act s. 63.8);
- monthly follow-up (Régime pédagogique s. 29.2): risk of failure, conduct,
  intervention plan; a to-do each month for the responsible teacher when the
  parents got no news, and the news emailed to the family.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_conduct_security.xml",
        "data/conduct_data.xml",
        "views/conduct_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
