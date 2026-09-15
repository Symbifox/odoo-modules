"""Ce qu'une fiche visée par des pastilles sait d'elles.

Un équipement, une salle, un contact : la fiche dit combien de pastilles la
visent, les ouvre d'un bouton, et en crée d'autres. Jusqu'à la 2.2.0, rien ne
menait d'une fiche à ses pastilles, et les formulaires des satellites se
contentaient d'un « Créez ensuite une pastille… » sans bouton.

⚠️ Le compte se lit en sudo : un interne voit qu'une salle a deux pastilles même
s'il ne peut rien y changer. Le bouton qui les ouvre passe, lui, par ses droits.
"""
from odoo import _, fields, models


class BfNfcTargetMixin(models.AbstractModel):
    _name = "bf.nfc.target.mixin"
    _description = "Fiche visée par des pastilles NFC"

    nfc_tag_count = fields.Integer(string="Pastilles", compute="_compute_nfc_tag_count")

    def _nfc_domaine_pastilles(self):
        """Les pastilles qui visent ces fiches. Un satellite élargit (une tournée → ses points)."""
        return [("res_model", "=", self._name), ("res_id", "in", self.ids)]

    def _nfc_comptes(self):
        """{id de fiche: nombre de pastilles}, en UNE requête pour tout le lot.

        ⚠️ Un regroupement, pas un compte par fiche : ce champ est posé sur
        ``res.partner``, et un ``read()`` sans liste de champs (une intégration, un
        export) le calcule pour chaque contact lu. Un satellite dont les pastilles ne
        visent pas la fiche elle-même (une tournée vise ses points) surcharge ici.
        """
        groupes = self.env["bf.nfc.tag"].sudo()._read_group(
            [("res_model", "=", self._name), ("res_id", "in", self.ids)],
            ["res_id"], ["__count"])
        return {res_id: nombre for res_id, nombre in groupes}

    def _compute_nfc_tag_count(self):
        self.nfc_tag_count = 0
        vraies = self.filtered(lambda f: f.id and not isinstance(f.id, models.NewId))
        if not vraies:
            return
        comptes = vraies._nfc_comptes()
        for fiche in vraies:
            fiche.nfc_tag_count = comptes.get(fiche.id, 0)

    def action_voir_pastilles(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pastilles de « %s »", self.display_name),
            "res_model": "bf.nfc.tag",
            "view_mode": "list,form",
            "domain": self._nfc_domaine_pastilles(),
            "context": {"default_res_model": self._name, "default_res_id": self.id},
        }

    def _nfc_fiches_a_etiqueter(self):
        """Les fiches à qui l'assistant donnera une pastille chacune."""
        return self

    def action_creer_pastilles(self):
        fiches = self._nfc_fiches_a_etiqueter()
        return {
            "type": "ir.actions.act_window",
            "name": _("Créer des pastilles"),
            "res_model": "bf.nfc.tag.lot",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": fiches._name,
                "default_res_ids": ",".join(str(i) for i in fiches.ids),
            },
        }


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "bf.nfc.target.mixin"]
