from odoo import _, models


class BfPropertyOrganisation(models.Model):
    _inherit = "bf.property.organisation"

    def _bf_access_efvp_extras(self):
        """La tablette de l'entrée, à l'EFVP du contrôle d'accès (si l'organisation
        en a une)."""
        extras = super()._bf_access_efvp_extras()
        kiosks = self.env["bf.property.lobby.kiosk"].sudo().with_context(active_test=False).search(
            [("building_id.organisation_id", "in", self.ids)])
        if kiosks:
            extras.append({
                "title": _("Portier sur tablette : %s") % ", ".join(kiosks.mapped("name")),
                "items": [
                    _("Annuaire de l'entrée : seuls les résidents qui y consentent au portail y paraissent "
                      "(date du consentement consignée), sous le libellé qu'ils choisissent ; retrait en tout "
                      "temps (art. 9.1, 14)."),
                    _("Aucun numéro de téléphone n'est affiché ni transmis à la tablette ; le numéro du résident "
                      "joignable sert seulement à faire sonner son téléphone par la téléphonie de l'organisation."),
                    _("Journal des appels et livraisons : date, logement, issue ; supprimé au délai de "
                      "conservation de l'organisation. Les avis au responsable nomment le logement, pas la personne."),
                    _("Page publique sans compte : protégée par une adresse secrète par appareil (empreinte "
                      "seulement), plafonnée, non indexée."),
                ],
            })
        return extras
