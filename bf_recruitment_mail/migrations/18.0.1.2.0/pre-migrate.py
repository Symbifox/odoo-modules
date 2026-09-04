# Part of bf_recruitment_mail. Voir LICENSE.
"""Lever le drapeau `noupdate` AVANT que le fichier de données ne se charge.

C'est l'ordre qui compte : un `post-migrate` arriverait après le chargement, et
la réécriture aurait déjà été ignorée. Voir hooks.py.
"""

from odoo.addons.bf_recruitment_mail.hooks import ouvrir_a_la_mise_a_jour


def migrate(cr, version):
    ouvrir_a_la_mise_a_jour(cr)
