"""La déclaration d'examen sur place, r. 8.01, art. 6.

  « La personne qui établit ou révise le carnet d'entretien signe une
  déclaration attestant que les parties communes et les biens visés à
  l'article 2 ont été examinés sur place par elle-même ou sous sa supervision
  et qu'elle a pris connaissance des renseignements contenus au carnet. Cette
  déclaration est datée et incluse au carnet d'entretien. »

🔴 **C'est la seule pièce de la suite dont le texte exige expressément une
SIGNATURE**, et le module la représentait par une case à cocher. Une case dit
« quelqu'un affirme que le professionnel a déclaré » ; le règlement veut que le
professionnel déclare lui-même. Le document existe donc maintenant, et le pont
bf_property_sign le fait signer par l'auteur du carnet, qui n'a pas de compte
dans l'instance et n'en a pas besoin.

⚠️ **La case reste modifiable à la main, et c'est voulu.** Une déclaration
papier signée puis versée au carnet est parfaitement valable, et un syndicat qui
en a une n'a pas à passer par la signature électronique pour que son carnet soit
en règle. Le module offre un chemin de plus, il n'en ferme aucun.
"""
from odoo import _, fields, models


class BfPropertyMaintenanceLog(models.Model):
    _inherit = "bf.property.maintenance.log"

    def _report_declaration_rows(self):
        self.ensure_one()
        orders = dict(
            self._fields["author_order"]._description_selection(self.env)
        )
        rows = [
            (_("Syndicat"), self.organisation_id.display_name or ""),
            (_("Immeuble"), self.building_id.display_name or _("Non rattaché")),
            (_("Carnet"), self.name or ""),
            (_("Auteur"), self.author_partner_id.display_name or ""),
            (_("Ordre professionnel"), orders.get(self.author_order, "")),
        ]
        if self.established_date:
            rows.append(
                (_("Carnet établi le"), fields.Date.to_string(self.established_date))
            )
        if self.last_revision_date:
            rows.append(
                (
                    _("Dernière révision"),
                    fields.Date.to_string(self.last_revision_date),
                )
            )
        rows.append(
            (
                _("Biens décrits au carnet"),
                str(len(self.item_ids)),
            )
        )
        return rows

    def _report_declaration_date(self):
        """La date de la déclaration, ou celle du jour tant qu'elle n'existe pas.

        ⚠️ L'art. 6 veut la déclaration DATÉE. Tant que le syndicat n'a pas
        porté cette date, le document s'annonce comme un projet plutôt que de
        se dater lui-même : une déclaration datée du jour de son impression
        dirait que l'examen a eu lieu ce jour-là.
        """
        self.ensure_one()
        return self.site_declaration_date or fields.Date.context_today(self)
