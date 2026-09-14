"""Le chronomètre doit figer l'écoulé à l'arrêt.

Odoo n'exprime aucune version dans ``depends``. Sur un ``bf_timesheet_timer``
antérieur à 18.0.1.12.0, un chrono arrêté au navigateur voit sa durée proposée
grossir à chaque lecture, et le téléphone la proposerait telle quelle. Plutôt
qu'une application qui enregistre des heures fausses, une installation qui
refuse et dit pourquoi.

🔴 Le contrôle porte sur la BASE : la colonne ``first_start`` existe dans
``bf_timer`` et ``bf_timesheet_timer`` y est installé en 18.0.1.12.0 ou plus.
Des fichiers 18.0.1.12.0 copiés sans ``-u`` du chronomètre ont bien les
méthodes attendues, et une base qui n'a ni la colonne ni le chrono figé : un
contrôle sur le seul code laissait passer cette installation.
"""
from odoo.exceptions import UserError
from odoo.tools import parse_version
from odoo.tools.sql import column_exists

VERSION_MINIMALE = "18.0.1.12.0"


def _exiger_chrono_fige(env):
    cr = env.cr
    cr.execute("""
        SELECT latest_version FROM ir_module_module
         WHERE name = 'bf_timesheet_timer' AND state = 'installed'
    """)
    ligne = cr.fetchone()
    version = ligne[0] if ligne else None
    a_jour = bool(version) and parse_version(version) >= parse_version(VERSION_MINIMALE)
    if not (a_jour and column_exists(cr, "bf_timer", "first_start")):
        raise UserError(
            "Chronomètre : application Android exige « BF Timer - Feuilles de "
            "temps » %s ou plus récent, mis à jour dans cette base (version en "
            "base : %s). Mettez d'abord ce module à jour." % (
                VERSION_MINIMALE, version or "absente"))
