from odoo import api, fields, models


class BfTrainingRecord(models.Model):
    """La réalisation née d'un accusé porte la VERSION qu'elle atteste."""

    _inherit = "bf.training.record"

    distribution_id = fields.Many2one(
        "project.document.distribution", string="Remise du document",
        ondelete="set null", index=True)
    document_version_id = fields.Many2one(
        "project.document.version", string="Version lue", ondelete="restrict",
        help="La version exacte que la personne a confirmé avoir lue. C'est elle "
             "qui décide si l'accusé vaut encore.")
    document_version_number = fields.Char(
        related="document_version_id.version_number", string="Numéro de version")

    @api.depends("document_version_id",
                 "activity_id.document_id.latest_version_id",
                 "activity_id.reopen_on_change")
    def _compute_is_outdated(self):
        """Pour une activité adossée à un document, la péremption se lit sur les
        VERSIONS, pas sur un numéro interne.

        ⚠️ Le socle compare deux entiers qu'il gère lui-même. Ici la vérité est
        ailleurs : c'est `latest_version_id` du document qui dit ce qui est en
        vigueur. Fabriquer un entier à côté aurait donné deux sources pour un
        même fait, et elles auraient divergé au premier document repris à la main.
        """
        super()._compute_is_outdated()
        for rec in self:
            document = rec.activity_id.document_id
            if not document or not rec.document_version_id:
                continue
            if not rec.activity_id.reopen_on_change:
                rec.is_outdated = False
                continue
            rec.is_outdated = bool(
                document.latest_version_id
                and rec.document_version_id != document.latest_version_id)
