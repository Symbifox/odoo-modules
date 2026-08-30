"""Bascule vers le paramètre de socket unique porté par ``bf_ai_bridge``.

Le réglage visible dans les Paramètres pointait sur
``bf_claude_chat.bridge_socket``. Quatre modules le lisaient, un cinquième
lisait ``bf_meeting.bridge_socket`` — un paramètre qui n'a jamais existé en
base et ne marchait que parce que son défaut codé en dur valait la même chose.
Changer le réglage visible aurait laissé ``bf_meeting`` sur l'ancien chemin
sans un mot.

Les deux anciennes clés sont reprises puis retirées : les laisser en place
donnerait à lire un réglage qui ne pilote plus rien.
"""
import logging

_logger = logging.getLogger(__name__)

NOUVEAU = "bf_ai_bridge.socket"
ANCIENS = ("bf_claude_chat.bridge_socket", "bf_meeting.bridge_socket")


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (NOUVEAU,))
    ligne = cr.fetchone()
    if not (ligne and ligne[0]):
        for ancien in ANCIENS:
            cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (ancien,))
            ligne = cr.fetchone()
            if ligne and ligne[0]:
                cr.execute(
                    "INSERT INTO ir_config_parameter (key, value) VALUES (%s, %s)"
                    " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    (NOUVEAU, ligne[0]),
                )
                _logger.info("bf_claude_chat : %s repris depuis %s (%s)",
                             NOUVEAU, ancien, ligne[0])
                break

    cr.execute("DELETE FROM ir_config_parameter WHERE key IN %s", (ANCIENS,))
    if cr.rowcount:
        _logger.info("bf_claude_chat : %d ancien(s) paramètre(s) de socket retiré(s)",
                     cr.rowcount)
