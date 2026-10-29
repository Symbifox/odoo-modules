"""Les signataires d'une résolution, au nom légal.

`_get_signatories` rend des dictionnaires où le nom est déjà écrit, sans la
fiche. On relit donc les mêmes personnes, dans le même ordre et par les mêmes
branches, et on remplace le nom. Si un jour la liste ne correspond plus
(branche ajoutée en amont), rien n'est remplacé et c'est journalisé : un acte
au nom d'usage vaut mieux qu'un nom posé à côté de la mauvaise personne.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class CorporateResolution(models.Model):
    _inherit = "corporate.resolution"

    def _bf_signatory_partners(self):
        """Les fiches des signataires, dans l'ordre de `_get_signatories`
        (une fiche vide pour une ligne à remplir)."""
        self.ensure_one()
        Partner = self.env["res.partner"]
        if self.signatory_ids:
            return [ligne.partner_id for ligne in self.signatory_ids]
        if self.resolution_type in ("board", "written_board"):
            return [a.partner_id for a in self._get_directors_at_date()]
        if self.resolution_type == "members":
            return [Partner, Partner]
        return list(self.mover_id | self.seconder_id)

    def _get_signatories(self):
        signatories = super()._get_signatories()
        partners = self._bf_signatory_partners()
        if len(partners) != len(signatories):
            _logger.warning(
                "Resolution %s: signatories and records do not line up; "
                "legal names not applied.", self.id)
            return signatories
        for signatory, partner in zip(signatories, partners):
            if partner:
                signatory["name"] = partner._bf_legal_name()
        return signatories
