"""Rebâtir la traduction en_CA des gabarits `noupdate` après la réunion des deux lignées.

Idempotent, et nécessaire sur toute installation antérieure, pour deux raisons distinctes :

* Certains locataires tournaient avec le hook d'avant 1.6.1 (`en_body = src`), où
  `Markup.replace()` échappait ses arguments : tout terme contenant du balisage
  (« <strong>…</strong> ») ne matchait jamais et restait en français, sans
  erreur. Leur créneau en_CA est donc à moitié traduit — il faut le refaire.
* Leur `.po` ne portait que 292 termes (contre 504 ici) : même les termes en
  texte brut étaient incomplets.
"""
from odoo.addons.bf_securetransfer.hooks import apply_email_translations


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    apply_email_translations(env)
