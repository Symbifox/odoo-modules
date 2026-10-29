"""Le nom de l'acte, figé à l'envoi à côté du nom du signataire.

`bf.sign.signer.name` est figé à l'envoi et sert à tout : l'acte et la
courtoisie. Le nom légal y aurait salué la personne par son nom légal dans
chaque courriel, et `_ensure_signer_partners` le recopie parfois sur la fiche,
où il serait devenu son nom d'usage. D'où un second champ, lu par l'acte seul.
"""

import logging

from odoo import fields, models

from odoo.addons.bf_preferred_name.models.res_partner import LEGAL_NAME_GROUP

_logger = logging.getLogger(__name__)


class BfSignSigner(models.Model):
    _inherit = "bf.sign.signer"

    bf_act_name = fields.Char(
        string="Name on the act",
        groups=LEGAL_NAME_GROUP,
        copy=False,
        readonly=True,
        help="The signer's legal name, frozen when the request is sent. The "
             "certificate, the stamped name and the verification page print "
             "it; emails and the portal keep the name above.")

    def _bf_freeze_act_name(self, ambiguous=None):
        """Fige le nom légal des signataires, en brouillon seulement.

        En sudo : le demandeur n'est pas forcément du groupe « Noms légaux »,
        et le champ lui est fermé. Rien n'est écrit quand la fiche n'a pas de
        nom légal : l'acte prend alors le nom du signataire.
        """
        ambiguous = ambiguous or self.browse()
        for signer in self.sudo():
            partner = signer.partner_id
            legal = partner.bf_legal_name if partner else False
            # Une société n'a pas de nom légal de PERSONNE à mettre sur l'acte,
            # et un signataire relié par un courriel partagé n'est pas sûr
            # d'être cette personne.
            if partner.is_company or signer in ambiguous:
                legal = False
            # La fiche doit être celle du signataire : la recherche par courriel
            # du rattachement prend la première fiche venue (courriel partagé),
            # et un nom ou un courriel changé en brouillon peut viser quelqu'un
            # d'autre. Sans le même courriel, le
            # nom légal de la fiche ne va pas sur l'acte.
            same = (partner.email or "").strip().lower() == (signer.email or "").strip().lower()
            if legal and not same:
                _logger.info("Sign signer %s: contact %s has another email; "
                             "its legal name is not used on the act.", signer.id, partner.id)
                legal = False
            if (legal or False) != (signer.bf_act_name or False):
                signer.write({"bf_act_name": legal or False})

    def _bf_act_name(self):
        """Le nom à imprimer sur l'acte : le nom légal figé, sinon le nom."""
        self.ensure_one()
        return self.sudo().bf_act_name or self.name or ""
