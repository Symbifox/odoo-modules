{
    "name": "Symbifox École : transmission à Charlemagne",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "The student headcount, the local results and the ministerial exams sent to the "
               "Ministry of Education's Charlemagne system: XML, signed and encrypted, by SFTP",
    "description": """
Charlemagne
===========

Private schools transmit their student headcount and the results of their students to the
Ministry of Education through Charlemagne (teletransmission guide P560C, version 1.1 of
2026-09-21). This module does the whole circuit:

- the data the Ministry asks and the school's records do not hold (codes of languages and
  of places of birth, the names of the Ministry's register) are entered once per student and
  follow them from year to year;
- the declaration of the headcount (DJ), the local results (RJ), the registrations to the
  ministerial exams (IJ) and their results (UJ) are made from the school's records, as a
  creation the first time and a modification afterwards; a ledger of what the Ministry
  accepted tells which;
- every transaction is checked against the guide's structures before anything leaves, and a
  file with one that fails is not sent partially;
- the file is written in ISO-8859-1, signed with the school's key and encrypted with the
  Ministry's, put on the Ministry's SFTP server, and the control reports that come back are
  decrypted, checked against the Ministry's signature and read: each transaction is accepted,
  accepted with a message or rejected, with the Ministry's own words;
- a simulation mode runs the circuit with a stand-in for the Ministry, to rehearse.

What the Ministry checks with its own tables (the declaration alone has more than two
thousand lines of rules) is not reproduced: a transaction that passes here can still be refused
there, and the answer says why.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_gradebook", "bf_school_record", "bf_policy"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_charlemagne_security.xml",
        "data/charlemagne_data.xml",
        "views/charlemagne_views.xml",
    ],
    "installable": True,
    "application": False,
}
