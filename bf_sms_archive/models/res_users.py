"""Ce qu'archiver une personne doit déclencher sur ses téléphones.

Archiver le compte est le seul geste que tout le monde fait
au départ d'un employé. Il suffit déjà à faire
refuser le jeton : la garde est dans ``_resolve``. Mais le refus n'arrive qu'au
PROCHAIN appel du téléphone, et rien ne dit quand il aura lieu.

Ce fichier ajoute la seule chose qui manquait : un coup de sonnette. Le
téléphone rappelle le serveur, reçoit son 401, et efface ce qu'il garde.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    def write(self, vals):
        """Réveille les téléphones d'une personne qu'on vient d'archiver.

        ⚠️ APRÈS le ``super()``, jamais avant : réveillé pendant que le compte
        est encore actif, le téléphone rappellerait, recevrait un 200 et
        repartirait tranquille. C'est l'ordre qui fait tout le travail.

        ⚠️ Et seulement sur la BASCULE vers l'archivage. ``write`` passe ici à
        chaque connexion (``login_date``) ; tester la présence de la clé sans
        tester sa valeur aurait poussé un réveil à chaque réactivation de
        compte, c'est-à-dire l'inverse de ce qu'on veut.
        """
        bascule = self.env["res.users"]
        if "active" in vals and not vals["active"]:
            bascule = self.filtered("active")
        res = super().write(vals)
        if bascule:
            # Le push ne lève jamais (``_send`` est défensif), mais un appel de
            # plus dans ``res.users.write`` mérite sa propre ceinture : rater un
            # réveil ne doit pas faire rater l'archivage, qui est LE geste utile.
            try:
                self.env["sms.archive.unifiedpush"].sudo()\
                    ._reveiller_les_appareils_de(bascule)
            except Exception:  # noqa: BLE001
                _logger.warning(
                    "Coupe-circuit : réveil raté à l'archivage de %s.",
                    bascule.ids, exc_info=True)
        return res
