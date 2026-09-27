# -*- coding: utf-8 -*-
"""Plafond des écrans qui suivent un tour de Gen en direct.

🔴 Relevé du 2026-09-24 sur une base réelle : six flux ``/claude-chat/stream`` et
``/claude-chat/attach`` ouverts en même temps ont pris les six workers HTTP,
chacun tenu jusqu'à la fin de son tour (jusqu'à 11 minutes). Odoo n'a plus
répondu à personne pendant six minutes, sans une ligne d'erreur : base
muette, CPU à zéro, chaque worker attendant son pont.

Le tour n'appartient pas à la réponse HTTP (voir ``turns.py``) : un écran
refusé ne perd rien, il revient par ``/claude-chat/attach`` avec son
attente croissante, et le fil du tour continue d'écrire sans lui. On peut
donc plafonner les écrans sans toucher aux tours.

Les workers sont des processus distincts : le compte se tient par des
verrous ``flock`` sur des fichiers, un par place. Le noyau libère la place
d'un worker tué ou recyclé, sans ménage à faire.
"""
import fcntl
import logging
import os
import tempfile

from odoo.tools import config

_logger = logging.getLogger(__name__)

# Part des workers HTTP qu'on laisse aux flux de Gen. Le reste sert l'écran.
_SHARE = 0.5


def viewer_cap(env):
    """Le nombre d'écrans en direct permis, ou 0 pour « sans plafond ».

    ``bf_claude_chat.max_live_viewers`` l'impose ; sans lui, la moitié des
    workers HTTP. En mode fils (``workers = 0``), un flux ne tient qu'un fil :
    pas de plafond.
    """
    raw = env["ir.config_parameter"].sudo().get_param("bf_claude_chat.max_live_viewers")
    if raw not in (None, False, ""):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            _logger.warning("bf_claude_chat.max_live_viewers illisible : %r", raw)
    workers = int(config.get("workers") or 0)
    if workers <= 0:
        return 0
    return max(1, int(workers * _SHARE))


def _slot_dir(db_name):
    path = os.path.join(tempfile.gettempdir(), "bf_claude_chat_viewers", db_name)
    os.makedirs(path, exist_ok=True)
    return path


class ViewerSlot:
    """Une place prise ; ``release()`` la rend (sans effet la deuxième fois)."""

    def __init__(self, fd=None):
        self._fd = fd

    def release(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)  # fermer le descripteur lève le verrou
            except OSError:
                pass

    def __del__(self):
        self.release()


def acquire(env, db_name):
    """Prendre une place, ou rendre None si toutes sont prises.

    Sans plafond, rend une place vide qui ne tient rien. Une erreur de
    système de fichiers ne doit jamais couper Gen : elle laisse passer.
    """
    cap = viewer_cap(env)
    if not cap:
        return ViewerSlot()
    try:
        folder = _slot_dir(db_name)
        for i in range(cap):
            fd = os.open(os.path.join(folder, f"slot-{i}"), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            return ViewerSlot(fd)
    except OSError:
        _logger.warning("Gen : plafond des écrans illisible, flux laissé passer", exc_info=True)
        return ViewerSlot()
    _logger.info("Gen : %s écrans en direct, plafond atteint", cap)
    return None


def held(slot, iterable):
    """Parcourir ``iterable`` en tenant ``slot``, rendu à la fin quoi qu'il arrive.

    werkzeug ferme l'itérable quand le navigateur part : le ``finally``
    tourne alors aussi.
    """
    try:
        yield from iterable
    finally:
        slot.release()
