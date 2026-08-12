from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_note_default_reminder = fields.Selection(
        [
            ("none", "Aucun"),
            ("today", "Aujourd'hui"),
            ("tomorrow", "Demain"),
            ("2days", "+2 jours"),
            ("1week", "+1 semaine"),
        ],
        string="Rappel par défaut (Bloc-notes)",
        default="today",
        help="Rappel pré-sélectionné dans le dialogue de création rapide (Alt+N).",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["bf_note_default_reminder"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["bf_note_default_reminder"]

    def action_open_bf_note_preferences(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Préférences Bloc-notes",
            "res_model": "res.users",
            "res_id": self.env.user.id,
            "view_mode": "form",
            "view_id": self.env.ref("bf_bloc_notes.bf_note_user_pref_view_form").id,
            "target": "new",
        }
