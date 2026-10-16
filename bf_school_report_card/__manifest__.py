{
    "name": "Symbifox École : bulletins",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "Elementary and secondary report cards from the gradebook, sealed when issued, "
               "shown to the families with a read receipt, and the written communications "
               "the regulation requires",
    "description": """
Report cards
============

- a report card per student and term, generated from the gradebook: the figures are never
  typed, only the teachers' comments and the year-end decision;
- results as whole percentages by competency or component, the discipline result and the
  group average; on the last report card of the year the final result (20/20/60), the
  ministerial exam, the final group average and the units earned;
- issuing seals the report card: a JSON snapshot with its SHA-256, filed as the "school
  results" piece of the student's file; a correction is a new version that replaces it;
- the deadlines of the regulation (November 20, March 15, July 10) flag a late report card;
- the families read it in the portal, with a PDF and a read receipt; a student of age gets
  their own;
- the first written communication (October 15) and the monthly information (s. 29.2) are
  tracked, prepared and sent.

Preschool report cards (statements, not percentages) are not covered.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_gradebook", "bf_school_record", "bf_school_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_report_card_security.xml",
        "report/report_card.xml",
        "data/report_card_data.xml",
        "views/report_card_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
