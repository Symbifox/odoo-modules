from odoo import api, fields, models


class FederationDocument(models.Model):
    _inherit = "federation.document"

    federation_statement_fingerprint = fields.Char(
        string="Statement fingerprint", readonly=True, copy=False,
        help="The fingerprint of the timesheet statement's content. "
             "Producing the statement again for the same period publishes "
             "a new version only if the content changed.")

    @api.model
    def _selection_source(self):
        """Un relevé des heures naît d'un projet : il faut pouvoir le dire."""
        out = super()._selection_source()
        if "project.project" in self.env and "project.project" not in {nom for nom, _l in out}:
            out.append(("project.project", self._libelle_modele("project.project")))
        return out
