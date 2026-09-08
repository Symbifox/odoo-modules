# -*- coding: utf-8 -*-
"""Les pages publiques : signer une carte sans avoir de compte.

Le module n'importe pas les utilitaires de `bf_appointment`, comme le fait
`bf_appointment_poll`, parce qu'il faudrait alors dépendre de tout le produit
de prise de rendez-vous pour poser une carte de fête. Les quelques fonctions
copiées ici sont courtes et closes ; le commentaire qui suit dit ce qu'elles
protègent, pour qu'un durcissement ultérieur sache où revenir.

Ce qui NE sort jamais du serveur : aucune requête vers un fournisseur de GIF,
aucune police distante, aucune image tierce. Une carte de fête signée par tout
le bureau dit qui travaille ici et avec qui ; ça ne s'exporte pas pour une
animation.
"""

import hmac
import io
import logging
import threading
import time
from collections import defaultdict

from odoo import _, fields, http
from odoo.http import Controller, content_disposition, request, route
from odoo.tools import html2plaintext
from odoo.tools.mimetypes import guess_mimetype

_logger = logging.getLogger(__name__)

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

from markupsafe import Markup

DOCTYPE = Markup("<!DOCTYPE html>")

_bucket_lock = threading.Lock()
_bucket_data = defaultdict(list)
_MAX_TRACKED = 5000

# Un jeton qui échoue, c'est du tâtonnement : plafonné par IP, et seuls les
# ÉCHECS comptent. Consommer à chaque affichage enfermerait dehors la personne
# qui recharge sa propre page, ce qui n'est pas une faute.
_TOKEN_MAX, _TOKEN_WINDOW = 10, 300
# Signer crée un enregistrement. Plafond par IP, plus large que le jeton parce
# qu'un bureau entier signe depuis une même sortie réseau le même midi.
_SIGN_MAX, _SIGN_WINDOW = 40, 600

_TAILLE_IMAGE_MAX = 8 * 1024 * 1024
_TYPES_IMAGE = ("image/png", "image/jpeg", "image/gif", "image/webp")


def _ip():
    """L'adresse du pair, jamais un en-tête.

    `proxy_mode` est actif sur ce déploiement, donc werkzeug a déjà réécrit
    `remote_addr` depuis un nombre connu de sauts. Lire nous-mêmes
    X-Forwarded-For laisserait un client changer de seau à chaque requête.
    """
    try:
        return request.httprequest.remote_addr or "inconnu"
    except Exception:  # noqa: BLE001
        return "inconnu"


def _plafond(seau, maxi, fenetre, cle=None, consomme=True):
    ident = (seau, cle or _ip())
    maintenant = time.monotonic()
    with _bucket_lock:
        if len(_bucket_data) > _MAX_TRACKED:
            _bucket_data.clear()
        limite = maintenant - fenetre
        coups = [t for t in _bucket_data[ident] if t > limite]
        if len(coups) >= maxi:
            _bucket_data[ident] = coups
            return False
        if consomme:
            coups.append(maintenant)
        if coups:
            _bucket_data[ident] = coups
        else:
            _bucket_data.pop(ident, None)
        return True


def _compter_echec(seau, fenetre, cle=None):
    ident = (seau, cle or _ip())
    with _bucket_lock:
        _bucket_data[ident].append(time.monotonic())


def _entetes(reponse):
    try:
        entetes = reponse.headers
    except AttributeError:
        return reponse
    entetes["Content-Security-Policy"] = _CSP
    entetes["X-Frame-Options"] = "DENY"
    entetes["X-Content-Type-Options"] = "nosniff"
    entetes["Referrer-Policy"] = "strict-origin-when-cross-origin"
    entetes["X-Robots-Tag"] = "noindex, nofollow"
    return reponse


class CelebrationController(Controller):

    # ------------------------------------------------------------------
    # Résolution du jeton
    # ------------------------------------------------------------------

    def _tableau(self, token):
        """Le tableau derrière un jeton, ou False. Comparaison à temps fixe.

        La recherche par index rendrait déjà le bon enregistrement, mais elle
        se fait sur une chaîne fournie par le client : `compare_digest`
        referme la mesure du temps de réponse, qui autrement laisse deviner
        un préfixe.
        """
        if not token or len(token) > 128:
            return False
        if not _plafond("cel_token", _TOKEN_MAX, _TOKEN_WINDOW, consomme=False):
            return False
        board = request.env["bf.celebration.board"].sudo().search(
            [("access_token", "=", token)], limit=1)
        if not board or not hmac.compare_digest(
                board.access_token or "", token):
            _compter_echec("cel_token", _TOKEN_WINDOW)
            return False
        if board.state == "cancelled":
            return False
        return board

    def _rendre(self, gabarit, valeurs, board=None):
        valeurs.setdefault("palette", board.palette() if board else {})
        valeurs.setdefault("board", board)
        # ⚠️ `doctype` n'est fourni par personne : sans lui, `t-out="doctype"`
        # rend vide et la page s'affiche en mode « quirks », où la grille et
        # les rapports d'aspect ne se comportent plus comme écrit.
        valeurs.setdefault("doctype", DOCTYPE)
        return _entetes(request.render(gabarit, valeurs))

    def _indisponible(self, motif=None):
        return _entetes(request.render(
            "bf_celebrations.page_indisponible",
            {"motif": motif or _("Ce lien n'est plus valide."),
             "doctype": DOCTYPE}))

    # ------------------------------------------------------------------
    # La page où l'on signe
    # ------------------------------------------------------------------

    @route("/celebration/<string:token>", type="http", auth="public",
           methods=["GET"], csrf=False, sitemap=False)
    def page_signature(self, token, **kw):
        board = self._tableau(token)
        if not board:
            return self._indisponible()
        return self._rendre(
            "bf_celebrations.page_signature",
            {
                "messages": board.post_ids.filtered(
                    lambda p: p.state == "published"),
                "merci": "merci" in request.params,
                "en_attente": "attente" in request.params,
                "ferme": board.state in ("delivered", "cancelled"),
                "token": token,
            },
            board,
        )

    @route("/celebration/<string:token>/signer", type="http", auth="public",
           methods=["POST"], csrf=False, sitemap=False)
    def signer(self, token, **post):
        board = self._tableau(token)
        if not board:
            return self._indisponible()
        if board.state == "cancelled":
            return self._indisponible()
        if not _plafond("cel_sign", _SIGN_MAX, _SIGN_WINDOW):
            return self._indisponible(_(
                "Beaucoup de messages sont partis d'ici en peu de temps. "
                "Réessayez dans quelques minutes."))

        nom = (post.get("author_name") or "").strip()[:80]
        corps = (post.get("body") or "").strip()
        if not nom:
            return request.redirect("/celebration/%s?erreur=nom" % token)

        image_b64 = False
        fichier = request.httprequest.files.get("image")
        if fichier and fichier.filename and board.allow_images:
            image_b64 = self._lire_image(fichier)
            if image_b64 is None:
                return request.redirect(
                    "/celebration/%s?erreur=image" % token)

        # L'encre : des traits tracés au doigt ou à la souris, relus par le
        # modèle qui n'en garde que des nombres bornés. Une charge tordue
        # est refusée poliment, jamais stockée.
        Post = request.env["bf.celebration.post"].sudo()
        try:
            encre = Post._normaliser_encre(post.get("ink") or "")
        except ValueError:
            return request.redirect("/celebration/%s?erreur=encre" % token)

        if (not html2plaintext(corps).strip() and not image_b64
                and not encre):
            return request.redirect("/celebration/%s?erreur=vide" % token)

        etat = "pending" if board.moderation else "published"
        valeurs = {
            "board_id": board.id,
            "author_name": nom,
            # ⚠️ Le corps arrive du public : on ne garde que du texte, remis
            # en paragraphes. Laisser passer du HTML fourni par un anonyme
            # sur une page que tout le bureau ouvrira serait une porte, même
            # avec l'assainissement d'Odoo derrière.
            "body": self._en_paragraphes(corps),
            "style": "hand" if post.get("style") == "hand" else "typed",
            "state": etat,
            "create_ip": _ip(),
        }
        if image_b64:
            valeurs["image"] = image_b64
        if encre:
            valeurs["ink_strokes"] = encre
        if not request.env.user._is_public():
            valeurs["author_user_id"] = request.env.user.id
            valeurs["author_partner_id"] = request.env.user.partner_id.id
        Post.create(valeurs)
        suite = "attente" if etat == "pending" else "merci"
        return request.redirect("/celebration/%s?%s=1" % (token, suite))

    def _en_paragraphes(self, texte):
        from markupsafe import escape, Markup
        lignes = [escape(l.strip()) for l in (texte or "").splitlines()]
        lignes = [l for l in lignes if l]
        if not lignes:
            return ""
        return Markup("").join(
            Markup("<p>%s</p>") % l for l in lignes)

    def _lire_image(self, fichier):
        """Rend le base64, ou None si le fichier n'est pas une image tenable.

        Le type déclaré par le navigateur ne prouve rien : c'est Pillow qui
        tranche, en ouvrant vraiment le fichier.
        """
        import base64
        from PIL import Image
        contenu = fichier.read(_TAILLE_IMAGE_MAX + 1)
        if len(contenu) > _TAILLE_IMAGE_MAX:
            return None
        if (fichier.mimetype or "").lower() not in _TYPES_IMAGE:
            return None
        try:
            Image.open(io.BytesIO(contenu)).verify()
        except Exception:  # noqa: BLE001
            return None
        return base64.b64encode(contenu)

    # ------------------------------------------------------------------
    # Le tableau, et le diaporama
    # ------------------------------------------------------------------

    def _cle_du_merci_valide(self, board, cle):
        """La clé qui n'est que dans le courriel de la personne fêtée.

        Comparée en temps constant, comme le jeton. Un essai raté compte
        dans le même seau que les jetons ratés : c'est du tâtonnement.
        """
        if not cle or len(cle) > 64 or board.state != "delivered":
            return False
        attendue = board.sudo().thanks_token or ""
        if not attendue or not hmac.compare_digest(attendue, cle):
            _compter_echec("cel_token", _TOKEN_WINDOW)
            return False
        return True

    @route("/celebration/<string:token>/tableau", type="http", auth="public",
           methods=["GET"], csrf=False, sitemap=False)
    def page_tableau(self, token, **kw):
        board = self._tableau(token)
        if not board:
            return self._indisponible()
        cle = (kw.get("cle") or "").strip()
        peut_remercier = self._cle_du_merci_valide(board, cle)
        return self._rendre(
            "bf_celebrations.page_tableau",
            {
                "messages": board.post_ids.filtered(
                    lambda p: p.state == "published"),
                "token": token,
                "livre": board.state == "delivered",
                "rejouer": "ouvrir" in request.params,
                # Le formulaire de merci : seulement avec la clé, seulement
                # tant qu'aucun merci n'a été dit.
                "peut_remercier": peut_remercier and not board.thanks_html,
                "cle": cle if peut_remercier else "",
                "merci_envoye": "merci" in request.params,
            },
            board,
        )

    @route("/celebration/<string:token>/merci", type="http", auth="public",
           methods=["POST"], csrf=False, sitemap=False)
    def remercier(self, token, **post):
        board = self._tableau(token)
        if not board:
            return self._indisponible()
        cle = (post.get("cle") or "").strip()
        if not self._cle_du_merci_valide(board, cle):
            return self._indisponible(_(
                "Ce lien ne permet pas de remercier."))
        if not _plafond("cel_sign", _SIGN_MAX, _SIGN_WINDOW):
            return self._indisponible(_(
                "Réessayez dans quelques minutes."))
        corps = (post.get("body") or "").strip()
        if not html2plaintext(corps).strip():
            return request.redirect(
                "/celebration/%s/tableau?cle=%s&erreur=vide" % (token, cle))
        if len(corps) > 1200:
            corps = corps[:1200]
        # Même traitement que les mots : du texte remis en paragraphes,
        # jamais du HTML fourni depuis une page publique.
        board._remercier(self._en_paragraphes(corps))
        return request.redirect(
            "/celebration/%s/tableau?cle=%s&merci=1" % (token, cle))

    @route("/celebration/<string:token>/diaporama", type="http",
           auth="public", methods=["GET"], csrf=False, sitemap=False)
    def page_diaporama(self, token, **kw):
        board = self._tableau(token)
        if not board:
            return self._indisponible()
        return self._rendre(
            "bf_celebrations.page_diaporama",
            {
                "messages": board.post_ids.filtered(
                    lambda p: p.state == "published"),
                "token": token,
            },
            board,
        )

    # ------------------------------------------------------------------
    # Les binaires
    # ------------------------------------------------------------------

    @route("/celebration/<string:token>/image/<int:post_id>", type="http",
           auth="public", methods=["GET"], csrf=False, sitemap=False)
    def image_message(self, token, post_id, **kw):
        board = self._tableau(token)
        if not board:
            return request.not_found()
        message = board.post_ids.filtered(
            lambda p: p.id == post_id and p.state == "published")
        if not message or not message.image:
            return request.not_found()
        import base64
        contenu = base64.b64decode(message.image)
        # ⚠️ Le type est celui des OCTETS, pas un « image/png » posé
        # d'office : sous `X-Content-Type-Options: nosniff`, un GIF annoncé
        # PNG est un mensonge, et c'est justement le GIF qu'on veut voir
        # bouger.
        return _entetes(request.make_response(contenu, headers=[
            ("Content-Type", guess_mimetype(contenu, default="image/png")),
            ("Content-Length", str(len(contenu))),
            ("Cache-Control", "private, max-age=600"),
        ]))

    @route("/celebration/<string:token>/fond", type="http", auth="public",
           methods=["GET"], csrf=False, sitemap=False)
    def image_fond(self, token, **kw):
        board = self._tableau(token)
        if not board or not board.background_image:
            return request.not_found()
        import base64
        contenu = base64.b64decode(board.background_image)
        return _entetes(request.make_response(contenu, headers=[
            ("Content-Type", guess_mimetype(contenu, default="image/png")),
            ("Content-Length", str(len(contenu))),
            ("Cache-Control", "private, max-age=600"),
        ]))

    # ------------------------------------------------------------------
    # Le souvenir, à garder hors du système
    # ------------------------------------------------------------------
    # Servis seulement une fois la carte livrée : avant, la page se signe
    # et rien n'est fini. Après, le lien mourra un jour (compte fermé,
    # purge de rétention) et ces deux fichiers sont ce qui reste.

    @route("/celebration/<string:token>/pdf", type="http", auth="public",
           methods=["GET"], csrf=False, sitemap=False)
    def souvenir_pdf(self, token, **kw):
        board = self._tableau(token)
        if not board or board.state != "delivered":
            return self._indisponible(_(
                "La carte n'est pas encore livrée."))
        contenu = board._pdf_souvenir()
        nom = "Carte - %s.pdf" % (board.recipient_name or board.name)
        return _entetes(request.make_response(contenu, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", str(len(contenu))),
            ("Content-Disposition", content_disposition(nom)),
        ]))

    @route("/celebration/<string:token>/souvenir", type="http",
           auth="public", methods=["GET"], csrf=False, sitemap=False)
    def souvenir_html(self, token, **kw):
        board = self._tableau(token)
        if not board or board.state != "delivered":
            return self._indisponible(_(
                "La carte n'est pas encore livrée."))
        contenu = board._html_souvenir()
        nom = "Carte - %s.html" % (board.recipient_name or board.name)
        # ⚠️ En pièce à télécharger, jamais affichée depuis notre origine :
        # une page autonome est faite pour être ouverte depuis le disque.
        return _entetes(request.make_response(contenu, headers=[
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(contenu))),
            ("Content-Disposition", content_disposition(nom)),
        ]))

    @route("/celebration/<string:token>/qr", type="http", auth="public",
           methods=["GET"], csrf=False, sitemap=False)
    def code_qr(self, token, **kw):
        board = self._tableau(token)
        if not board:
            return request.not_found()
        contenu = board.qr_png()
        return _entetes(request.make_response(contenu, headers=[
            ("Content-Type", "image/png"),
            ("Content-Length", str(len(contenu))),
        ]))
