"""Ce que la barre système a besoin de savoir avant d'afficher son bouton.

Le compteur, lui, se compte tout seul par un ``search_count`` : le domaine est
recopié dans le JavaScript parce qu'il faut compter AVANT qu'aucune action ne
soit ouverte. Cette recopie est épinglée par
``bf_email_management`` (``test_the_systray_javascript_carries_every_leaf``).

Ce qui suit ne concerne que la façon d'ouvrir la boîte, pas son contenu.
"""

from odoo import api, models

# Sous 40 % le volet des dossiers et la liste ne tiennent plus ensemble, et
# au-delà de 100 % le panneau sortirait de la fenêtre. Mêmes bornes que le
# panneau Nextcloud, pour que les deux se règlent pareil.
MIN_PCT = 40
MAX_PCT = 100
DEFAULT_PCT = 85
DEFAULT_MODE = "panneau"
MODES = ("panneau", "page")


def _clamp_pct(raw, fallback=DEFAULT_PCT):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return min(MAX_PCT, max(MIN_PCT, value))


class BfEmail(models.Model):
    _inherit = "bf.email"

    @api.model
    def systray_config(self):
        """Le mode d'ouverture et la taille de départ du panneau.

        Appelé une fois au montage du bouton. Ne rend que des réglages
        d'affichage : rien ici n'est propre à une personne ni à un courriel,
        donc la lecture par RPC de tout usager interne est sans conséquence.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        mode = ICP.get_param("bf_email_systray.mode", DEFAULT_MODE)
        if mode not in MODES:
            mode = DEFAULT_MODE
        return {
            "mode": mode,
            "width_pct": _clamp_pct(
                ICP.get_param("bf_email_systray.width_pct", DEFAULT_PCT)),
            "height_pct": _clamp_pct(
                ICP.get_param("bf_email_systray.height_pct", DEFAULT_PCT)),
        }
