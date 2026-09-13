# -*- coding: utf-8 -*-
"""La détention vue depuis la fiche : ce qu'on détient, et qui nous détient."""
from odoo import _, api, fields, models

from odoo.addons.bf_org_chart.moteur import modele

#: Garde-fou de parcours. Une structure de détention réelle dépasse rarement
#: cinq étages; au-delà, c'est une saisie qui part en vrille, et on préfère
#: dessiner ce qu'on a plutôt que de remonter sans fin.
PROFONDEUR_MAX = 8


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "bf.org.chart.source"]

    ownership_out_ids = fields.One2many(
        "bf.ownership", "owner_id", string="Participations détenues")
    ownership_in_ids = fields.One2many(
        "bf.ownership", "owned_id", string="Détenue par")
    ownership_total = fields.Float(
        string="Capital inscrit", digits=(6, 3),
        compute="_compute_ownership_total",
        help="Somme des pourcentages inscrits sur cette société. Un total "
             "inférieur à 100 dit seulement que la structure n'est pas "
             "complètement connue.")
    ownership_state = fields.Selection(
        [("aucune", "Aucune détention inscrite"),
         ("partielle", "Structure partielle"),
         ("complete", "Structure complète"),
         ("excedent", "Total supérieur à 100 %")],
        string="État de la détention", compute="_compute_ownership_total")

    @api.depends("ownership_in_ids.percent", "ownership_in_ids.active",
                 "ownership_in_ids.date_fin")
    def _compute_ownership_total(self):
        for fiche in self:
            liens = fiche.ownership_in_ids.filtered(lambda l: l._est_en_vigueur())
            total = sum(liens.mapped("percent"))
            fiche.ownership_total = total
            if not liens:
                fiche.ownership_state = "aucune"
            elif total > 100.001:
                fiche.ownership_state = "excedent"
            elif total >= 99.999:
                fiche.ownership_state = "complete"
            else:
                fiche.ownership_state = "partielle"

    # --- contrat du socle ---------------------------------------------------
    def _org_chart_genres(self):
        genres = super()._org_chart_genres()
        return genres + [{
            "code": "detention",
            "libelle": _("Organigramme de détention"),
            "sequence": 20,
        }]

    def _org_chart_groupe(self):
        """Toutes les fiches reliées à celle-ci par la détention.

        On remonte les détenteurs ET on redescend les détenues, en largeur, en
        s'arrêtant à `PROFONDEUR_MAX`. Une structure se lit dans les deux sens :
        partir d'une filiale sans montrer son holding ne dit rien.

        Rend `(groupe, tronqué)` : le second dit que la structure continuait
        au-delà du garde-fou, et la carte doit l'annoncer.
        """
        self.ensure_one()
        Lien = self.env["bf.ownership"]
        vus, bord, profondeur = {self.id}, [self.id], 0
        while bord and profondeur < PROFONDEUR_MAX:
            liens = Lien.search([
                "|", ("owner_id", "in", bord), ("owned_id", "in", bord),
                ("active", "=", True),
            ])
            suivant = set()
            for lien in liens:
                if not lien._est_en_vigueur():
                    continue
                for fiche in (lien.owner_id, lien.owned_id):
                    if fiche.id not in vus:
                        vus.add(fiche.id)
                        suivant.add(fiche.id)
            bord = list(suivant)
            profondeur += 1
        # 🔴 Une structure plus profonde que le garde-fou était dessinée
        # TRONQUÉE, sans un mot : neuf sociétés sur quatorze, et rien ne le
        # disait. Une carte incomplète qui se tait est pire qu'une carte
        # absente.
        # ⚠️ `BaseModel` porte des `__slots__` : on ne colle pas un drapeau sur
        # un recordset, on le RETOURNE.
        return self.browse(sorted(vus)), bool(bord)

    def _org_chart_carte(self, code):
        if code != "detention":
            return super()._org_chart_carte(code)
        self.ensure_one()
        groupe, tronque = self._org_chart_groupe()
        liens = self.env["bf.ownership"].search([
            ("owner_id", "in", groupe.ids), ("owned_id", "in", groupe.ids),
            ("active", "=", True),
        ]).filtered(lambda l: l._est_en_vigueur())
        carte = modele.Carte(
            titre=self.display_name or _("Organigramme"),
            sous_titre=_("Structure de détention au %s",
                         fields.Date.to_string(fields.Date.context_today(self))),
            pied=_("Saisi dans Odoo, non vérifié au registre"),
            avertissements=(
                [_("La structure se poursuit au-delà de %s niveaux : le dessin "
                   "s'arrête là, et ce n'est pas le groupe entier.",
                   PROFONDEUR_MAX)]
                if tronque else []),
        )
        detenues = set(liens.mapped("owned_id").ids)
        for fiche in groupe:
            teinte = "bleu" if fiche.id not in detenues else "neutre"
            if fiche.ownership_state == "excedent":
                teinte = "rouge"
            elif fiche.ownership_state == "partielle":
                teinte = "ambre"
            note = ""
            if fiche.ownership_state == "partielle":
                note = _("Capital inscrit : %s %%", _format_pct(fiche.ownership_total))
            elif fiche.ownership_state == "excedent":
                note = _("Total inscrit : %s %%", _format_pct(fiche.ownership_total))
            carte.boites.append(modele.Boite(
                cle="s%s" % fiche.id,
                titre=fiche.name or _("Sans nom"),
                sous_titre=fiche.ref or fiche.vat or "",
                note=note,
                teinte=teinte,
                accent=fiche.id == self.id,
                lien="/odoo/contacts/%s" % fiche.id,
            ))
        for lien in liens:
            etiquette = lien._pourcentage_lisible()
            if lien.share_class:
                etiquette += " · %s" % lien.share_class
            carte.aretes.append(modele.Arete(
                de="s%s" % lien.owner_id.id,
                vers="s%s" % lien.owned_id.id,
                etiquette=etiquette,
                pointille=not lien.voting,
            ))
        carte.legende = [
            ("bleu", _("Détenteur ultime")),
            ("ambre", _("Structure partielle")),
            ("rouge", _("Total supérieur à 100 %")),
        ]
        return carte

    def action_org_chart_detention(self):
        return self.action_org_chart("detention")

    def action_ownership_liens(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Détention : %s", self.display_name),
            "res_model": "bf.ownership",
            "view_mode": "list,form",
            "domain": ["|", ("owner_id", "=", self.id), ("owned_id", "=", self.id)],
            "context": {"default_owned_id": self.id},
        }


def _format_pct(valeur):
    return ("%.3f" % (valeur or 0.0)).rstrip("0").rstrip(".") or "0"
