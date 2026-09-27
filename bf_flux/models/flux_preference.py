# -*- coding: utf-8 -*-
"""La préférence d'une personne pour une liste : désabonnement et résumé.

L'appartenance vient du service ou du projet, jamais d'une saisie. Ce que la
personne règle, c'est seulement ce qu'elle en reçoit.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# Délai minimal entre deux résumés. Un peu moins d'un jour ou d'une semaine,
# pour qu'un cron qui glisse de quelques minutes ne saute pas un envoi.
# Un résumé de 300 titres ne se lit pas : au-delà, le lien mène au reste.
PAR_LISTE = 25
# La mise en page de la maison quand `bluefox_branding` est installé ; sinon
# celle d'Odoo. Ni l'une ni l'autre n'est une dépendance.
MISES_EN_PAGE = ("bluefox_branding.bf_mail_layout", "mail.mail_notification_light")

DELAIS = {"quotidien": timedelta(hours=20), "hebdomadaire": timedelta(days=6, hours=20)}


class FluxPreference(models.Model):
    _name = "bf.flux.preference"
    _description = "Préférence de réception d'une liste de flux"
    _order = "liste_id"

    liste_id = fields.Many2one(
        "bf.flux.liste", string="Liste", required=True, ondelete="cascade",
        index=True)
    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True, default=lambda s: s.env.user)
    desabonne = fields.Boolean(
        "Désabonné", help="Ne reçoit plus rien de cette liste, ni au canal ni "
                          "par courriel, sans quitter son service ni son projet.")
    frequence = fields.Selection(
        [("quotidien", "Chaque jour"), ("hebdomadaire", "Chaque semaine"),
         ("aucun", "Pas de résumé (canal seulement)")],
        string="Résumé courriel", default="quotidien", required=True)
    dernier_envoi = fields.Datetime("Dernier résumé", readonly=True)

    _sql_constraints = [
        ("liste_user_unique", "UNIQUE(liste_id, user_id)",
         "Une seule préférence par personne et par liste."),
    ]

    def write(self, vals):
        # La règle d'enregistrement se vérifie AVANT l'écriture : sans cette
        # garde, on pouvait réattribuer sa préférence à un collègue, désabonné.
        if {"user_id", "liste_id"} & set(vals) and not (
                self.env.su or self.env.user.has_group("bf_flux.group_flux_gestion")):
            raise AccessError(_("Une préférence reste à la personne et à la liste qui l'ont créée."))
        res = super().write(vals)
        if "desabonne" in vals:
            self.liste_id.sudo()._flux_synchroniser_canal()
        return res

    @api.model
    def _flux_preference(self, liste, user):
        pref = self.sudo().search(
            [("liste_id", "=", liste.id), ("user_id", "=", user.id)], limit=1)
        return pref or self.sudo().create({"liste_id": liste.id, "user_id": user.id})

    @api.model
    def action_mes_abonnements(self):
        """Ouvre les préférences de l'usager, en créant celles qui manquent."""
        listes = self.env["bf.flux.liste"].sudo().search([("active", "=", True)])
        for liste in listes:
            desabonnes = liste.preference_ids.filtered("desabonne").user_id
            if self.env.user in (liste.membre_user_ids | desabonnes):
                self._flux_preference(liste, self.env.user)
        return {
            "type": "ir.actions.act_window",
            "name": _("Mes abonnements"),
            "res_model": "bf.flux.preference",
            "view_mode": "list",
            "domain": [("user_id", "=", self.env.uid)],
        }

    # ------------------------------------------------------------------ résumé

    @api.model
    def _cron_resume(self):
        """Un courriel par personne, qui regroupe ses listes dues."""
        maintenant = fields.Datetime.now()
        par_user = defaultdict(list)
        for liste in self.env["bf.flux.liste"].sudo().search([("active", "=", True)]):
            for user in liste.membre_user_ids:
                pref = self._flux_preference(liste, user)
                if pref.desabonne or pref.frequence == "aucun":
                    continue
                if pref.dernier_envoi and maintenant - pref.dernier_envoi < DELAIS[pref.frequence]:
                    continue
                depuis = pref.dernier_envoi or (maintenant - DELAIS[pref.frequence])
                retenues = self.env["bf.flux.retenue"].sudo().search([
                    ("liste_id", "=", liste.id), ("etat", "=", "retenu"),
                    ("rattrapage", "=", False), ("create_date", ">", depuis),
                ], order="date_publication desc")
                # Ce que la personne a déjà lu (dans Odoo, Discuss ou un résumé
                # précédent) ne revient pas dans le suivant.
                lus = set(self.env["bf.flux.lecture"].sudo().search([
                    ("user_id", "=", user.id), ("element_id", "in", retenues.element_id.ids),
                ]).element_id.ids)
                retenues = retenues.filtered(lambda r: r.element_id.id not in lus)
                par_user[user].append((pref, liste, retenues))
        gabarit = self.env.ref("bf_flux.mail_template_resume").sudo()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        mise_en_page = self._flux_mise_en_page()
        for user, blocs in par_user.items():
            pleins = [(l, r) for _p, l, r in blocs if r]
            for pref, _l, _r in blocs:
                pref.dernier_envoi = maintenant
            if not pleins or not user.partner_id.email:
                continue
            donnees = [{
                "nom": liste.name,
                "total": len(rets),
                "autres": max(0, len(rets) - PAR_LISTE),
                "url": f"{base}/odoo/bf.flux.liste/{liste.id}",
                "elements": [{"titre": r.titre, "lien": r.element_id._flux_url_lire(),
                              "emetteur": r.emetteur or ""}
                             for r in rets[:PAR_LISTE]],
            } for liste, rets in pleins]
            total = sum(b["total"] for b in donnees)
            gabarit.with_context(
                lang=user.lang,
                flux_blocs=donnees,
                flux_sujet=self.with_context(lang=user.lang).env._(
                    "Vos flux : %s élément(s) retenu(s)", total),
                flux_url_abonnements=f"{base}/odoo/mes-flux",
            ).send_mail(
                user.id, force_send=False,
                email_values={"recipient_ids": [(6, 0, user.partner_id.ids)], "email_to": False},
                email_layout_xmlid=mise_en_page,
            )
        return True

    @api.model
    def _flux_mise_en_page(self):
        """La mise en page de la maison si elle est installée, sinon la légère."""
        for xmlid in MISES_EN_PAGE:
            if self.env.ref(xmlid, raise_if_not_found=False):
                return xmlid
        return False
