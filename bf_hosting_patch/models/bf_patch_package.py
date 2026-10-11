# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""bf.patch.package — un paquet en attente, tel que la machine l'a vu.

Enfant du relevé et non de la machine, pour que l'historique tienne debout : on
veut pouvoir dire « ce paquet attendait déjà il y a trois semaines ».
"""

from odoo import fields, models


class BfPatchPackage(models.Model):
    _name = "bf.patch.package"
    _description = "Package pending at report time"
    _order = "is_security desc, name"

    report_id = fields.Many2one(
        comodel_name="bf.patch.report",
        string="Report",
        required=True,
        ondelete="cascade",
        index=True,
    )
    system_id = fields.Many2one(
        related="report_id.system_id", string="System", store=True, index=True
    )
    endpoint_id = fields.Many2one(
        related="report_id.endpoint_id", string="Endpoint", store=True, index=True
    )
    name = fields.Char(string="Package", required=True)
    version_installed = fields.Char(string="Installed version")
    version_candidate = fields.Char(string="Available version")
    # L'origine n'est pas décorative : c'est elle qui explique pourquoi un
    # auto-updateur configuré laisse des paquets en attente (dépôt hors des
    # `Allowed-Origins` d'unattended-upgrades, par exemple).
    origin = fields.Char(string="Origin")
    is_security = fields.Boolean(string="Security")
