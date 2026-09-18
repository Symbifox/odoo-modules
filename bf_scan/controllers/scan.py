"""La page ``/scan`` élargie : une carte, une facture, un document.

Pourquoi un module à part plutôt qu'une rallonge de ``bf_contact_enrichment`` :
la page est mince, mais ce qu'elle écrit ne l'est pas. Une facture fournisseur
touche la comptabilité, un document touche le bloc-notes et le fil de n'importe
quelle fiche. Faire dépendre un module de contacts de ``account`` pour ça
mélangerait trois domaines dans un seul paquet, et chaque base qui ne veut que
les cartes hériterait des deux autres.

Le contrôleur hérite de celui des cartes : les routes redéfinies ici
(``/scan`` et les deux routes d'infrastructure) remplacent les siennes, le
reste (lecture de la carte, enregistrement du contact) lui reste. Une base sans
``bf_scan`` garde donc exactement la page d'avant.

Ce que ce fichier ne fait jamais :

* **lire une pièce.** Aucun appel au pont, aucune invite. Poser une photo et la
  lire sont deux gestes, et le second appartient à ``bf_invoice_ocr``.
* **écrire ``ocr_state``.** Une facture déposée reste à ``none``, donc visible
  du passage horaire. La marquer en erreur ici la rendrait invisible pour
  toujours au module qui, lui, saura la lire demain.
* **faire confiance au nom du fichier.** Le type se décide sur les octets.
"""

import base64
import binascii
import json
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from odoo.addons.bf_contact_enrichment.controllers.portal_card import (
    MANIFEST,
    SERVICE_WORKER,
    BfContactScanPortal,
    _accent,
)

_logger = logging.getLogger(__name__)

#: Droit qui ouvre la tuile « Facture ». Le groupe de facturation, pas celui de
#: la comptabilité complète : saisir une facture fournisseur est le geste d'une
#: personne qui achète, pas d'un comptable.
GROUPE_FACTURE = "account.group_account_invoice"

#: Plafond de la pièce reçue, une fois décodée. Le navigateur réduit déjà la
#: photo à 1600 px sur son grand côté (≈ 300 Ko) ; ce plafond n'existe que pour
#: le chemin « choisir un fichier existant », où rien ne garantit la taille.
TAILLE_MAX = 12 * 1024 * 1024

#: Types acceptés, reconnus aux octets. Le nom du fichier ne décide de rien :
#: un téléphone nomme « image0 » ou « attachment », et un nom est de toute
#: façon fourni par le client.
SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"%PDF-", "application/pdf", "pdf"),
)

#: Marques HEIC/HEIF, reconnues pour être refusées AVEC leur nom. Rien ici ne
#: sait décoder ce format ; un refus qui le nomme dit quoi faire, un refus
#: générique laisse la personne reprendre la même photo trois fois.
MARQUES_HEIC = (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1", b"ftypheim")

#: Rappels offerts au dépôt d'un document, en jours.
RAPPELS = {"aucun": None, "aujourdhui": 0, "demain": 1, "semaine": 7}


def _type_de_piece(octets):
    """(mimetype, extension) d'après les octets, ou lève ``UserError``."""
    tete = octets[:32]
    for signature, mimetype, extension in SIGNATURES:
        if tete.startswith(signature):
            return mimetype, extension
    if tete[4:8] == b"ftyp" and any(m in tete for m in MARQUES_HEIC):
        raise UserError(_(
            "Cette photo est au format HEIC, que le serveur ne sait pas lire. "
            "Reprenez-la avec l'appareil photo de la page plutôt qu'en la "
            "choisissant dans la galerie."))
    if tete.startswith(b"RIFF") and tete[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise UserError(_(
        "Ce fichier n'est ni une photo (JPEG, PNG, WEBP) ni un PDF."))


def _octets(image_b64):
    """Décode la charge reçue, avec ses deux refus explicites."""
    charge = (image_b64 or "").strip()
    if not charge:
        raise UserError(_("Aucune image reçue."))
    # Une page peut envoyer la forme « data:image/jpeg;base64,… » ; la couper
    # ici évite un décodage qui réussirait à moitié.
    if charge.startswith("data:"):
        charge = charge.split(",", 1)[-1]
    try:
        octets = base64.b64decode(charge, validate=True)
    except (binascii.Error, ValueError):
        raise UserError(_("L'image reçue est illisible."))
    if not octets:
        raise UserError(_("L'image reçue est vide."))
    if len(octets) > TAILLE_MAX:
        raise UserError(_(
            "Le fichier dépasse %s Mo. Reprenez-le en photo plutôt que de "
            "l'envoyer tel quel.") % (TAILLE_MAX // (1024 * 1024)))
    return octets


def _nom_de_piece(nom_propose, extension, defaut):
    """Un nom de fichier sûr : jamais de chemin, toujours la bonne extension."""
    nom = (nom_propose or "").strip().replace("\\", "/").split("/")[-1]
    nom = "".join(c for c in nom if c.isprintable() and c not in '"\n\r\t')[:120]
    if not nom:
        nom = defaut
    if "." not in nom:
        nom = "%s.%s" % (nom, extension)
    return nom


def _json_erreur(exc, defaut):
    """Message d'une ``UserError`` pour un téléphone, sans trace d'exécution."""
    return {"error": exc.args[0] if exc.args else defaut}


class ScanEtendu(BfContactScanPortal):
    """Les trois gestes de la page, et l'infrastructure qui les porte."""

    # ── Droits ──────────────────────────────────────────────────────

    @staticmethod
    def _peut_facture():
        """Vrai si la personne peut saisir une facture fournisseur.

        Les deux moitiés comptent : le groupe ouvre le menu Facturation, le
        droit de création est ce qui décide vraiment. Une règle
        d'enregistrement peut encore refuser la fiche elle-même — c'est le
        ``create`` qui le dira, et son refus est rendu tel quel.
        """
        return (request.env.user.has_group(GROUPE_FACTURE)
                and request.env["account.move"].has_access("create"))

    @staticmethod
    def _peut_document():
        """Vrai si la personne peut déposer un document au bloc-notes.

        Un compte de portail n'a rien à faire ici : la page sert à classer des
        pièces internes, et le tampon est une note interne.
        """
        return (request.env.user._is_internal()
                and request.env["bf.note"].has_access("create"))

    @classmethod
    def _garde_facture(cls):
        if not request.env.user.has_group(GROUPE_FACTURE):
            raise AccessError(_(
                "Votre compte n'a pas accès à la facturation."))
        if not request.env["account.move"].has_access("create"):
            raise AccessError(_(
                "Votre compte peut lire les factures, mais pas en créer."))

    @classmethod
    def _garde_document(cls):
        if not request.env.user._is_internal():
            raise AccessError(_("Cette page est réservée aux comptes internes."))
        if not request.env["bf.note"].has_access("create"):
            raise AccessError(_(
                "Votre compte ne peut pas créer de note."))

    # ── La page d'accueil ───────────────────────────────────────────

    @http.route("/scan", type="http", auth="user", methods=["GET"])
    def scan_page(self, **kw):
        """Les tuiles. Chacune n'apparaît que si son geste est permis.

        Cacher une tuile plutôt que la griser : une page tenue à bout de bras
        n'a pas la place d'expliquer trois droits, et la page de chaque geste
        dit, elle, exactement ce qui manque.
        """
        accent, encre = _accent()
        tuiles = []
        if self._may_scan():
            tuiles.append({
                "url": "/scan/carte",
                "ico": "📇",
                "titre": _("Carte d'affaires"),
                "detail": _("Lue, corrigée, puis enregistrée en contact."),
            })
        if self._peut_facture():
            tuiles.append({
                "url": "/scan/facture",
                "ico": "🧾",
                "titre": _("Facture"),
                "detail": _("Crée un brouillon de facture fournisseur."),
            })
        if self._peut_document():
            tuiles.append({
                "url": "/scan/document",
                "ico": "📄",
                "titre": _("Document"),
                "detail": _("Au bloc-notes avec un rappel, ou au fil d'une fiche."),
            })
        return request.render("bf_scan.page_menu", {
            "tuiles": tuiles,
            "user_name": request.env.user.name,
            "accent": accent,
            "accent_ink": encre,
        })

    # ── La carte, telle qu'elle était ───────────────────────────────

    @http.route("/scan/carte", type="http", auth="user", methods=["GET"])
    def scan_carte(self, **kw):
        """La page des cartes, déménagée sous son propre chemin.

        Le gabarit reste celui de ``bf_contact_enrichment`` : ses routes JSON
        (`/scan/extract`, `/scan/save`) et son script ne bougent pas, donc rien
        de ce qui marchait ne dépend de ce déménagement.
        """
        accent, encre = _accent()
        return request.render("bf_contact_enrichment.scan_page", {
            "has_access": self._may_scan(),
            "user_name": request.env.user.name,
            "accent": accent,
            "accent_ink": encre,
        })

    # ── La facture ──────────────────────────────────────────────────

    @http.route("/scan/facture", type="http", auth="user", methods=["GET"])
    def page_facture(self, **kw):
        accent, encre = _accent()
        return request.render("bf_scan.page_facture", {
            "has_access": self._peut_facture(),
            "user_name": request.env.user.name,
            "societe": request.env.company.name,
            "accent": accent,
            "accent_ink": encre,
        })

    @http.route("/scan/facture/deposer", type="json", auth="user",
                methods=["POST"])
    def facture_deposer(self, image_b64=None, filename=None, **kw):
        """Crée le brouillon, y attache la photo, et s'arrête là.

        L'ordre compte : la facture d'abord, la pièce ensuite, le fil en
        dernier. Une pièce jointe posée avec ``res_id`` n'apparaît pas dans le
        fil — seul ``message_post`` l'y met, et c'est le fil que les gens
        regardent.
        """
        try:
            self._garde_facture()
            octets = _octets(image_b64)
            mimetype, extension = _type_de_piece(octets)
        except AccessError as exc:
            return _json_erreur(exc, _("Accès refusé."))
        except UserError as exc:
            return _json_erreur(exc, _("Pièce refusée."))

        Move = request.env["account.move"]
        try:
            facture = Move.with_context(
                default_move_type="in_invoice",
                mail_create_nolog=True,
            ).create({"move_type": "in_invoice"})
        except AccessError:
            return {"error": _("Votre compte n'a pas le droit de créer cette "
                               "facture.")}
        except Exception:  # noqa: BLE001 — jamais de trace sur un téléphone
            _logger.exception("Dépôt de facture refusé pour %s",
                              request.env.user.login)
            return {"error": _("La facture n'a pas pu être créée.")}

        nom = _nom_de_piece(filename, extension, _("facture"))
        piece = request.env["ir.attachment"].create({
            "name": nom,
            "datas": base64.b64encode(octets),
            "mimetype": mimetype,
            "res_model": "account.move",
            "res_id": facture.id,
        })
        au_fil = self._poster(
            facture, _("Photographiée depuis la page Numériser."), piece)

        return {
            "move_id": facture.id,
            "name": self._nom_lisible(facture),
            "url": "/odoo/action-account.action_move_in_invoice_type/%s"
                   % facture.id,
            "lecture": self._lecture_si_possible(facture),
            "au_fil": au_fil,
        }

    @staticmethod
    def _poster(fiche, texte, piece):
        """Pose la pièce dans le fil. Rend faux si le fil a refusé.

        🔴 Un fil peut refuser pour une raison qui n'a rien à voir avec la
        pièce : Odoo exige une adresse d'expéditeur calculable, et un compte
        interne sans courriel n'en a pas. Laisser cette erreur remonter
        perdrait le geste alors que la facture et la pièce existent déjà —
        la pièce reste accrochée à la fiche par ``res_id``, donc visible dans
        son tiroir de pièces jointes, et la page le dit.
        """
        try:
            fiche.message_post(body=Markup("<p>%s</p>") % texte,
                               attachment_ids=[piece.id])
            return True
        except Exception:  # noqa: BLE001
            _logger.exception("Message refusé sur %s %s", fiche._name, fiche.id)
            return False

    @staticmethod
    def _nom_lisible(facture):
        """Un brouillon n'a pas encore de numéro : ``name`` y vaut « / »."""
        nom = (facture.name or "").strip()
        if nom and nom != "/":
            return nom
        return _("Brouillon n° %s") % facture.id

    @staticmethod
    def _lecture_si_possible(facture):
        """Lance la lecture SEULEMENT si le module sait lire cette pièce.

        🔴 Appeler la lecture sans ce test écrirait ``ocr_state = error`` sur
        une facture parfaitement bonne, et le passage horaire ne reprend que
        les ``none`` : la facture resterait non lue pour toujours. Tant que
        ``bf_invoice_ocr`` ne lit que les PDF, une photo repart donc d'ici
        sans état, et c'est le passage suivant qui la prendra le jour où il
        saura. Rien à changer ici ce jour-là.
        """
        if not hasattr(facture, "_ocr_has_source"):
            return "absent"
        try:
            if not facture._ocr_has_source():
                return "en_attente"
            facture.action_ocr_scan()
        except Exception:  # noqa: BLE001 — la facture existe, c'est l'essentiel
            _logger.exception("Lecture OCR échouée sur la facture %s",
                              facture.id)
            facture.write({"ocr_state": "none"})
            return "en_attente"

        etat = facture.ocr_state
        if etat == "error" and not facture.ocr_raw_response:
            # 🔴 Une panne passagère ne doit pas geler la facture. Le passage
            # horaire ne reprend que les ``none`` : un « error » posé parce que
            # la socket du pont dormait condamnerait cette facture à ne jamais
            # être lue, alors que la pièce est bonne.
            #
            # La distinction se mesure, elle ne se devine pas : le module
            # n'écrit ``ocr_raw_response`` que lorsqu'il a vraiment atteint le
            # modèle. Pas de réponse brute = l'appel n'est jamais arrivé
            # (socket absente, délai dépassé) = transitoire. Réponse brute
            # présente = le modèle a répondu et c'est le module qui a refusé =
            # ce n'est pas au passage suivant de réessayer indéfiniment.
            facture.write({"ocr_state": "none"})
            return "en_attente"
        return etat or "en_attente"

    # ── Le document ─────────────────────────────────────────────────

    @http.route("/scan/document", type="http", auth="user", methods=["GET"])
    def page_document(self, **kw):
        accent, encre = _accent()
        return request.render("bf_scan.page_document", {
            "has_access": self._peut_document(),
            "user_name": request.env.user.name,
            "accent": accent,
            "accent_ink": encre,
        })

    @http.route("/scan/document/cibles", type="json", auth="user",
                methods=["POST"])
    def document_cibles(self, query=None, **kw):
        """Le sélecteur de fiche, délégué à ``bf.chatter.target``.

        Il ne rend que des fiches lisibles par la personne connectée, et il
        comprend aussi bien « 22299 » qu'une URL Odoo collée depuis un
        courriel. Rien n'est réimplémenté ici : deux sélecteurs divergeraient
        au premier modèle ajouté.
        """
        try:
            self._garde_document()
        except AccessError as exc:
            return _json_erreur(exc, _("Accès refusé."))
        groupes = request.env["bf.chatter.target"].search_targets(query, limit=5)
        return {"groupes": groupes}

    @http.route("/scan/document/deposer", type="json", auth="user",
                methods=["POST"])
    def document_deposer(self, image_b64=None, filename=None, titre=None,
                         destination="tampon", cible=None, rappel="aucun",
                         **kw):
        """Dépose la pièce au bloc-notes, ou au fil de la fiche choisie."""
        try:
            self._garde_document()
            octets = _octets(image_b64)
            mimetype, extension = _type_de_piece(octets)
        except AccessError as exc:
            return _json_erreur(exc, _("Accès refusé."))
        except UserError as exc:
            return _json_erreur(exc, _("Pièce refusée."))

        cible = cible or {}
        fiche = None
        if cible.get("model") and cible.get("id"):
            fiche = request.env["bf.chatter.target"]._browse_if_allowed(
                cible["model"], int(cible["id"]))
            if not fiche:
                return {"error": _("Cette fiche n'existe plus, ou vous n'y "
                                   "avez pas accès.")}

        if destination == "fiche":
            if not fiche:
                return {"error": _("Choisissez la fiche avant d'envoyer.")}
            return self._deposer_au_fil(fiche, octets, mimetype, extension,
                                        filename, titre)
        return self._deposer_au_tampon(octets, mimetype, extension, filename,
                                       titre, fiche, rappel)

    def _deposer_au_fil(self, fiche, octets, mimetype, extension, filename,
                        titre):
        """La pièce part directement dans le fil de la fiche.

        🔴 C'est l'ÉCRITURE qu'il faut, pas ``_mail_post_access``. Plusieurs
        modèles (``project.task`` le premier) posent
        ``_mail_post_access = "read"`` : n'importe qui pouvant lire la tâche
        peut y écrire un commentaire. Mais ``ir.attachment.check()`` contrôle,
        lui, le droit d'ÉCRIRE la fiche visée, et une pièce est exactement ce
        que cette page dépose. Contrôler le seuil du message laissait donc
        passer la personne jusqu'à une erreur d'accès brute d'Odoo — mesuré en
        production sur une tâche dont la lecture était permise et
        l'écriture non.

        Le contrôle vient AVANT la création de la pièce : un refus ne doit
        jamais laisser une pièce orpheline derrière lui.
        """
        try:
            fiche.check_access("write")
        except AccessError:
            return {"error": _("Vous pouvez lire « %s », mais pas y écrire.")
                             % fiche.display_name}

        nom = _nom_de_piece(filename, extension, _("document"))
        try:
            piece = request.env["ir.attachment"].create({
                "name": nom,
                "datas": base64.b64encode(octets),
                "mimetype": mimetype,
                "res_model": fiche._name,
                "res_id": fiche.id,
            })
        except AccessError:
            return {"error": _("Vous pouvez lire « %s », mais pas y écrire.")
                             % fiche.display_name}
        texte = (titre or "").strip() or _(
            "Document numérisé depuis la page Numériser.")
        try:
            fiche.message_post(body=Markup("<p>%s</p>") % texte,
                               attachment_ids=[piece.id])
        except AccessError:
            piece.unlink()
            return {"error": _("Vous pouvez lire « %s », mais pas y écrire.")
                             % fiche.display_name}
        except Exception:  # noqa: BLE001
            _logger.exception("Message refusé sur %s %s", fiche._name, fiche.id)
            return {
                "destination": "fiche",
                "name": fiche.display_name,
                "url": self._url_de_fiche(fiche),
                "au_fil": False,
            }
        return {
            "destination": "fiche",
            "name": fiche.display_name,
            "url": self._url_de_fiche(fiche),
            "au_fil": True,
        }

    def _deposer_au_tampon(self, octets, mimetype, extension, filename, titre,
                           fiche, rappel):
        """La pièce va au bloc-notes, avec son lien et son rappel.

        Le bloc-notes est le tampon : une note porte la pièce, le lien
        éventuel vers la fiche, et l'activité qui fera revenir dessus. Le
        rappel se pose sur la fiche liée quand elle accepte les activités, et
        sur la note sinon — ``bf.note`` sait déjà faire ce tri, on ne le
        refait pas ici.
        """
        nom = _nom_de_piece(filename, extension, _("document"))
        valeurs = {
            "name": (titre or "").strip() or _("Document numérisé"),
            "body": Markup("<p>%s</p>") % _(
                "Déposé depuis la page Numériser."),
        }
        if fiche is not None:
            valeurs["link_ids"] = [(0, 0, {
                "res_model": fiche._name,
                "res_id": fiche.id,
            })]
        note = request.env["bf.note"].create(valeurs)

        piece = request.env["ir.attachment"].create({
            "name": nom,
            "datas": base64.b64encode(octets),
            "mimetype": mimetype,
            "res_model": "bf.note",
            "res_id": note.id,
        })
        au_fil = self._poster(note, nom, piece)

        jours = RAPPELS.get(rappel)
        rappel_pose = False
        if jours is not None:
            echeance = fields.Date.context_today(note) + timedelta(days=jours)
            try:
                note.deadline_date = echeance
                if note.link_ids:
                    note._create_activities_for_links(custom_deadline=echeance)
                else:
                    note._create_activity_on_self(echeance)
                rappel_pose = True
            except Exception:  # noqa: BLE001 — la pièce est posée, c'est l'essentiel
                _logger.exception("Rappel non posé sur la note %s", note.id)
        return {
            "destination": "tampon",
            "note_id": note.id,
            "name": note.name,
            "url": "/odoo/action-bf_bloc_notes.action_bf_note/%s" % note.id,
            "lien": fiche.display_name if fiche is not None else "",
            "rappel": rappel_pose,
            "au_fil": au_fil,
        }

    @staticmethod
    def _url_de_fiche(fiche):
        """Un chemin qui ouvre la fiche dans le client web d'Odoo 18."""
        return "/odoo/%s/%s" % (fiche._name.replace(".", "-"), fiche.id)

    # ── Infrastructure de l'application installée ───────────────────

    @http.route("/scan/manifest.webmanifest", type="http", auth="public",
                methods=["GET"], save_session=False)
    def scan_manifest(self, **kw):
        """La même application, avec son nouveau nom et ses trois raccourcis.

        ⚠️ ``id`` ne bouge pas. Le changer ferait une DEUXIÈME application à
        côté de celle déjà installée sur les téléphones, avec sa propre icône
        et sans rien dire.
        """
        fiche = dict(MANIFEST)
        icones = "/bf_scan/static/src/scan"
        fiche.update({
            "icons": [
                {"src": "%s/icon-192.png" % icones, "sizes": "192x192",
                 "type": "image/png", "purpose": "any"},
                {"src": "%s/icon-512.png" % icones, "sizes": "512x512",
                 "type": "image/png", "purpose": "any"},
                {"src": "%s/icon-maskable-192.png" % icones, "sizes": "192x192",
                 "type": "image/png", "purpose": "maskable"},
                {"src": "%s/icon-maskable-512.png" % icones, "sizes": "512x512",
                 "type": "image/png", "purpose": "maskable"},
            ],
            "name": _("Numériser"),
            "short_name": _("Numériser"),
            "description": _("Une carte, une facture ou un document, "
                             "photographiés et classés."),
            "shortcuts": [
                {"name": _("Carte d'affaires"), "url": "/scan/carte"},
                {"name": _("Facture"), "url": "/scan/facture"},
                {"name": _("Document"), "url": "/scan/document"},
            ],
        })
        return request.make_response(
            json.dumps(fiche, ensure_ascii=False),
            headers=[("Content-Type",
                      "application/manifest+json; charset=utf-8"),
                     ("Cache-Control", "public, max-age=3600")],
        )

    @http.route("/scan/sw.js", type="http", auth="public", methods=["GET"],
                save_session=False)
    def scan_service_worker(self, **kw):
        """L'agent de service du parent, plus les fichiers de ce module.

        Le nom du cache change avec le contenu (``v3``) : un agent qui garde
        l'ancien nom servirait la coquille d'hier à une page qui n'existe plus.
        """
        source = SERVICE_WORKER.replace("'bf-scan-v2'", "'bf-scan-v3'")
        source = source.replace(
            "  OFFLINE,\n];",
            "  OFFLINE,\n"
            "  '/bf_scan/static/src/scan/scan_plus.css',\n"
            "  '/bf_scan/static/src/scan/commun.js',\n"
            "  '/bf_scan/static/src/scan/facture.js',\n"
            "  '/bf_scan/static/src/scan/document.js',\n"
            "  '/bf_scan/static/src/scan/icon-192.png',\n"
            "];")
        source = source.replace(
            "  if (url.pathname.startsWith('/bf_contact_enrichment/static/')) {",
            "  if (url.pathname.startsWith('/bf_contact_enrichment/static/') ||\n"
            "      url.pathname.startsWith('/bf_scan/static/')) {")
        return request.make_response(
            source,
            headers=[("Content-Type", "application/javascript; charset=utf-8"),
                     ("Service-Worker-Allowed", "/scan"),
                     ("Cache-Control", "no-cache")],
        )
