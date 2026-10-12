{
    "name": "Record Privacy Guard",
    # 18.0.1.0.0 : première version. Deux fuites du cœur d'Odoo 18 :
    #   en mode debug, l'erreur d'accès nommait les fiches refusées ; les
    #   lignes d'abonnés se lisaient sans égard à la fiche suivie.
    "version": "18.0.1.0.0",
    "category": "Hidden/Tools",
    "summary": "Access errors stop naming the records you cannot read, and follower "
               "lines follow the access of the record they follow",
    "description": """
Record Privacy Guard
====================

Two leaks of Odoo 18 that hold for every model with records hidden from some users
(personal notes, mailboxes, private conversations, expenses, invoices...).

* In debug mode, which every internal user can open, the access error of a record
  rule listed the refused records by name. The error keeps the model, the id and
  the rules at fault; the name is replaced. Administrators keep the names.
* Follower lines (mail.followers) were readable by every internal user. A line is
  now read only by someone who can read the record it follows.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [],
    "installable": True,
    "application": False,
}
