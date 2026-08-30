"""Réglages d'organisation de la gestion des courriels.

Avant 18.0.4.0.0 ce fichier portait l'unique compte IMAP global de la base.
Le pivot vers des comptes par usager (``bf.email.account``) l'a vidé, et il
n'a longtemps servi qu'à garder l'xmlid historique
``bf_email_management.action_bf_email_settings`` résolvable.

Il reprend du service en 18.0.11.1.0 pour ce qui n'appartient à personne en
particulier : l'affichage des dossiers IMAP dans la boîte de réception est
une décision d'organisation, pas une préférence d'affichage. Voir #24976.
"""

from odoo import _, api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_email_show_imap_folders = fields.Boolean(
        string="Dossiers IMAP dans la boîte de réception",
        default=True,
        help="Ajoute les vrais dossiers du serveur de courriel sous la "
             "boîte de réception. Le contenu affiché reste des lignes de "
             "cette base : chaque message garde ses actions Traité, Router "
             "et Ajouter, ainsi que son lien vers la fiche où il est classé.\n"
             "Décocher retire le groupe pour tout le monde — utile si "
             "l'organisation juge que naviguer par dossier de serveur "
             "détourne du classement dans les fiches.",
    )
    bf_email_folder_cache_minutes = fields.Integer(
        string="Fraîcheur de l'arborescence (minutes)",
        default=60,
        help="Durée pendant laquelle la liste des dossiers relevée sur le "
             "serveur est réutilisée sans le rappeler. 0 = relire à chaque "
             "affichage (déconseillé : un aller-retour IMAP par ouverture).",
    )

    bf_email_popup_enabled = fields.Boolean(
        string="Avis à l'arrivée d'un courriel",
        default=False,
        help="Autorise l'avis qui s'affiche dans Odoo quand un courriel "
             "entre, pour toute la base. Chacun règle ensuite le sien sur "
             "ses propres comptes IMAP : éphémère, persistant, ou aucun.\n\n"
             "Décoché par défaut, y compris sur une base qui vient de "
             "recevoir la mise à jour : personne ne découvre un avis qu'il "
             "n'a pas demandé. L'avis reste dans le navigateur ; la poussée "
             "vers le téléphone est un transport distinct.",
    )

    # ------------------------------------------------------------------
    # ⚠️ Lecture et écriture explicites plutôt que `config_parameter`.
    #
    # `res.config.settings.set_values` appelle `set_param(clé, False)` quand
    # une case est décochée, et `ir.config_parameter.set_param` SUPPRIME la
    # rangée sur une valeur fausse. Le paramètre absent retomberait alors sur
    # le défaut du code — « 1 » — et la case ne pourrait jamais rester
    # décochée : on la décoche, on enregistre, elle revient cochée. Écrire
    # « 0 » explicitement est la seule façon de rendre le « non » persistant.
    # ------------------------------------------------------------------
    @api.model
    def get_values(self):
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        res["bf_email_show_imap_folders"] = (
            self.env["bf.email"]._inbox_imap_folders_enabled()
        )
        try:
            minutes = int(ICP.get_param("bf_email.folder_cache_minutes", 60))
        except (TypeError, ValueError):
            minutes = 60
        res["bf_email_folder_cache_minutes"] = minutes
        res["bf_email_popup_enabled"] = (
            self.env["bf.email.popup"]._instance_enabled()
        )
        return res

    def set_values(self):
        super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(
            "bf_email.show_imap_folders",
            "1" if self.bf_email_show_imap_folders else "0",
        )
        ICP.set_param(
            "bf_email.folder_cache_minutes",
            str(max(0, self.bf_email_folder_cache_minutes or 0)),
        )
        ICP.set_param(
            "bf_email.popup_enabled",
            "1" if self.bf_email_popup_enabled else "0",
        )

    def action_bf_email_refresh_imap_folders(self):
        """Relever tout de suite les dossiers de mes comptes IMAP.

        Un dossier créé dans le webmail n'apparaît ici qu'au prochain
        rafraîchissement du cache. Ce bouton n'attend pas.
        """
        self.ensure_one()
        accounts = self.env["bf.email"]._inbox_imap_accounts()
        total = sum(len(a._get_imap_folders(force=True)) for a in accounts)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Dossiers relevés"),
                "message": _(
                    "%(f)s dossier(s) sur %(a)s compte(s) IMAP.",
                    f=total, a=len(accounts),
                ),
                "sticky": False,
            },
        }
