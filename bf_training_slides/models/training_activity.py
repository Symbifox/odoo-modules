from odoo import _, api, fields, models


class BfTrainingActivity(models.Model):
    """Une activité du registre adossée à un cours en ligne."""

    _inherit = "bf.training.activity"

    slide_channel_id = fields.Many2one(
        "slide.channel", string="Cours en ligne", index=True, tracking=True,
        help="Le cours qui porte le contenu. La complétion du cours écrit une "
             "réalisation au registre.")
    slide_content_count = fields.Integer(
        string="Contenus publiés", compute="_compute_slide_content_count", store=True)
    slide_content_count_at_version = fields.Integer(
        string="Contenus à la dernière version", default=0, readonly=True)
    support_default = fields.Boolean(
        string="Accompagnement offert d'office", default=False,
        help="Repris sur les réalisations écrites automatiquement à la "
             "complétion. À ne cocher que si un accompagnement est réellement "
             "offert pendant l'apprentissage.")
    default_plan_id = fields.Many2one(
        "bf.training.plan", string="Plan par défaut",
        help="Le plan sous lequel la durée de ce cours est établie. Repris sur "
             "les réalisations écrites automatiquement.")
    content_drifted = fields.Boolean(
        string="Contenu dérivé", compute="_compute_content_drifted", store=True,
        help="Le cours n'a plus le même nombre de contenus publiés qu'au moment "
             "où la version a été fixée.")

    _sql_constraints = [
        ("canal_unique", "unique (slide_channel_id)",
         "Ce cours est déjà adossé à une activité."),
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
            "name": canal.name or _("Cours en ligne"),
            "slide_channel_id": canal.id,
            "mode": "elearning",
            "duration_hours": canal.total_time or 0.0,
        }
        base.update(valeurs or {})
        activite = self.create(base)
        activite.slide_content_count_at_version = activite.slide_content_count
        return activite
