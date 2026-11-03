"""La purge liée au départ, et rien d'automatique par défaut."""

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Délai de grâce après le départ. Un départ se défait parfois dans les
# premières semaines, et une déclaration détruite ne se retrouve pas.
GRACE_DAYS = 90


class Profile(models.Model):
    _inherit = "bf.ex.profile"

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
        """🔴 Livré DESACTIVE. Une purge irréversible ne doit jamais être un
        effet de bord d'une installation.

        Écrit au registre de destruction ce qui est parti, sans nommer le type
        ni les scores : l'entrée dirait sinon exactement ce qu'elle vient de
        détruire, et le registre est immuable.
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
        calendar = self.env.ref(
            "bf_employee_experience_profile_privacy.retention_ex_profile",
            raise_if_not_found=False,
        )
        # ⚠️ `env.ref` rend une règle ARCHIVEE sans broncher. Une entrée de
        # registre qui cite une règle archivée dit que la destruction suit un
        # calendrier qui n'est plus en vigueur.
        calendar_id = calendar.id if calendar and calendar.active else False
        for employee, number in by_employee.items():
            # ⚠️ L'entrée appartient à la société de la PERSONNE, pas à celle du
            # cron : en multi-société, le responsable de l'autre société ne
            # voyait pas la preuve de destruction qui le concerne.
            Register.with_company(
                employee.company_id or self.env.company
            ).create({
                "destruction_date": now,
                "destroyed_by_id": self.env.uid,
                # Le cron tourne sans personne au clavier. L'approbation est
                # l'acte de l'AVOIR ALLUME : il est livré désactivé pour que ce
                # soit une décision, et non un effet de bord d'installation.
                "approved_by_id": self.env.uid,
                "res_model": "bf.ex.profile",
                "res_name": employee.display_name,
                "document_description": _(
                    "Types de profil : %(n)s déclaration(s) détruite(s) pour une "
                    "personne dont le départ remonte à plus de %(days)s jours. "
                    "⚠️ L'entrée ne nomme NI le référentiel, NI le type, NI les "
                    "scores : les inscrire au registre immuable reviendrait à "
                    "conserver pour toujours le renseignement qu'on vient de "
                    "détruire.",
                    n=number, days=grace_days,
                ),
                "pi_categories": _("Type de profil et style de travail"),
                "subject_count": 1,
                "destruction_method": "delete",
                "legal_basis": _(
                    "Art. 23 LPRPSP : le renseignement personnel est détruit "
                    "lorsque la fin pour laquelle il a été recueilli est "
                    "accomplie. La fin d'une déclaration de type de profil est "
                    "accomplie au départ de la personne."
                ),
                "retention_calendar_id": calendar_id,
                "company_id": (employee.company_id or self.env.company).id,
            })
        # Les lignes de score partent par la cascade Postgres de leur
        # `profile_id`. Elles ne portent ni pièce jointe ni fil de discussion,
        # donc rien ne reste orphelin au filestore.
        records.unlink()
        _logger.info(
            "bf_employee_experience_profile_privacy: %s déclaration(s) purgée(s) "
            "après départ", count,
        )
        return count
