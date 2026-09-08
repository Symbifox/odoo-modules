# -*- coding: utf-8 -*-
"""Un message signé sur le tableau.

Le contributeur n'a pas de compte : `author_name` est du texte libre, saisi
sur la page publique. Le corps est du HTML assaini par Odoo, l'image est une
pièce jointe. Rien n'est jamais rapatrié d'un tiers, donc aucune requête ne
sort du serveur quand quelqu'un signe.
"""

import json

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Un message de carte de fête n'est pas un roman, et un champ libre exposé
# sans compte est une porte. Les deux raisons vont dans le même sens.
LONGUEUR_MAX = 1200
NOM_MAX = 80

# L'encre : ce que la main a tracé sur la page, gardé en VECTEURS.
#
# Une image PNG du tracé aurait été plus simple à écrire, et fausse à
# l'usage : l'encre noire d'un tracé disparaît sur le thème sombre, et le
# thème d'une carte se change après coup. Des traits en coordonnées se
# redessinent en `currentColor`, donc dans la couleur de texte du thème du
# moment, ils pèsent quelques kilooctets, et ils restent nets à toute
# taille, sur la page comme dans le PDF.
#
# Le serveur ne garde que des NOMBRES : la charge du navigateur est relue,
# bornée, arrondie, puis réécrite. Aucune balise fournie par un anonyme
# n'atteint jamais la page.
ENCRE_LARGEUR, ENCRE_HAUTEUR = 800, 320
ENCRE_TRAITS_MAX = 400
ENCRE_POINTS_MAX = 20000
ENCRE_OCTETS_MAX = 300 * 1024
ENCRE_EPAISSEUR = 3.2
ENCRE_MARGE = 14
# Largeur de rendu par défaut (attribut `width`), celle qu'un moteur sans
# CSS moderne emploiera. La page l'écrase par sa feuille de style.
ENCRE_RENDU_LARGEUR = 420

STYLES = [
    ("typed", "Tapé"),
    ("hand", "Manuscrit"),
]


class CelebrationPost(models.Model):
    _name = "bf.celebration.post"
    _description = "Message sur un tableau de vœux"
    _order = "sequence, id"

    board_id = fields.Many2one(
        "bf.celebration.board", string="Tableau", required=True,
        ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)

    author_name = fields.Char(string="Signé", required=True)
    author_partner_id = fields.Many2one(
        "res.partner", string="Contact", ondelete="set null")
    author_user_id = fields.Many2one(
        "res.users", string="Usager", ondelete="set null",
        help="Rempli seulement quand la personne était connectée.")

    body = fields.Html(string="Message", sanitize=True, sanitize_style=True)
    style = fields.Selection(
        STYLES, string="Style", default="typed", required=True,
        help="« Manuscrit » rend le mot dans une police d'écriture à la main.")
    image = fields.Image(string="Image", max_width=1600, max_height=1600)
    # ⚠️ Odoo 18 garde les images d'un GIF animé au redimensionnement
    # (`ImageProcess.animated_frames`) : le GIF téléversé bouge encore.

    ink_strokes = fields.Text(
        string="Encre",
        help="Les traits dessinés à la main sur la page publique, en "
             "coordonnées. Redessinés dans la couleur du thème.")
    has_ink = fields.Boolean(
        string="Tracé à la main", compute="_compute_has_ink", store=True)

    state = fields.Selection(
        [
            ("pending", "En attente d'approbation"),
            ("published", "Visible"),
            ("rejected", "Retiré"),
        ],
        default="published", required=True, index=True)

    create_ip = fields.Char(
        string="Adresse d'origine", readonly=True, groups="base.group_system",
        help="Conservée pour retracer un abus sur un lien public. Effacée "
             "avec le tableau.")

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------

    @api.constrains("author_name")
    def _check_nom(self):
        for post in self:
            if len(post.author_name or "") > NOM_MAX:
                raise ValidationError(_(
                    "La signature dépasse %(n)s caractères.", n=NOM_MAX))

    @api.constrains("body")
    def _check_corps(self):
        for post in self:
            texte = post._texte_brut()
            if len(texte) > LONGUEUR_MAX:
                raise ValidationError(_(
                    "Le message dépasse %(n)s caractères.", n=LONGUEUR_MAX))

    def _texte_brut(self):
        self.ensure_one()
        from odoo.tools import html2plaintext
        return html2plaintext(self.body or "").strip()

    # ⚠️ `author_name` est dans la liste exprès : une contrainte ne se
    # vérifie que si l'un de SES champs est écrit. Un `create` sans corps,
    # sans image et sans encre n'en écrit aucun, et la contrainte dormait.
    # La signature, elle, est obligatoire, donc toujours écrite à la création.
    @api.constrains("author_name", "body", "image", "ink_strokes")
    def _check_non_vide(self):
        for post in self:
            if (not post._texte_brut() and not post.image
                    and not post._encre()):
                raise ValidationError(_(
                    "Un message vide ne signe rien : écrivez un mot, tracez "
                    "quelque chose ou ajoutez une image."))

    # ------------------------------------------------------------------
    # L'encre
    # ------------------------------------------------------------------

    @api.depends("ink_strokes")
    def _compute_has_ink(self):
        for post in self:
            post.has_ink = bool(post._encre())

    @api.model
    def _normaliser_encre(self, brut):
        """Relit la charge du navigateur et ne garde que des nombres bornés.

        Rend la chaîne JSON compacte à stocker, ou une chaîne vide si rien
        d'exploitable n'a été tracé. Lève `ValueError` si la charge n'est
        pas une liste de traits : le contrôleur traduit en refus poli.
        """
        if not brut:
            return ""
        if isinstance(brut, (bytes, bytearray)):
            brut = brut.decode("utf-8", "replace")
        if len(brut) > ENCRE_OCTETS_MAX:
            raise ValueError("encre trop lourde")
        try:
            traits = json.loads(brut)
        except ValueError as exc:
            raise ValueError("encre illisible") from exc
        if not isinstance(traits, list):
            raise ValueError("encre : liste attendue")
        if len(traits) > ENCRE_TRAITS_MAX:
            raise ValueError("encre : trop de traits")
        propres, total = [], 0
        for trait in traits:
            if not isinstance(trait, list):
                raise ValueError("encre : trait attendu")
            points = []
            for point in trait:
                if (not isinstance(point, (list, tuple)) or len(point) != 2
                        or not all(isinstance(c, (int, float)) and
                                   c == c for c in point)):
                    raise ValueError("encre : point attendu")
                x = min(max(float(point[0]), 0.0), float(ENCRE_LARGEUR))
                y = min(max(float(point[1]), 0.0), float(ENCRE_HAUTEUR))
                points.append([round(x, 1), round(y, 1)])
            total += len(points)
            if total > ENCRE_POINTS_MAX:
                raise ValueError("encre : trop de points")
            if points:
                propres.append(points)
        if not propres:
            return ""
        return json.dumps(propres, separators=(",", ":"))

    def _encre(self):
        """Les traits stockés, ou une liste vide. Ne lève jamais."""
        self.ensure_one()
        if not self.ink_strokes:
            return []
        try:
            traits = json.loads(self.ink_strokes)
        except ValueError:
            return []
        return traits if isinstance(traits, list) else []

    @staticmethod
    def _chemin_svg(points):
        """Un trait en chemin SVG lissé par quadratiques aux milieux.

        Le même lissage que le canevas du navigateur : ce que la personne a
        vu en traçant est ce que les autres voient sur la carte.
        """
        if len(points) == 1:
            x, y = points[0]
            # Un point seul : un tout petit segment, sinon rien ne se rend.
            return "M%.1f %.1fl0.1 0" % (x, y)
        morceaux = ["M%.1f %.1f" % tuple(points[0])]
        for i in range(1, len(points) - 1):
            mx = (points[i][0] + points[i + 1][0]) / 2
            my = (points[i][1] + points[i + 1][1]) / 2
            morceaux.append("Q%.1f %.1f %.1f %.1f" % (
                points[i][0], points[i][1], mx, my))
        morceaux.append("L%.1f %.1f" % tuple(points[-1]))
        return "".join(morceaux)

    def ink_svg(self):
        """Le tracé, en SVG en ligne, recadré sur ce qui a été dessiné.

        `stroke="currentColor"` : la couleur suit le texte du thème, sur la
        page comme dans le PDF. Le recadrage évite qu'une petite signature
        traîne un grand blanc autour d'elle. Tout ce qui entre dans la
        balise est un nombre formaté par nous : `Markup` est justifié.
        """
        self.ensure_one()
        traits = self._encre()
        if not traits:
            return Markup("")
        xs = [p[0] for t in traits for p in t]
        ys = [p[1] for t in traits for p in t]
        x0 = max(0.0, min(xs) - ENCRE_MARGE)
        y0 = max(0.0, min(ys) - ENCRE_MARGE)
        x1 = min(float(ENCRE_LARGEUR), max(xs) + ENCRE_MARGE)
        y1 = min(float(ENCRE_HAUTEUR), max(ys) + ENCRE_MARGE)
        largeur = max(x1 - x0, 40.0)
        hauteur = max(y1 - y0, 40.0)
        chemin = " ".join(self._chemin_svg(t) for t in traits)
        # ⚠️ `width` et `height` explicites, en plus du `viewBox` : le moteur
        # de wkhtmltopdf (un WebKit de 2011) rend un SVG en ligne sans
        # dimensions à ZÉRO, en silence. Vu au premier PDF du banc : la carte
        # de Léonie portait un blanc à la place du tracé. La CSS de la page
        # (`width: 100%; height: auto`) prend le dessus sur ces attributs.
        px_largeur = ENCRE_RENDU_LARGEUR
        px_hauteur = round(px_largeur * hauteur / largeur)
        return Markup(
            '<svg class="cel-encre-rendu" xmlns="http://www.w3.org/2000/svg" '
            'viewBox="%.1f %.1f %.1f %.1f" width="%d" height="%d" role="img" '
            'aria-label="%s" preserveAspectRatio="xMidYMid meet">'
            '<path d="%s" fill="none" stroke="currentColor" '
            'stroke-width="%.1f" stroke-linecap="round" '
            'stroke-linejoin="round"/></svg>'
        ) % (x0, y0, largeur, hauteur, px_largeur, px_hauteur,
             _("Tracé à la main"), chemin, ENCRE_EPAISSEUR)

    # ------------------------------------------------------------------
    # Modération
    # ------------------------------------------------------------------

    def action_publier(self):
        self.write({"state": "published"})
        return True

    def action_retirer(self):
        self.write({"state": "rejected"})
        return True
