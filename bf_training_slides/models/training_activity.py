from odoo import _, api, fields, models


class BfTrainingActivity(models.Model):
    """Une activité du registre adossée à un cours en ligne."""

    _inherit = "bf.training.activity"

    slide_channel_id = fields.Many2one(
        "slide.channel", string="Online course", index=True, tracking=True,
        help="The course that carries the content. Completing the course "
             "writes a completion to the register.")
    slide_content_count = fields.Integer(
        string="Published contents", compute="_compute_slide_content_count", store=True)
    slide_content_count_at_version = fields.Integer(
        string="Contents at the latest version", default=0, readonly=True)
    support_default = fields.Boolean(
        string="Support provided by default", default=False,
        help="Carried over to the completions written automatically when "
             "the course is completed. Tick this only if support is "
             "really provided during the learning.")
    default_plan_id = fields.Many2one(
        "bf.training.plan", string="Default plan",
        help="The plan under which the duration of this course is set "
             "out. Carried over to the completions written automatically.")
    content_drifted = fields.Boolean(
        string="Content drifted", compute="_compute_content_drifted", store=True,
        help="The course no longer has the same number of published "
             "contents as it had when the version was set.")

    _sql_constraints = [
        ("canal_unique", "unique (slide_channel_id)",
         "This course is already linked to an activity."),
    ]

    @api.depends("slide_channel_id",
                 "slide_channel_id.slide_ids.is_published",
                 "slide_channel_id.slide_ids.active",
                 "slide_channel_id.slide_ids.is_category")
    def _compute_slide_content_count(self):
        for rec in self:
            contenus = rec.slide_channel_id.slide_ids.filtered(
                lambda s: s.is_published and s.active and not s.is_category)
            rec.slide_content_count = len(contenus)

    @api.depends("slide_content_count", "slide_content_count_at_version",
                 "slide_channel_id")
    def _compute_content_drifted(self):
        for rec in self:
            rec.content_drifted = bool(
                rec.slide_channel_id
                and rec.slide_content_count != rec.slide_content_count_at_version)

    def action_bump_version(self):
        """Monter la version, et figer le compte de contenus de cette version."""
        resultat = super().action_bump_version()
        for rec in self:
            if rec.slide_channel_id:
                rec.slide_content_count_at_version = rec.slide_content_count
        return resultat

    def action_acknowledge_drift(self):
        """Prendre acte du changement sans faire refaire le cours.

        Une coquille corrigée ne vaut pas une nouvelle formation. Ce geste fige
        le compte sans monter la version.
        """
        for rec in self:
            rec.slide_content_count_at_version = rec.slide_content_count
        return True

    @api.model
    def _pour_canal(self, canal, valeurs=None):
        """L'activité qui porte ce cours, créée si elle n'existe pas."""
        if not canal:
            return self.browse()
        existante = self.search([("slide_channel_id", "=", canal.id)], limit=1)
        if existante:
            return existante
        base = {
            "name": canal.name or _("Online course"),
            "slide_channel_id": canal.id,
            "mode": "elearning",
            "duration_hours": canal.total_time or 0.0,
        }
        base.update(valeurs or {})
        activite = self.create(base)
        activite.slide_content_count_at_version = activite.slide_content_count
        return activite
