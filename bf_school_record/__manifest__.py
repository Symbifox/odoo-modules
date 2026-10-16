{
    "name": "Symbifox École : dossier de l'élève",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "The student's file with the eight pieces the regulation requires, the register of "
               "enrolment, the transfer to the receiving school and the copy given to the person",
    "description": """
Student's file
==============

- a file per student and school, with the eight pieces of the Regulation respecting private
  educational institutions (E-9.1, r. 3, s. 7): which are there, which are missing, which do
  not apply and why;
- the register of enrolment (s. 8) read from the file: name, residence address, birth date,
  language of instruction and holder of parental authority, on screen and as a PDF;
- the transfer to the school that receives a student (Private Education Act s. 38.1): what
  is sent is chosen, the package carries a manifest with the SHA-256 of every file, and the
  school's own target date flags a late one;
- a structured copy of the file for the person it concerns (portability, P-39.1 s. 27 and
  A-2.1 s. 84);
- the export of every file for the Minister if the school closes;
- an access log that nobody edits.

The file is the administration's: teachers and families have no right on it.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_core"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_record_security.xml",
        "data/record_type_data.xml",
        "views/record_views.xml",
        "report/register_report.xml",
    ],
    "installable": True,
    "application": False,
}
