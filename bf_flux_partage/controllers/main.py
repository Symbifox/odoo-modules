# -*- coding: utf-8 -*-
"""Les deux portes publiques d'un lien de partage : la page et le RSS.

Aucune session n'est demandée. Le jeton est le seul secret : un jeton
inconnu, échu ou révoqué répond 404, sans dire lequel des trois.
"""
import re
from email.utils import format_datetime

from lxml import etree
from pytz import utc
from werkzeug.exceptions import NotFound

from odoo import fields, http
from odoo.http import request

# noindex : une veille partagée n'a rien à faire dans un moteur de recherche.
# no-referrer : cliquer un article ne révèle pas au média l'adresse secrète.
ENTETES = [
    ("X-Robots-Tag", "noindex, nofollow"),
    ("Referrer-Policy", "no-referrer"),
    # ⚠️ Rien en cache : avec « max-age », un lien révoqué restait visible
    # jusqu'à cinq minutes chez qui l'avait déjà ouvert (vu au parcours).
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
]
CHAPEAU_MAX = 600
COULEUR_RE = re.compile(r"^#[0-9A-Fa-f]{3,8}$")
POLICE_RE = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")

CSS = """
body{margin:0;background:#F8FAFC;color:#374151;font-family:{police},Segoe UI,Arial,sans-serif}
header{background:{sombre};color:#E6EDF3;padding:18px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:4px solid {accent}}
header .logo{background:#fff;border-radius:8px;padding:6px 12px;display:inline-flex;align-items:center}
header img{height:36px;width:auto;display:block}
main{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:24px;margin:0 0 4px 0;color:#111827}
.sous{color:#6B7280;margin:0 0 20px 0;font-size:14px}
.grille{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:16px}
.carte{background:#fff;border:1px solid #E5E7EB;border-radius:8px;overflow:hidden;display:flex;flex-direction:column}
.carte img{width:100%;height:150px;object-fit:cover;display:block}
.corps{padding:14px 16px;display:flex;flex-direction:column;flex-grow:1}
.meta{font-size:12px;color:#6B7280;display:flex;justify-content:space-between;gap:8px;margin-bottom:6px}
.carte h2{font-size:16px;line-height:1.35;margin:0 0 8px 0}
.carte h2 a{color:#111827;text-decoration:none}
.carte h2 a:hover{color:{accent};text-decoration:underline}
.carte p{font-size:14px;margin:0 0 12px 0;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.lire{margin-top:auto;color:{accent};font-weight:600;font-size:14px;text-decoration:none}
.rss{color:{accent};font-size:13px}
.vide{padding:48px;text-align:center;color:#6B7280}
footer{max-width:1180px;margin:0 auto;padding:8px 24px 32px 24px;font-size:12px;color:#9CA3AF}
"""


def _css(accent, sombre, police):
    """La feuille de style aux couleurs de la société. Les valeurs sont
    vérifiées avant d'entrer dans le CSS : une couleur ou une police mal saisie
    retombe sur le neutre au lieu de casser la page."""
    accent = accent if COULEUR_RE.match(accent or "") else "#1F2937"
    sombre = sombre if COULEUR_RE.match(sombre or "") else "#1F2937"
    police = police if POLICE_RE.match(police or "") else "Arial"
    return (CSS.replace("{accent}", accent).replace("{sombre}", sombre)
            .replace("{police}", police))


def _chapeau(texte):
    texte = (texte or "").strip()
    if len(texte) > CHAPEAU_MAX:
        texte = texte[:CHAPEAU_MAX].rsplit(" ", 1)[0] + " …"
    return texte


class FluxPartage(http.Controller):

    def _partage(self, jeton):
        part = request.env["bf.flux.partage"]._flux_ouvert(jeton)
        if not part:
            raise NotFound()
        part._flux_compter()
        return part

    @http.route("/flux/partage/<string:jeton>", type="http", auth="public",
                methods=["GET"], sitemap=False)
    def page(self, jeton, **kw):
        part = self._partage(jeton)
        societe = part.liste_id.company_id.sudo() or request.env.company.sudo()
        champs = societe._fields
        valeurs = {
            "partage": part,
            "liste": part.liste_id.sudo(),
            "elements": part._flux_elements(),
            "societe": societe,
            "css": _css(
                (champs.get("report_brand_primary") and societe.report_brand_primary)
                or societe.primary_color,
                champs.get("report_brand_dark") and societe.report_brand_dark,
                champs.get("font") and societe.font),
            "chapeau": _chapeau,
            "date": lambda d: fields.Date.to_string(d) if d else "",
        }
        # Le doctype s'ajoute ici : écrit dans le gabarit, QWeb l'échappait et
        # la page l'affichait en texte (vu au parcours).
        corps = "<!DOCTYPE html>\n" + str(request.env["ir.qweb"]._render("bf_flux_partage.page", valeurs))
        return request.make_response(corps, headers=ENTETES + [("Content-Type", "text/html; charset=utf-8")])

    @http.route("/flux/partage/<string:jeton>/rss", type="http", auth="public",
                methods=["GET"], sitemap=False)
    def rss(self, jeton, **kw):
        part = self._partage(jeton)
        liste = part.liste_id.sudo()
        rss = etree.Element("rss", version="2.0")
        canal = etree.SubElement(rss, "channel")
        etree.SubElement(canal, "title").text = liste.name
        etree.SubElement(canal, "link").text = part.url
        # Le nom seulement : la description de la liste est une note interne.
        etree.SubElement(canal, "description").text = liste.name
        etree.SubElement(canal, "language").text = "fr-ca"
        for elem in part._flux_elements():
            item = etree.SubElement(canal, "item")
            etree.SubElement(item, "title").text = elem.titre
            etree.SubElement(item, "link").text = elem.lien
            etree.SubElement(item, "description").text = _chapeau(elem.resume)
            guid = etree.SubElement(item, "guid", isPermaLink="false")
            guid.text = elem.cle
            if elem.date_publication:
                etree.SubElement(item, "pubDate").text = format_datetime(
                    utc.localize(elem.date_publication))
            if elem.emetteur:
                etree.SubElement(item, "author").text = elem.emetteur
            # Le nom de la source seulement, jamais son adresse : un flux peut
            # porter un jeton privé dans son URL.
            sources = part._flux_sources_de(elem)
            if sources:
                etree.SubElement(item, "category").text = sources[0].name
            if elem.image_url:
                etree.SubElement(item, "enclosure", url=elem.image_url, type="image/jpeg", length="0")
        corps = etree.tostring(rss, xml_declaration=True, encoding="utf-8")
        return request.make_response(
            corps, headers=ENTETES + [("Content-Type", "application/rss+xml; charset=utf-8")])
