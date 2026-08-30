"""Les réglages du module, dans Paramètres.

Tout est stocké en `ir.config_parameter` plutôt qu'en champs de société : ces
réglages décrivent le comportement du module sur l'instance, pas une politique
par société. Le module lit d'ailleurs déjà `bf_linkpage.oneoff_expiry_days` de
cette façon ; ce formulaire ne fait que cesser d'obliger à passer par les
paramètres techniques pour le voir.
"""

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_linkpage_autocreate = fields.Boolean(
        string="Créer une page pour chaque employé",
        help="Une passe périodique crée la page manquante des employés actifs "
             "qui ont un contact ou un compte utilisateur. Sans hr installé, "
             "le réglage reste sans effet et le dit au journal.",
        config_parameter="bf_linkpage.autocreate",
    )
    bf_linkpage_autocreate_state = fields.Selection(
        [("draft", "En brouillon"), ("published", "Publiée tout de suite")],
        string="État à la création",
        default="published",
        help="Une page publiée expose le courriel et le téléphone de travail "
             "de la personne sur une URL publique. En brouillon, la page existe "
             "et est déjà garnie, mais rend 404 tant que personne ne la publie.",
        config_parameter="bf_linkpage.autocreate_state",
    )
    bf_linkpage_autorefresh = fields.Boolean(
        string="Rafraîchir les pages qui suivent un gabarit",
        help="À chaque passe, réapplique le gabarit sur les pages qui en ont "
             "un. Les liens ajoutés à la main sur une page sont conservés ; "
             "seuls ceux venus du gabarit sont remplacés.",
        config_parameter="bf_linkpage.autorefresh",
    )
    bf_linkpage_oneoff_expiry_days = fields.Integer(
        string="Durée de vie d'une page ponctuelle (jours)",
        default=90,
        help="Une page ponctuelle n'a pas de propriétaire : elle est armée "
             "d'une expiration à la création. Une valeur absente ou nulle "
             "retombe sur 90 jours.",
        config_parameter="bf_linkpage.oneoff_expiry_days",
    )

    # Lecture seule, pour répondre sur place à « est-ce que ça tourne ? »
    bf_linkpage_cron_next = fields.Datetime(
        string="Prochaine passe",
        compute="_compute_bf_linkpage_cron",
    )
    bf_linkpage_cron_active = fields.Boolean(
        string="Passe planifiée active",
        compute="_compute_bf_linkpage_cron",
    )

    @api.depends_context("uid")
    def _compute_bf_linkpage_cron(self):
        cron = self.env.ref("bf_linkpage.cron_sync_employees", raise_if_not_found=False)
        for record in self:
            record.bf_linkpage_cron_next = cron.nextcall if cron else False
            record.bf_linkpage_cron_active = bool(cron and cron.active)

    def action_bf_linkpage_sync_now(self):
        self.ensure_one()
        return self.env["bf.linkpage"].action_sync_now()

    def action_bf_linkpage_open_cron(self):
        self.ensure_one()
        cron = self.env.ref("bf_linkpage.cron_sync_employees")
        return {
            "type": "ir.actions.act_window",
            "res_model": "ir.cron",
            "res_id": cron.id,
            "view_mode": "form",
            "target": "current",
        }
