"""« Changer le nom d'usage » : renommer une personne sans laisser l'ancien nom
au fil.

Renommer une fiche à la main écrit « ancien nom → nouveau nom » dans son
chatter, lisible par quiconque l'ouvre et envoyé à ses abonnés. Ce geste écrit le nom sans suivi, sur la fiche et sur
l'employé lié, et, pour le groupe « Noms légaux » seulement :

- range l'ancien nom comme nom légal, si la fiche n'en a pas encore ;
- efface l'historique des changements de nom de la fiche.

⚠️ Ce qui est déjà parti (notifications de suivi envoyées par courriel aux
abonnés) ne se rattrape pas, et l'historique des AUTRES fiches n'est pas
réécrit : une tâche dont ce contact était le client garde, dans sa trace, le
nom d'alors. L'aide de l'option le dit.

Un assistant porte le nouveau nom avant qu'il ne soit écrit : seul celui qui
l'a ouvert le lit (règle d'accès sur `create_uid`).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.res_partner import LEGAL_NAME_GROUP


class BfPreferredNameRename(models.TransientModel):
    _name = "bf.preferred.name.rename"
    _description = "Change preferred name"

    partner_id = fields.Many2one(
        "res.partner", string="Contact", required=True, ondelete="cascade")
    # Lu avec les droits de l'usager : un related est lu en sudo par défaut,
    # et le nom d'une fiche qu'on ne peut pas lire sortirait par l'assistant.
    current_name = fields.Char(
        related="partner_id.name", string="Current name", compute_sudo=False)
    new_name = fields.Char(string="New preferred name", required=True)
    current_legal_name = fields.Char(
        related="partner_id.bf_legal_name", string="Current legal name",
        compute_sudo=False, groups=LEGAL_NAME_GROUP)
    keep_old_as_legal = fields.Boolean(
        string="Keep the current name as legal name",
        groups=LEGAL_NAME_GROUP,
        help="Store the current name in the personal legal name, which only "
             "the “Legal names” group can read. Only when the contact has no "
             "legal name yet.")
    erase_history = fields.Boolean(
        string="Erase the name-change history",
        groups=LEGAL_NAME_GROUP,
        help="Remove every “old name → new name” trace from this contact's "
             "chatter (and from the linked employee's). Other records that "
             "tracked this contact, such as the customer of a task, keep the "
             "name it had then. Notifications already emailed to followers "
             "cannot be recalled.")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        context = self.env.context
        if (not values.get("partner_id") and context.get("active_model") == "res.partner"
                and context.get("active_id")):
            values["partner_id"] = context["active_id"]
        return values

    def action_apply(self):
        self.ensure_one()
        partner = self.partner_id
        partner.check_access("write")
        new_name = (self.new_name or "").strip()
        if not new_name:
            raise UserError(_("The new preferred name cannot be empty."))
        old_name = partner.name

        # Les deux options sont réservées au groupe : relues en sudo après le
        # contrôle, parce qu'un usager hors du groupe ne peut pas lire leurs
        # champs, et qu'il a pu les cocher par RPC (`create` ne vérifie pas
        # `groups=`).
        legal = self.env.user.has_group(LEGAL_NAME_GROUP)
        options = self.sudo()
        keep_old = legal and options.keep_old_as_legal
        erase = legal and options.erase_history

        skipped = self.env["res.partner"]
        if new_name != old_name:
            partner.with_context(mail_notrack=True).write({"name": new_name})
            skipped = self._rename_employees(partner, new_name)
        if keep_old and old_name and not partner.sudo().bf_legal_name:
            partner.sudo().bf_legal_name = old_name
        if erase:
            partner._bf_erase_name_history()

        if not skipped:
            return {"type": "ir.actions.act_window_close"}
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning",
                "sticky": True,
                "message": _(
                    "The contact is renamed, but not its employee record, "
                    "which you are not allowed to change. Ask Human Resources "
                    "to use “Change preferred name” too."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _rename_employees(self, partner, new_name):
        """Renomme, sans suivi, les employés liés que l'usager peut modifier.

        Le nom de `hr.employee` est suivi lui aussi. On cherche en sudo (un
        interne sans droits RH ne voit pas `hr.employee`), mais on écrit avec
        les droits de l'usager. Rend la fiche quand des employés n'ont pas pu
        être renommés.
        """
        if "employee_ids" not in partner._fields:
            return self.env["res.partner"]
        employees = partner.sudo().with_context(active_test=False).employee_ids
        employees = employees.filtered(lambda e: e.name != new_name).with_env(self.env)
        if not employees:
            return self.env["res.partner"]
        if not employees.has_access("write"):
            return partner
        employees.with_context(mail_notrack=True).write({"name": new_name})
        return self.env["res.partner"]
