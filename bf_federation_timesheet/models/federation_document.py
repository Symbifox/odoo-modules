from odoo import api, fields, models


class FederationDocument(models.Model):
    _inherit = "federation.document"

    federation_statement_fingerprint = fields.Char(
        string="Empreinte du relevé", readonly=True, copy=False,
        help="L'empreinte du contenu du relevé des heures. Refaire le relevé d'une même période "
             "ne publie une nouvelle version que si le contenu a changé.")

    @api.model
    def _selection_source(self):
        """Un relevé des heures naît d'un projet : il faut pouvoir le dire."""
        out = super()._selection_source()
        if "project.project" in self.env and "project.project" not in {nom for nom, _l in out}:
            out.append(("project.project", self.env["project.project"]._description or "project.project"))
        return out
