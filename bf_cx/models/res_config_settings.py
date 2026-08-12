"""Settings panel.

Only scalar field types here (Boolean/Integer/Char/…): Text/Html fields on
res.config.settings crash the whole Settings page.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def set_values(self):
        icp = self.env["ir.config_parameter"].sudo()
        before = icp.get_param("bf_cx.complaint_ack_days", "2")
        super().set_values()
        after = icp.get_param("bf_cx.complaint_ack_days", "2")
        if before != after:
            # ack_deadline is a stored compute whose parameter is not in its
            # depends: re-flag open complaints so the new delay applies.
            complaints = self.env["bf.cx.complaint"].search(
                [("state", "not in", ("resolved", "closed"))]
            )
            self.env.add_to_compute(
                complaints._fields["ack_deadline"], complaints
            )

    bf_cx_auto_activity = fields.Boolean(
        string="Boucle fermée automatique",
        config_parameter="bf_cx.auto_activity",
        default=True,
        help="Créer automatiquement une activité de suivi pour le "
             "responsable quand un détracteur (NPS ≤ 6) ou une note "
             "insatisfaite arrive.",
    )
    bf_cx_ingest_ratings = fields.Boolean(
        string="Ingérer les évaluations par courriel",
        config_parameter="bf_cx.ingest_ratings",
        default=True,
        help="Copier chaque évaluation reçue (projets, tickets…) dans le "
             "registre unifié des feedbacks.",
    )
    bf_cx_solicitation_cooldown_days = fields.Integer(
        string="Anti-sursollicitation (jours)",
        config_parameter="bf_cx.solicitation_cooldown_days",
        default=30,
        help="Nombre de jours minimum entre deux demandes de feedback au "
             "même contact (vagues, post-rencontre, post-perte). 0 = aucun "
             "garde-fou.",
    )
    bf_cx_followup_days = fields.Integer(
        string="Échéance de boucle fermée (jours)",
        config_parameter="bf_cx.followup_days",
        default=2,
        help="Échéance de l'activité créée pour un détracteur ou une note "
             "insatisfaite (pratique : recontacter sous 24-72 h).",
    )
    bf_cx_nps_window_days = fields.Integer(
        string="Fenêtre NPS (jours)",
        config_parameter="bf_cx.nps_window_days",
        default=365,
        help="Fenêtre glissante du score NPS affiché (tableau de bord, "
             "digest, programmes). À petit volume, 365 jours est la seule "
             "fenêtre statistiquement honnête ; le score est masqué sous "
             "10 réponses.",
    )
    bf_cx_testimonial_activity = fields.Boolean(
        string="Activité pour les candidats témoignage",
        config_parameter="bf_cx.testimonial_activity",
        default=True,
        help="Créer une activité (échéance 5 j) quand un répondant accepte "
             "d'être cité - un opt-in est périssable.",
    )
    bf_cx_complaint_ack_days = fields.Integer(
        string="Délai d'accusé de réception (jours)",
        config_parameter="bf_cx.complaint_ack_days",
        default=2,
        help="Nombre de jours pour accuser réception d'une plainte "
             "(calcule l'échéance affichée sur chaque plainte).",
    )
