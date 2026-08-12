# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Attention : `do_not_contact` existe déjà sur res.partner, apporté par
    # `privacy_consent` — c'est un champ CALCULÉ, non stocké, dérivé de
    # `privacy.contact.preference`, et `bf_cx_privacy` s'en sert pour bloquer
    # les sollicitations. On ne le redéfinit surtout pas : on ajoute notre
    # propre drapeau, et on consulte le sien à l'exécution (voir
    # `_outreach_is_blocked`).
    outreach_opt_out = fields.Boolean(
        string="Ne plus démarcher",
        tracking=True,
        copy=False,
        index=True,
        help="La personne a demandé de ne plus recevoir de sollicitation de "
        "démarchage. Elle ne peut plus être ajoutée à une campagne et la cadence "
        "s'arrête sur toutes ses cibles existantes.",
    )
    outreach_opt_out_date = fields.Datetime(
        string="Exclusion demandée le", readonly=True, copy=False
    )
    outreach_opt_out_reason = fields.Char(string="Motif de l'exclusion", copy=False)
    outreach_target_ids = fields.One2many(
        "bf.outreach.target", "partner_id", string="Cibles de démarchage"
    )
    outreach_target_count = fields.Integer(
        string="Nombre de cibles de démarchage", compute="_compute_outreach_target_count"
    )

    @api.depends("outreach_target_ids")
    def _compute_outreach_target_count(self):
        counts = {}
        if self.ids:
            counts = {
                partner.id: count
                for partner, count in self.env["bf.outreach.target"]._read_group(
                    [("partner_id", "in", self.ids)], ["partner_id"], ["__count"]
                )
            }
        for partner in self:
            partner.outreach_target_count = counts.get(partner.id, 0)

    def _outreach_is_blocked(self):
        """Le contact refuse-t-il d'être sollicité, pour quelque raison que ce soit ?

        Deux sources : notre propre drapeau, stocké, et le « ne pas contacter »
        du registre de consentement (`privacy_consent`), calculé en direct. Ce
        dernier est consulté au moment de l'action plutôt que recopié, car un
        consentement peut être retiré à tout moment.
        """
        self.ensure_one()
        if self.outreach_opt_out:
            return True
        if "do_not_contact" in self._fields:
            try:
                return bool(self.do_not_contact)
            except Exception:  # noqa: BLE001 — un registre absent ne bloque rien
                return False
        return False

    def write(self, vals):
        if vals.get("outreach_opt_out") and "outreach_opt_out_date" not in vals:
            vals = dict(vals, outreach_opt_out_date=fields.Datetime.now())
        return super().write(vals)

    def action_view_outreach_targets(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_outreach.action_bf_outreach_target"
        )
        action["domain"] = [("partner_id", "=", self.id)]
        action["context"] = {"default_partner_id": self.id}
        return action

    def action_outreach_do_not_contact(self):
        """Enregistre l'exclusion depuis la fiche contact."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Ne plus démarcher"),
            "res_model": "bf.outreach.exclude.wizard",
            "view_mode": "form",
            "target": "new",
            "context": dict(self.env.context, default_partner_ids=[(6, 0, self.ids)]),
        }
