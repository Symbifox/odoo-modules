"""Le droit à l'effacement atteint le persona, et le détruit vraiment.

🔴 Sans ce pont, une demande d'effacement sans politique retombe sur
« anonymisation » (`privacy_consent`), et `destroy_record` n'anonymise un
persona qu'en l'archivant : il n'a pas de champ `notes`. Profil, cote de payeur
et fil (dont l'historique suivi de la qualité de payeur) restaient intacts,
pendant que le journal écrivait « DÉTRUIT (anonymisation) » et que le registre
inscrivait une anonymisation scellée.

Un persona ne s'anonymise pas : son contenu EST la personne. Il est donc
supprimé, quelle que soit la méthode demandée (sauf « manuelle », qui ne fait
rien d'automatique), et la demande le dit dans son journal et ses notes, que le
registre reprend.
"""
from odoo import _, api, models
from odoo.exceptions import UserError

from .privacy_destruction_campaign import PERSONA_MODELS

_REMOVAL_METHODS = ("delete", "secure_wipe")


class PrivacyDestructionRequest(models.Model):
    _inherit = "privacy.destruction.request"

    @api.model
    def create_partner_destruction_request(self, partner_id, reason=None):
        """Classe le persona de la personne avant que la demande ne ramasse ses
        documents classifiés : sans classification, l'effacement l'ignorait."""
        partner = self.env["res.partner"].browse(partner_id)
        if partner.exists():
            self._bf_persona_classify_for_erasure(partner)
        return super().create_partner_destruction_request(partner_id, reason=reason)

    @api.model
    def _bf_persona_classify_for_erasure(self, partner):
        Persona = self.env["contact.persona"].sudo().with_context(active_test=False)
        Classification = self.env["privacy.document.classification"].sudo().with_context(
            active_test=False)
        rule = self.env["contact.persona"]._privacy_rules()
        found = Classification.browse()
        for persona in Persona.search([("partner_id", "=", partner.id)]):
            existing = Classification.search([
                ("res_model", "=", "contact.persona"), ("res_id", "=", persona.id),
                ("pi_category", "=", "financial"),
                ("company_id", "in", [self.env.company.id, False]),
            ], limit=1)
            vals = {"subject_partner_id": partner.id, "active": True}
            if existing:
                existing.write(vals)
                found |= existing
                continue
            found |= Classification.create(dict(vals, **{
                "res_model": "contact.persona", "res_id": persona.id,
                "pi_category": "financial", "sensitivity_level": "confidential",
                "contains_direct_identifiers": True,
                "retention_calendar_id": rule.id or False,
                "company_id": self.env.company.id,
                "notes": _("Classé par bf_persona_privacy pour une demande d'effacement : "
                           "profil relationnel et cote de payeur."),
            }))
        return found

    def _execute_destruction(self, method):
        """Une demande d'effacement saisie à la main n'est pas passée par
        `create_partner_destruction_request` : on classe le persona ici aussi."""
        self.ensure_one()
        if self.request_type == "erasure_right" and self.partner_id and method != "manual":
            found = self._bf_persona_classify_for_erasure(self.partner_id)
            if found - self.classification_ids:
                self.write({"classification_ids": [(4, cid) for cid in found.ids]})
        return super()._execute_destruction(method)

    def _execute_document_destruction(self, method):
        self.ensure_one()
        removed = []
        # Toutes les méthodes sauf « manuelle » : même pour « suppression », le
        # socle contrôlerait le droit d'écriture du responsable sur le persona,
        # qu'il n'a pas sans rôle persona, et sauterait la ligne.
        if method != "manual":
            for cls in self.classification_ids.filtered(lambda c: c.res_model in PERSONA_MODELS):
                Model = self.env[cls.res_model].sudo().with_context(active_test=False)
                record = Model.browse(cls.res_id).exists()
                if not record:
                    continue
                label = self.env["privacy.destruction.campaign.line"]._bf_persona_anonymous_name(record)
                record.unlink()
                if Model.browse(cls.res_id).exists():
                    raise UserError(_(
                        "%(label)s existe toujours après la suppression : rien n'est "
                        "inscrit au registre.", label=label))
                removed.append(label)
        res = super()._execute_document_destruction(method)
        if removed and method in _REMOVAL_METHODS:
            self.write({"execution_log": "\n".join(filter(None, [self.execution_log, _(
                "bf_persona_privacy : %s supprimé(s) par le pont.", ", ".join(removed))]))})
        if removed and method not in _REMOVAL_METHODS:
            note = _(
                "bf_persona_privacy : %(items)s SUPPRIMÉ(S), et non traité(s) par "
                "« %(method)s » : un persona ne s'anonymise pas (son profil et "
                "l'historique de sa cote de payeur resteraient dans son fil).",
                items=", ".join(removed), method=method)
            self.write({
                "execution_log": "\n".join(filter(None, [self.execution_log, note])),
                "notes": "\n".join(filter(None, [self.notes, note])),
            })
        return res
