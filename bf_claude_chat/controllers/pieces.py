"""Pièces jointes de Gen : le téléversement, et ce qui les relie à un tour.

Le cycle d'une pièce :

1. ``/claude-chat/upload`` lit le fichier, en extrait le texte (``pieces_jointes``, dans
   un sous-processus borné) et le garde avec la pièce, EN ATTENTE : ``res_model =
   claude.chat.message``, ``res_id = 0``. Odoo ne montre une pièce sans ``res_id`` qu'à
   celle ou celui qui l'a créée et aux administrateurs système (``ir.attachment.check``) ;
   un tour, lui, n'accepte que les pièces de l'appelant, administrateur compris.
2. Au départ du tour, ``pieces_du_tour`` revérifie chaque pièce (à soi, en attente) et
   rend le texte gardé : le fichier n'est lu qu'UNE fois.
3. ``rattacher`` la pose sur le message de la personne : dès lors, ce sont les règles de
   ``claude.chat.message`` (la conversation de l'usager) qui en gardent l'accès.

Aucun champ neuf : une pièce se retrouve par ``res_model``/``res_id``, le patron d'Odoo.
Le texte lu vit dans ``index_content`` (le champ d'Odoo pour le texte d'un fichier), et
sa longueur d'origine dans ``description``.
"""

import json

from odoo import _, http
from odoo.http import request

from .. import pieces_jointes as pj
from ..models.ir_attachment import CLE_PIECE_GEN

MODELE = "claude.chat.message"
#: Le type demandé à la création. ⚠️ Il ne protège de RIEN : Odoo redevine le type depuis
#: le contenu. C'est la clé de contexte `CLE_PIECE_GEN` (models/ir_attachment.py) qui
#: empêche Odoo de relire le fichier dans le worker.
TYPE = "application/octet-stream"
#: Plafond du texte des pièces d'un message, par locataire (paramètre système). Sans lui,
#: `pieces_jointes.CARACTERES_MAX_MESSAGE`. Un pont qui borne la longueur du message plus
#: bas (16 384 caractères sur un locataire public) exige un plafond plus bas ici, sinon le
#: tour part et le pont le refuse sans un mot pour la personne.
PARAM_PLAFOND = "bf_claude_chat.pieces_caracteres_max"


def _json(donnees, statut=200):
    return request.make_response(
        json.dumps(donnees), headers=[("Content-Type", "application/json")], status=statut)


def _milliers(n):
    return f"{n:,}".replace(",", " ")


def en_attente(env, uid=None):
    """Les pièces en attente d'un usager (sudo, borné à lui)."""
    return env["ir.attachment"].sudo().search([
        ("res_model", "=", MODELE), ("res_id", "=", 0),
        ("create_uid", "=", uid or env.uid),
    ])


def plafond_du_message(env):
    """Le plafond du texte des pièces d'un message sur ce locataire."""
    brut = env["ir.config_parameter"].sudo().get_param(PARAM_PLAFOND)
    # 🔴 Absent, `get_param` rend False, et `int(False)` vaut 0 sans lever : le plafond
    # tombait à zéro sur tout locataire sans réglage (attrapé par les essais).
    if brut in (False, None, ""):
        return pj.CARACTERES_MAX_MESSAGE
    try:
        valeur = int(brut)
    except (TypeError, ValueError):
        return pj.CARACTERES_MAX_MESSAGE
    return max(0, min(valeur, pj.CARACTERES_MAX_MESSAGE))


def pieces_du_tour(env, ids):
    """``(pieces, textes)`` des pièces nommées par l'écran, ou :class:`pj.PieceRefusee`.

    ``textes`` : ``[(nom, texte, total, coupe)]``. Une liste vide rend ``(vide, [])`` :
    un tour sans pièce ne change en rien.
    """
    Attachment = env["ir.attachment"].sudo()
    if not ids:
        return Attachment, []
    if not isinstance(ids, list) or len(ids) > pj.PIECES_MAX:
        raise pj.PieceRefusee(_("At most %s attachments per message.", pj.PIECES_MAX))
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise pj.PieceRefusee(_("Attachment not found."))
    pieces = Attachment.browse(ids).exists()
    # 🔴 Tout ce qui n'est pas À SOI et EN ATTENTE est refusé du même mot : une pièce d'un
    # autre, ou déjà partie avec un autre tour, ne doit ni partir ni laisser savoir
    # qu'elle existe. L'écran reçoit les identifiants qu'IL a envoyés, pour marquer
    # ses puces mortes : rien de ce qu'il ne savait déjà.
    bonnes = pieces.filtered(
        lambda p: p.res_model == MODELE and not p.res_id and p.create_uid.id == env.uid)
    mortes = [i for i in dict.fromkeys(ids) if i not in bonnes.ids]
    if mortes or len(ids) != len(set(ids)):
        raise pj.PieceRefusee(_("Attachment not found."), ids=mortes)
    textes, total = [], 0
    for piece in bonnes:
        texte = piece.index_content or ""
        try:
            meta = json.loads(piece.description or "{}")
        except ValueError:
            meta = {}
        total += len(texte)
        textes.append((piece.name, texte, meta.get("total") or len(texte),
                       bool(meta.get("coupe"))))
    plafond = plafond_du_message(env)
    if total > plafond:
        # Sans `ids` : aucune pièce n'est en cause à elle seule, et les marquer toutes en
        # erreur obligeait à tout retirer pour en renvoyer une.
        raise pj.PieceRefusee(_(
            "Together these attachments hold %(total)s characters; Gen reads at most "
            "%(max)s per message here. Send fewer, or shorter, attachments.",
            total=_milliers(total), max=_milliers(plafond)))
    return bonnes, textes


def rattacher(pieces, message):
    """La pièce suit désormais le message de la personne (et ses règles d'accès)."""
    if pieces:
        pieces.sudo().write({"res_id": message.id})


def pieces_des_messages(env, message_ids):
    """``{message_id: [{id, name}]}`` pour les messages DÉJÀ lus par l'appelant."""
    if not message_ids:
        return {}
    lignes = env["ir.attachment"].sudo().search_read(
        [("res_model", "=", MODELE), ("res_id", "in", list(message_ids))],
        ["res_id", "name", "file_size"], order="id asc")
    rendu = {}
    for ligne in lignes:
        rendu.setdefault(ligne["res_id"], []).append(
            {"id": ligne["id"], "name": ligne["name"], "size": ligne["file_size"]})
    return rendu


class GenPiecesJointes(http.Controller):

    @http.route("/claude-chat/upload", type="http", auth="user", methods=["POST"])
    def televerser(self, ufile=None, **kw):
        """Un fichier glissé ou choisi : son texte est lu TOUT DE SUITE.

        La puce dit donc avant l'envoi si Gen pourra lire le fichier, et combien il en
        lira ; un PDF numérisé est refusé ici, pas au milieu du tour.
        """
        from .main import _check_rate_limit, _get_settings
        # 🔴 Un compte portail n'a pas accès aux messages de Gen : la création de la pièce
        # le refusait, mais APRÈS la lecture du fichier dans le sous-processus (jusqu'à
        # 60 s de processeur et 2 Go). Refusé ici, avant de lire quoi que ce soit.
        if not request.env.user._is_internal():
            return _json({"error": _("Gen is reserved for internal users.")}, 403)
        if not _get_settings()["enabled"]:
            return _json({"error": _("Gen is turned off.")}, 403)
        if ufile is None or not getattr(ufile, "filename", ""):
            return _json({"error": _("No file received.")}, 400)
        env = request.env
        nom = ufile.filename[:255]
        if not _check_rate_limit(f"upload:{env.uid}"):
            return _json({"error": _("Too many attachments at once. Wait a moment."),
                          "name": nom})
        # Le ménage d'abord : une pièce choisie puis jamais envoyée (onglet fermé) n'a de
        # sens que le temps d'une conversation. Le cron passe aussi pour qui ne revient pas.
        env["claude.chat.message"].sudo()._purger_pieces(uid=env.uid)
        if len(en_attente(env)) >= 4 * pj.PIECES_MAX:
            return _json({"error": _("Too many attachments waiting. Send or remove "
                                     "the ones that are ready."), "name": nom})
        octets = ufile.read(pj.TAILLE_MAX + 1)
        try:
            texte, total, coupe = pj.extraire(nom, octets)
        except pj.PieceRefusee as refus:
            return _json({"error": str(refus), "name": nom})
        # Un locataire au plafond plus bas que celui d'un fichier : la pièce est coupée
        # là, et la puce le dit, plutôt que de passer pour lue puis d'être refusée à
        # l'envoi.
        plafond = plafond_du_message(env)
        if len(texte) > plafond:
            texte, coupe = texte[:plafond], True

        # Sous l'usager, pas en sudo : `create_uid` est ce qui garde la pièce à lui.
        piece = env["ir.attachment"].with_context(**{CLE_PIECE_GEN: True}).create({
            "name": nom, "raw": octets, "mimetype": TYPE,
            "res_model": MODELE, "res_id": 0,
        })
        piece.sudo().write({
            "index_content": texte,
            "description": json.dumps({"total": total, "coupe": coupe}),
        })
        return _json({
            "id": piece.id, "name": nom, "size": len(octets),
            "chars": len(texte), "total": total, "truncated": coupe,
        })

    @http.route("/claude-chat/upload/remove", type="json", auth="user", methods=["POST"])
    def retirer(self, attachment_id):
        """Le × d'une puce : seule une pièce À SOI et EN ATTENTE se retire."""
        try:
            attachment_id = int(attachment_id)
        except (TypeError, ValueError):
            return {"ok": False}
        piece = en_attente(request.env).filtered(lambda p: p.id == attachment_id)
        if piece:
            piece.unlink()
        return {"ok": bool(piece)}
