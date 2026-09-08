# -*- coding: utf-8 -*-
"""2.0.0 : les boutons des courriels passent du bleu de marque au bleu lisible.

🔴 Les quatre gabarits de courriel du module sont `noupdate` : une montée de
version ne les touche pas, et c'est voulu, une entreprise a pu réécrire le
texte. Mais leur bouton était peint en #29ABE1 sous du texte blanc, soit
2,62:1 là où il en faut 4,5. On remplace la couleur, et seulement elle, dans
les gabarits qui la portent encore : un gabarit déjà recoloré à la main n'est
pas retouché, puisqu'il ne contient plus la chaîne.
"""

from odoo import SUPERUSER_ID, api

ANCIEN = "#29ABE1"
NOUVEAU = "#177AA3"

GABARITS = (
    "bf_celebrations.mail_template_invitation",
    "bf_celebrations.mail_template_rappel_organisateur",
    "bf_celebrations.mail_template_tableau_mince",
    "bf_celebrations.mail_template_livraison",
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in GABARITS:
        gabarit = env.ref(xmlid, raise_if_not_found=False)
        if not gabarit:
            continue
        # `body_html` est traduisible : on passe par le champ, dans chaque
        # langue chargée, plutôt que par un UPDATE sur le jsonb.
        for lang in env["res.lang"].get_installed():
            g = gabarit.with_context(lang=lang[0])
            corps = g.body_html or ""
            if ANCIEN in corps:
                g.body_html = corps.replace(ANCIEN, NOUVEAU)
