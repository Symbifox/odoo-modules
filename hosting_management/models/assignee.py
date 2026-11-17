"""À qui adresser un travail planifié, quand le repli habituel tombe sur un robot.

⚠️ Toutes les tâches planifiées de la base tournent sous ``__system__`` (uid 1), que
Odoo garde ``active = False`` dans toute base. Les replis de la forme
``record.user_id or self.env.user`` posent donc leurs activités et leurs courriels sur
un compte que personne n'ouvrira jamais. Mesuré sur une base réelle le 2026-11-13 : une
activité d'expiration perdue, et 20 services sur 104 sans responsable prêts à en
produire d'autres.
"""


def assignee_vivant(env, *candidats):
    """Premier usager ACTIF parmi les candidats, puis l'usager courant, puis l'admin.

    Un many2one vers un compte archivé se lit sans erreur : c'est ``active`` qu'il faut
    juger, pas la présence de la valeur. Dernier recours l'administrateur, et à défaut
    l'usager courant : une activité mal adressée se réassigne, une exception perd le
    rappel.
    """
    for user in candidats:
        # `not user.share` : jamais un compte portail, parce que `mail.activity.write`
        # abonne le nouvel assigné à l'enregistrement visé.
        if user and user.active and not user.share:
            return user
    if env.user.active:
        return env.user
    admin = env.ref("base.user_admin", raise_if_not_found=False)
    return admin if admin and admin.active else env.user
