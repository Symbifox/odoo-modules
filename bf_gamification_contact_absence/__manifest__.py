{
    "name": "Fox Quest : absences déclarées et constance",
    "summary": "Une absence déclarée, corrigée, archivée ou retirée refait la constance Fox Quest",
    # 18.0.1.0.0: first version. Fox Quest counts the streak in working days:
    #   an absence declared on the contact form (bf_contact_absence) pauses it.
    #   When the absence changes afterwards (declared, corrected, archived,
    #   unarchived, removed), the streak and the march towards the constance
    #   day off are recomputed. This bridge replaces the wrapper Fox Quest used
    #   to set at run time on the absence model: base_automation removed it in
    #   the process where an automated rule was edited, and archiving was not
    #   followed.
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": [
        "bf_gamification",
        "bf_contact_absence",
    ],
    "data": [],
}
