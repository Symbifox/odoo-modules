"""Les routes publiques des pages de liens.

La décision qui gouverne ce fichier : UN SLUG INCONNU REND UN 404 FRANC.

Le module voisin `bf_appointment` redirige en silence vers son index quand le
slug ne résout pas. C'est acceptable là où l'adresse est cliquée depuis un
courriel, qu'on peut corriger et renvoyer. Ici l'adresse part dans un QR
IMPRIMÉ dans une signature : elle ne se corrige plus une fois partie. Une
redirection silencieuse donnerait une page qui s'affiche, donc l'apparence du
succès, et personne ne saurait jamais que le QR pointe à côté. Le 404 est ce
qui rend la panne visible pendant qu'on peut encore réimprimer.

Corollaire : tous les refus rendent le MÊME 404. Un slug inexistant, une page
en brouillon, fermée, archivée ou expirée sont indiscernables de l'extérieur,
sans quoi l'adresse deviendrait un oracle qui confirme à un visiteur anonyme
quels slugs existent.
"""

import base64
import logging

from odoo import http
from odoo.tools.mimetypes import guess_mimetype
from odoo.http import Controller, request, route

_logger = logging.getLogger(__name__)

# Une page de liens charge une image de profil et rien d'autre : ni script
# tiers, ni cadre, ni formulaire. La politique est donc étroite par défaut.
LINKPAGE_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'none'; "
    "base-uri 'self'"
)


def _secure(response):
    """Poser les en-têtes de sécurité. Sans effet sur une redirection."""
    try:
        headers = response.headers
    except AttributeError:
        return response
    headers["Content-Security-Policy"] = LINKPAGE_CSP
    headers["X-Frame-Options"] = "DENY"
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


class BfLinkpageController(Controller):

    @route(
        "/l/<string:slug>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def linkpage_public(self, slug, **kwargs):
        page = request.env["bf.linkpage"]._resolve_slug(slug)
        if not page:
            return request.not_found()
        page._register_visit()
        # Le logo est lu en sudo : le visiteur public n'a aucun droit sur
        # res.company, et le gabarit ne doit décider d'afficher le pied qu'en
        # sachant qu'une image existe vraiment derrière la route.
        company = page.sudo()._company()
        response = request.render("bf_linkpage.linkpage_public", {
            "languages": self._page_languages(page),
            "page": page,
            "links": page._public_links(),
            "socials": page._social_links(),
            "vcard": page._vcard_available(),
            "company_logo": bool(company and company.logo),
            "company_name": company.name if company else "",
            # Une page ponctuelle n'a pas à finir dans un index de moteur de
            # recherche : elle est destinée à une poignée de gens et elle
            # expire.
            "noindex": page.kind == "oneoff",
        })
        return _secure(response)

    @route(
        "/l/<string:slug>/go/<int:link_id>",
        type="http",
        auth="public",
        website=False,
        sitemap=False,
    )
    def linkpage_go(self, slug, link_id, **kwargs):
        """Compter le clic puis rediriger.

        Le lien est retrouvé DANS la page, pas par son seul identifiant : sans
        cette contrainte, l'adresse permettrait de faire compter un clic sur
        n'importe quel lien de n'importe quelle page, et surtout de faire
        rediriger le site vers l'adresse d'un lien non publié.
        """
        page = request.env["bf.linkpage"]._resolve_slug(slug)
        if not page:
            return request.not_found()
        link = page._public_links().filtered(lambda item: item.id == link_id)
        if not link:
            return request.not_found()
        link._register_click()
        return request.redirect(link.resolved_url, local=False)

    @staticmethod
    def _page_languages(page):
        """Les versions linguistiques de la page, pour le sélecteur.

        L'adresse se construit avec `url_code` et non `code` : Odoo route sur
        `/en/…`, pas sur `/en_CA/…`. La langue par défaut du site n'a PAS de
        préfixe, et lui en donner un mènerait à une page servie, mais sous une
        deuxième adresse pour le même contenu.

        Rendu vide s'il n'y a qu'une langue : un sélecteur à une entrée est un
        bouton qui ne fait rien.
        """
        website = request.website
        langs = website.language_ids
        if len(langs) < 2:
            return []
        courant = request.env.lang
        defaut = website.default_lang_id
        sortie = []
        for lang in langs:
            prefixe = "" if lang == defaut else "/%s" % lang.url_code
            sortie.append({
                "code": lang.code,
                "label": (lang.url_code or lang.code).split("_")[0].upper(),
                "name": lang.name,
                "url": "%s/l/%s" % (prefixe, page.slug),
                "current": lang.code == courant,
            })
        return sortie

    @staticmethod
    def _image_response(payload, filename):
        """Servir une image en DISANT ce qu'elle est.

        Le type ne peut pas être supposé. Le logo de Blue Fox est un SVG, les
        photos de contact sont des PNG ou des JPEG selon ce qui a été téléversé.
        Annoncer `image/png` pour tout le monde marchait par accident jusqu'à ce
        que le site pose `X-Content-Type-Options: nosniff` : le navigateur cesse
        alors de deviner, refuse le fichier, et affiche une image brisée. La
        route répond pourtant 200 avec le bon nombre d'octets, donc rien ne
        signale la panne côté serveur. Elle ne se voit qu'à l'écran.
        """
        mimetype = guess_mimetype(payload) or "application/octet-stream"
        headers = [
            ("Content-Type", mimetype),
            ("Content-Length", len(payload)),
            ("Cache-Control", "public, max-age=3600"),
        ]
        if mimetype == "image/svg+xml":
            # Un SVG est un document exécutable. Servi en pièce jointe, il ne
            # peut pas devenir un vecteur de script sur notre propre domaine,
            # et une balise <img> l'affiche quand même.
            headers.append(("Content-Disposition", http.content_disposition(filename)))
        return request.make_response(payload, headers=headers)

    @route(
        "/l/<string:slug>/avatar",
        type="http",
        auth="public",
        website=False,
        sitemap=False,
    )
    def linkpage_avatar(self, slug, **kwargs):
        """Servir la photo de la page.

        Pourquoi une route à nous plutôt que `/web/image/bf.linkpage/<id>/avatar` :
        l'usager public n'a AUCUN droit de lecture sur `bf.linkpage`, et
        `/web/image` répond alors **200 avec une image de remplacement** au lieu
        d'une erreur. Mesuré le 2026-08-30 : 6078 octets de remplacement servis
        au visiteur là où la photo en fait 77. La page s'affichait donc avec une
        silhouette générique, sans que rien ne signale la panne — ni au
        visiteur, ni aux journaux.

        Ici la lecture passe par `_resolve_slug`, donc une page qui n'est pas en
        ligne ne divulgue pas sa photo, et une page sans photo rend un 404
        plutôt qu'une silhouette.
        """
        page = request.env["bf.linkpage"]._resolve_slug(slug)
        raw = page and page._photo_payload()
        if not raw:
            return request.not_found()
        return self._image_response(base64.b64decode(raw), "photo-%s" % page.slug)

    @route(
        "/l/<string:slug>/logo",
        type="http",
        auth="public",
        website=False,
        sitemap=False,
    )
    def linkpage_logo(self, slug, **kwargs):
        """Le logo de l'entreprise de la page.

        Même raison que pour la photo : `/web/image/res.company/<id>/logo`
        répondrait 200 avec une image de remplacement à un visiteur sans droit
        de lecture, et la page afficherait une silhouette au lieu du logo sans
        que rien ne le signale. On résout la page d'abord, donc une page hors
        ligne ne sert rien.
        """
        page = request.env["bf.linkpage"]._resolve_slug(slug)
        if not page:
            return request.not_found()
        company = page.sudo()._company()
        if not company or not company.logo:
            return request.not_found()
        return self._image_response(base64.b64decode(company.logo), "logo-%s" % page.slug)

    @route(
        "/l/<string:slug>/vcard.vcf",
        type="http",
        auth="public",
        website=False,
        sitemap=False,
    )
    def linkpage_vcard(self, slug, **kwargs):
        """La carte de visite de la personne, en téléchargement.

        Publique comme la page : elle ne contient QUE ce que la page affiche
        déjà. Le contraire serait un canal détourné pour lire des coordonnées
        qu'on n'a pas voulu publier.
        """
        page = request.env["bf.linkpage"]._resolve_slug(slug)
        if not page or not page._vcard_available():
            return request.not_found()
        payload = page._vcard()
        return request.make_response(payload, headers=[
            ("Content-Type", "text/vcard; charset=utf-8"),
            ("Content-Length", len(payload)),
            ("Content-Disposition", http.content_disposition("%s.vcf" % page.slug)),
            ("Cache-Control", "public, max-age=300"),
        ])

    @route(
        "/l/<string:slug>/qr.png",
        type="http",
        auth="user",
        website=False,
        sitemap=False,
    )
    def linkpage_qr(self, slug, branded="1", size="10", **kwargs):
        """Le QR de la page, en PNG.

        Réservé aux usagers connectés. Le QR n'encode qu'une adresse publique,
        donc il ne divulgue rien ; mais le fabriquer coûte du calcul et de la
        mémoire, et une route publique qui compose une image à la demande est
        un levier commode pour saturer le serveur. Le QR se télécharge une
        fois, par la personne qui monte sa signature.
        """
        page = request.env["bf.linkpage"].search([("slug", "=", slug)], limit=1)
        if not page:
            return request.not_found()
        try:
            box_size = max(4, min(20, int(size)))
        except (TypeError, ValueError):
            box_size = 10
        payload = page._qr_png(branded=branded not in ("0", "false", ""), box_size=box_size)
        filename = "qr-%s.png" % page.slug
        return request.make_response(payload, headers=[
            ("Content-Type", "image/png"),
            ("Content-Length", len(payload)),
            ("Content-Disposition", http.content_disposition(filename)),
            ("Cache-Control", "private, max-age=0, no-store"),
        ])
