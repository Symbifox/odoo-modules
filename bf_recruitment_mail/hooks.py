# Part of bf_recruitment_mail. Voir LICENSE.
"""Retirer les traductions périmées des gabarits qu'on vient de récrire.

🔴 **Le piège, et il annule tout le module en silence.** Sur un locataire
installé en français, les quatre gabarits de `hr_recruitment` portent une valeur
`fr_CA` traduite depuis l'anglais d'origine. Récrire le champ dans un fichier de
données ne touche que la **source** (`en_US`) : la valeur `fr_CA` survit et
c'est elle qui est rendue. Les sujets restent donc les quatre mêmes, le corps
reste celui d'Odoo, et rien ne le signale : le module s'installe, les
identifiants XML sont bien repris, et le candidat reçoit exactement ce qu'il
recevait avant.

Constaté le 2026-08-31 sur `demo.symbifox.com` : `subject->>'en_US'` portait le
nouveau texte, `subject->>'fr_CA'` l'ancien, et l'instance tourne en `fr_CA`.

**La correction est de retirer les autres langues**, pas d'en ajouter : le texte
que ce module écrit est du français, et c'est lui la source.

⚠️ Corollaire pour la publication : tant qu'aucun `.po` n'accompagne le module,
un locataire anglophone recevra le texte français. C'est une traduction à
fournir, pas un défaut à contourner ici.
"""

import logging

_logger = logging.getLogger(__name__)

_GABARITS = (
    "hr_recruitment.email_template_data_applicant_congratulations",
    "hr_recruitment.email_template_data_applicant_interest",
    "hr_recruitment.email_template_data_applicant_refuse",
    "hr_recruitment.email_template_data_applicant_not_interested",
)
_CHAMPS = ("subject", "body_html")


def _reduire_a_la_source(env):
    """Ne garder que `en_US` sur les champs traduits qu'on vient de récrire."""
    ids = []
    for xmlid in _GABARITS:
        gabarit = env.ref(xmlid, raise_if_not_found=False)
        if gabarit:
            ids.append(gabarit.id)
    if not ids:
        _logger.warning("bf_recruitment_mail : aucun gabarit à nettoyer")
        return
    for champ in _CHAMPS:
        # jsonb : on reconstruit l'objet avec la seule clé source. Passer par
        # l'ORM écrirait dans la langue de l'utilisateur courant, ce qui est
        # exactement le problème qu'on corrige.
        env.cr.execute(
            """
            UPDATE mail_template
               SET {champ} = jsonb_build_object('en_US', {champ} ->> 'en_US')
             WHERE id IN %s
               AND {champ} IS NOT NULL
               AND {champ} ->> 'en_US' IS NOT NULL
            """.format(champ=champ),
            (tuple(ids),),
        )
        _logger.info(
            "bf_recruitment_mail : %s ligne(s) réduites à la source sur %s",
            env.cr.rowcount, champ,
        )
    env.invalidate_all()


def ouvrir_a_la_mise_a_jour(cr):
    """Rendre les quatre gabarits modifiables par une mise à niveau.

    🔴 **Le deuxième no-op silencieux, et il est pire que celui des
    traductions.** Les gabarits de `hr_recruitment` sont déclarés `noupdate="1"`
    dans leur module d'origine. Odoo garde ce drapeau sur la ligne
    `ir_model_data`, et il **refuse alors toute réécriture lors d'une mise à
    niveau**, y compris depuis le fichier de données d'un AUTRE module.

    Conséquence : ce module écrit ses gabarits **à l'installation, et une seule
    fois**. Chaque correction apportée ensuite au XML est ignorée sans un mot.
    Le `-u` passe, les journaux sont muets, et le locataire garde la version
    précédente. Constaté le 2026-08-31 : trois réécritures de suite sont restées
    sans effet sur le banc.

    On lève donc le drapeau, une fois. À partir de là, ce module gouverne ces
    quatre enregistrements.

    ⚠️ Contrepartie assumée : une mise à niveau de `hr_recruitment` SEUL
    réappliquerait les textes d'Odoo. Comme ce module se charge après lui dans
    le graphe, une mise à niveau qui les emporte tous les deux se termine sur
    nos textes. Il faut donc mettre ce module à niveau après toute montée de
    version d'Odoo, et c'est le prix à payer pour que les corrections arrivent.
    """
    cr.execute(
        """
        UPDATE ir_model_data SET noupdate = FALSE
         WHERE model = 'mail.template' AND module = 'hr_recruitment'
           AND name IN %s AND noupdate
        """,
        (tuple(x.split(".", 1)[1] for x in _GABARITS),),
    )
    _logger.info(
        "bf_recruitment_mail : %s gabarit(s) ouverts à la mise à niveau", cr.rowcount)


def post_init_hook(env):
    ouvrir_a_la_mise_a_jour(env.cr)
    _reduire_a_la_source(env)
