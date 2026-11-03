{
    "name": "BF Irregularities",
    "summary": "Shared registry of irregularities: late, off-process, hazardous, silent, orphaned or abnormal records",
    # Socle commun : chaque module déclare ses détecteurs
    #   dans un pont au lieu de recoder sa tâche planifiée, son ntfy et sa
    #   section de digest. Libellés en anglais dans la source, fr_CA.po
    #   porte le français.
"description": """
Irregularities
==============

A shared registry for what is quietly wrong in a database: a record that is
late, out of process, hazardous, silent, orphaned or abnormal.

* **A detector is data**: a code, a family, a severity, a threshold with its own
  label, a fallback owner. Disable it, retune it or reassign it without code.
* **A finding is unique** per detector and key, so an hourly run updates instead
  of duplicating, and resolves itself once the cause is gone.
* **Four detectors included**: an activity overdue past its threshold, an
  activity assigned to an archived user, a scheduled message that never left,
  an outgoing email in failure.
* **Each person sees their own**: a record rule on the owner, administrators
  excepted. Running a detector requires write access on it.

Other modules add their own detectors through a bridge instead of writing their
own scheduled job and their own notification.
    """,
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["mail"],
    "data": [
        "security/bf_irregularity_security.xml",
        "security/ir.model.access.csv",
        "data/bf_irregularity_detector_data.xml",
        "data/bf_irregularity_cron.xml",
        "views/bf_irregularity_views.xml",
        "views/bf_irregularity_detector_views.xml",
        "views/bf_irregularity_menus.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
