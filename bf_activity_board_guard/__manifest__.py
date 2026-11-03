{
    "name": "BF Activity Board Guard",
    "summary": "One activity on a model without mail.activity.mixin no longer "
               "takes the activity list and the board form down",
    "description": """
BF Activity Board Guard
=======================

`mail_activity_board` (OCA) adds a Reference field, `related_model_instance`,
whose selection lists the models that inherit `mail.activity.mixin`
(`ir.model.is_mail_activity`). Its compute writes "<model>,<id>" into it without
checking the model is in that selection, so `Reference.convert_to_cache` raises
`ValueError` on every READ of the field.

A computed field is computed for the whole batch being read, so ONE such
activity breaks the board's list view and the board's form view for every user,
not just its own row. This module writes the reference only when the field's own
selection accepts the model, and keeps the record reachable from the board's
form by falling back to the record's name.

It does not change which models may carry activities: that is the business of
each model's own module.
""",
    # 18.0.1.0.0 : première version. Deux modèles `mail.thread` sans
    #   `mail.activity.mixin` portaient des activités posées par XML-RPC (le
    #   bouton d'Odoo, lui, exige le mixin) : la liste du tableau tombait pour
    #   tout le monde. Un modèle peut aussi cesser de porter le mixin en
    #   gardant ses activités. La garde est versionnée, là où la réparation
    #   qu'invite ce défaut (éditer le fichier OCA en place) ne l'est pas.
    #   ⚠️ Le formulaire du tableau met le champ en <h1> DANS le bouton
    #   `open_origin` : sans la vue de ce module, une ligne gardée perdrait son
    #   libellé et il n'y aurait plus rien à viser pour ouvrir la fiche.
    # 18.0.1.0.1 : l'icône du module.
    "version": "18.0.1.0.1",
    "category": "Productivity/Discuss",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": ["mail_activity_board"],
    "data": [
        "views/mail_activity_view.xml",
    ],
    "installable": True,
    "application": False,
}
