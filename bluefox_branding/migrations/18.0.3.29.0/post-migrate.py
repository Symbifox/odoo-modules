"""L'« Avis de retard sur facture » reprend son corps, cette fois par langue.

Le gabarit 141 n'a pas d'identifiant XML : le chargeur de données d'Odoo ne le
touche jamais, seul le crochet l'écrit. Jusqu'ici il écrivait le corps FRANÇAIS
dans toutes les langues actives, et le gabarit n'avait pas de `lang` : un client
anglais recevait son avis de retard en français, quelle que soit sa fiche.

On ne rejoue PAS le crochet entier : il réécrirait les seize autres surcharges
et ferait disparaître sans un mot une retouche faite à la main chez un locataire
(même raison que les 18.0.3.25.1, 3.26.0 et 3.28.0). Seul l'avis de retard est
repris, par la fonction que le crochet appelle lui-même.

🔴 Ce que cette migration NE fait pas : remettre la coquille. La production a
dé-coquillé ce gabarit le 2026-10-28 et l'a rattaché à
`bf_onboarding_base.bf_mail_layout` ; le littéral du module, lui, portait encore
son `<body>`, son en-tête et son pied. Le squelette de la 18.0.3.29.0 est le
corps réel, au caractère près, et `_apply_late_invoice` garde intact tout corps
qui porte notre marque sans être l'un des deux que nous avons publiés.

Le français est donc inchangé à l'octet : une base entièrement française ne voit
de cette migration que le `lang` posé sur le gabarit.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    from odoo.addons.bluefox_branding.hooks import _apply_late_invoice, _get_active_langs

    langs = _get_active_langs(env)
    ecrits = _apply_late_invoice(env, langs)
    _logger.info("bluefox_branding 18.0.3.29.0 : %d avis de retard repris en %d langue(s) : %s",
                 len(ecrits), len(langs), ", ".join(langs))
