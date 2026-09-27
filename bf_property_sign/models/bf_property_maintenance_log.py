"""La déclaration d'examen sur place, signée par qui la fait.

🔴 **C'est la seule pièce de la suite dont le texte exige expressément une
signature.** r. 8.01, art. 6 : « La personne qui établit ou révise le carnet
d'entretien SIGNE une déclaration attestant que les parties communes et les
biens visés à l'article 2 ont été examinés sur place par elle-même ou sous sa
supervision et qu'elle a pris connaissance des renseignements contenus au
carnet. Cette déclaration est datée et incluse au carnet d'entretien. »

Le module la représentait par une case à cocher, et une case dit tout autre
chose : elle dit qu'un gestionnaire affirme que le professionnel a déclaré.
C'est précisément la distance que l'art. 6 ferme en nommant la personne qui
signe. La signature électronique la referme ici aussi : l'auteur du carnet n'a
pas de compte dans l'instance, il reçoit un lien, il signe, et c'est sa
signature qui coche la case et pose la date.

⚠️ **La case reste modifiable à la main.** Une déclaration papier signée puis
versée au carnet vaut tout autant, et un syndicat qui en tient une n'a pas à
passer par ici pour être en règle. Le pont ouvre un chemin, il n'en ferme aucun.

⚠️ **La date posée est celle de la signature, pas celle du jour.** Une
déclaration datée du jour où le logiciel apprend son existence dirait que
l'examen a eu lieu ce jour-là.
"""
from odoo import _, fields, models


class BfPropertyMaintenanceLog(models.Model):
    _name = "bf.property.maintenance.log"
    _inherit = ["bf.property.maintenance.log", "bf.sign.mixin"]

    def _sign_report_ref(self):
        return "bf_property_records.action_report_site_declaration"

    def _sign_default_signers(self):
        """L'auteur du carnet, et lui seul.

        C'est le seul des trois documents du pont où la personne qui signe est
        une donnée du dossier : l'art. 1 du règlement borne qui peut établir un
        carnet, et le module tient déjà l'auteur avec son ordre professionnel.
        """
        self.ensure_one()
        author = self.author_partner_id
        if author and author.email:
            return [
                {
                    "name": author.name,
                    "email": author.email,
                    "partner_id": author.id,
                }
            ]
        return []

    def _sign_document_filename(self):
        self.ensure_one()
        return "Declaration_examen_%s.pdf" % (
            self._report_declaration_date() or ""
        )

    def _sign_on_signed(self, request):
        """La signature vaut la déclaration de l'art. 6, et la date vient d'elle.

        ⚠️ Si la déclaration porte déjà une date, elle n'est pas écrasée : un
        syndicat qui avait une déclaration papier et qui fait signer par-dessus
        ne doit pas voir sa date d'origine changer.
        """
        self.ensure_one()
        signer = request.signer_ids.sorted(lambda s: (s.sequence, s.id))[:1]
        signed_on = signer.signed_on if signer else False
        declared = fields.Date.to_date(signed_on) if signed_on else False
        vals = {"site_declaration": True}
        if declared and not self.site_declaration_date:
            vals["site_declaration_date"] = declared
        self.write(vals)
        self.message_post(
            body=_(
                "Déclaration d'examen sur place signée par %(who)s "
                "(r. 8.01, art. 6). La déclaration est datée du %(date)s et "
                "fait partie du carnet."
            )
            % {
                "who": (signer.name if signer else _("l'auteur du carnet")),
                "date": self.site_declaration_date or _("date non portée"),
            }
        )
        return super()._sign_on_signed(request)
