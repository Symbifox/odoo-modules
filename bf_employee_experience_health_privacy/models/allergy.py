"""La purge liée au départ, et rien d'automatique par défaut."""

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Délai de grâce après le départ. Un départ se défait parfois dans les
# premières semaines, et une déclaration détruite ne se retrouve pas.
GRACE_DAYS = 90


class Allergy(models.Model):
    _inherit = "bf.ex.allergy"

    @api.model
    def _departed_declarations(self, grace_days=GRACE_DAYS):
        """Les déclarations des personnes parties depuis plus que le délai."""
        cutoff = fields.Date.context_today(self) - relativedelta(days=grace_days)
        return self.sudo().search([
            ("employee_id.departure_date", "!=", False),
            ("employee_id.departure_date", "<=", cutoff),
        ])

    @api.model
    def _cron_privacy_purge_departed(self, grace_days=GRACE_DAYS):
        """🔴 Livré DÉSACTIVÉ. Une purge irréversible ne doit jamais être un
        effet de bord d'une installation.

        Écrit au registre de destruction ce qui est parti, sans nommer
        l'allergène : l'entrée dirait sinon exactement ce qu'elle vient de
        détruire.
        """
        records = self._departed_declarations(grace_days)
        if not records:
            return 0
        count = len(records)
        by_employee = {}
        for record in records:
            by_employee.setdefault(record.employee_id, 0)
            by_employee[record.employee_id] += 1

        Register = self.env["privacy.destruction.register"].sudo()
        now = fields.Datetime.now()
        for employee, number in by_employee.items():
            Register.create({
                "destruction_date": now,
                "destroyed_by_id": self.env.uid,
                # Le cron tourne sans personne au clavier. L'approbation est
                # l'acte de l'AVOIR ALLUMÉ : il est livré désactivé pour que
                # ce soit une décision, et non un effet de bord d'installation.
                "approved_by_id": self.env.uid,
                "res_model": "bf.ex.allergy",
                "res_name": employee.display_name,
                "document_description": _(
                    "Allergies : %(n)s déclaration(s) détruite(s) pour une "
                    "personne dont le départ remonte à plus de %(days)s jours. "
                    "⚠️ L'entrée ne nomme PAS les allergènes : les inscrire au "
                    "registre immuable reviendrait à conserver pour toujours "
                    "le renseignement de santé qu'on vient de détruire.",
                    n=number, days=grace_days,
                ),
                "pi_categories": _("Santé (allergies et contraintes alimentaires)"),
                "subject_count": 1,
                "destruction_method": "delete",
                "legal_basis": _(
                    "Art. 23 LPRPSP : le renseignement personnel est détruit "
                    "lorsque la fin pour laquelle il a été recueilli est "
                    "accomplie. La fin d'une déclaration d'allergie est "
                    "accomplie au départ de la personne."
                ),
                "retention_calendar_id": self.env.ref(
                    "bf_employee_experience_health_privacy.retention_ex_allergy",
                    raise_if_not_found=False,
                ).id or False,
            })
        records.unlink()
        _logger.info(
            "bf_employee_experience_health_privacy: %s déclaration(s) purgée(s) "
            "après départ", count,
        )
        return count
