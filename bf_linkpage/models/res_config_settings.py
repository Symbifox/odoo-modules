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
        string="Create a page for each employee",
        help="A periodic run creates the missing page for active "
             "employees who have a contact or a user account. Without hr "
             "installed, the setting has no effect and says so in the log.",
        config_parameter="bf_linkpage.autocreate",
    )
    bf_linkpage_autocreate_state = fields.Selection(
        [("draft", "As a draft"), ("published", "Published right away")],
        string="State at creation",
        default="published",
        help="A published page exposes the person's work email and phone "
             "on a public URL. As a draft, the page exists and is already "
             "filled in, but returns a 404 until someone publishes it.",
        config_parameter="bf_linkpage.autocreate_state",
    )
    bf_linkpage_autorefresh = fields.Boolean(
        string="Refresh pages that follow a template",
        help="On each run, reapplies the template to pages that have one. "
             "Links added by hand on a page are kept; only those from the "
             "template are replaced.",
        config_parameter="bf_linkpage.autorefresh",
    )
    bf_linkpage_oneoff_expiry_days = fields.Integer(
        string="One-off page lifetime (days)",
        default=90,
        help="A one-off page has no owner: it is armed with an expiry at "
             "creation. A missing or zero value falls back to 90 days.",
        config_parameter="bf_linkpage.oneoff_expiry_days",
    )

    bf_linkpage_org_expiry_days = fields.Integer(
        string="Organization page lifetime (days)",
        default=365,
        help="An organization page is created with an expiry date: this "
             "is what prevents a client welcome page from staying open "
             "three years after the engagement ends. Zero = no expiry, at "
             "your own risk.",
        config_parameter="bf_linkpage.org_expiry_days",
    )
    bf_linkpage_org_warn_days = fields.Integer(
        string="Warn before expiry (days)",
        default=30,
        help="How many days before closing the page warns its advisor, in "
             "the chatter and with an activity. Without this warning, the "
             "expiry would show up as a 404 on the client's side.",
        config_parameter="bf_linkpage.org_warn_days",
    )
    bf_linkpage_guide_url_fr = fields.Char(
        string="User guide (French)",
        help="The address served by the \"User guide\" source. Empty: the "
             "link is not shown, rather than pointing to a guessed "
             "address.",
        config_parameter="bf_linkpage.guide_url_fr",
    )
    bf_linkpage_guide_url_en = fields.Char(
        string="User guide (English)",
        config_parameter="bf_linkpage.guide_url_en",
    )

    # Lecture seule, pour répondre sur place à « est-ce que ça tourne ? »
    bf_linkpage_cron_next = fields.Datetime(
        string="Next run",
        compute="_compute_bf_linkpage_cron",
    )
    bf_linkpage_cron_active = fields.Boolean(
        string="Scheduled run active",
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
