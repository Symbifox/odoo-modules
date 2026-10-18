from urllib.parse import urlsplit

from odoo import _, api, models


class LinkTracker(models.Model):
    _inherit = "link.tracker"

    @api.model
    def _get_title_from_url(self, url):
        """Titre fixe pour un lien piégé de cette base, sans aller le chercher.

        🔴 À l'envoi d'une campagne, mass_mailing raccourcit chaque lien du
        courriel rendu, et `link.tracker` va chercher la page pour en lire le
        titre. Le serveur visiterait ainsi lui-même chaque `/phish/<jeton>` :
        un faux clic (et un faux signalement par `/report`) possible pour
        chaque destinataire, et un GET qui reste bloqué en essai.
        """
        if self._est_lien_piege(url):
            return _("Simulation d'hameçonnage")
        return super()._get_title_from_url(url)

    @api.model
    def _est_lien_piege(self, url):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        try:
            lien, base = urlsplit(url or ""), urlsplit(base or "")
        except ValueError:
            return False
        return bool(
            lien.hostname and lien.hostname == base.hostname
            and lien.path.startswith("/phish/"))
