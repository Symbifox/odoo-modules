"""Imprimer des étiquettes QR sur une planche du commerce.

🔴 Le logo est REFUSÉ quand le code ne fait pas 25 mm : il masque le centre du
code, que la correction H compense seulement s'il reste assez de modules
lisibles autour. Sous 20 mm, le code s'imprime, mais l'écran prévient qu'il faut
approcher le téléphone.

⚠️ ``decalage`` réutilise une planche entamée : les N premières places restent
vides. Sans lui, une demi-feuille d'étiquettes part à la poubelle à chaque
petite impression.
"""
import base64
import io

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.bf_qr_label_format import QR_MIN_LOGO_MM, QR_MIN_MM
from ..models.rendu import CouleurRefusee, pages_necessaires, planche, verifier_couleurs

TEXTES = [
    ("aucun", "Aucun"),
    ("reference", "N° d'étiquette"),
    ("libelle", "Libellé"),
    ("les_deux", "N° et libellé"),
]


class BfQrImprimer(models.TransientModel):
    _name = "bf.qr.imprimer"
    _description = "Imprimer des étiquettes QR"

    tag_ids = fields.Many2many("bf.nfc.tag", string="Étiquettes", readonly=True)
    count = fields.Integer(string="Nombre d'étiquettes", compute="_compute_apercu")
    format_id = fields.Many2one(
        "bf.qr.label.format", string="Format", required=True,
        default=lambda self: self.env["bf.qr.label.format"].search([], limit=1))
    decalage = fields.Integer(
        string="Places déjà utilisées",
        help="Sur la première feuille, combien d'étiquettes ont déjà été décollées. "
             "L'impression commence à la place suivante, ligne par ligne.")
    texte = fields.Selection(TEXTES, string="Texte", default="reference", required=True)
    avec_logo = fields.Boolean(string="Logo au centre")
    logo = fields.Binary(
        string="Logo", help="PNG ou JPEG. Vide : le logo de la société.")
    couleur_code = fields.Char(string="Couleur du code", default="#000000")
    couleur_fond = fields.Char(string="Couleur du fond", default="#FFFFFF")
    contours = fields.Boolean(
        string="Tracer les contours",
        help="Pour un essai sur papier ordinaire : superposez la feuille à une planche "
             "vierge devant une fenêtre et vérifiez l'alignement avant d'imprimer pour vrai.")
    pages = fields.Integer(string="Feuilles", compute="_compute_apercu")
    cote_mm = fields.Float(string="Côté du code, marge blanche comprise (mm)", compute="_compute_apercu", digits=(6, 1))
    avertissement = fields.Char(compute="_compute_apercu")

    @api.depends("tag_ids", "format_id", "decalage", "texte", "avec_logo",
                 "couleur_code", "couleur_fond")
    def _compute_apercu(self):
        for assistant in self:
            fmt = assistant.format_id
            assistant.count = len(assistant.tag_ids)
            assistant.pages = pages_necessaires(fmt.par_feuille, assistant.count,
                                                assistant.decalage) if fmt else 0
            assistant.cote_mm = fmt._cote_qr(avec_texte=assistant.texte != "aucun") if fmt else 0.0
            assistant.avertissement = assistant._probleme()[0]

    def _probleme(self):
        """(phrase, bloquant) : ce qui empêche ou nuance l'impression, ou (False, False)."""
        self.ensure_one()
        if not self.format_id:
            return False, False
        if self.decalage < 0 or self.decalage >= self.format_id.par_feuille:
            return _("Les places déjà utilisées vont de 0 à %s.",
                     self.format_id.par_feuille - 1), True
        try:
            verifier_couleurs(self.couleur_code or "", self.couleur_fond or "")
        except CouleurRefusee as refus:
            raison = str(refus)
            if raison == "inverse":
                return _("Le code doit être plus foncé que son fond : un code inversé n'est pas "
                         "lu par la plupart des téléphones."), True
            if raison.startswith("contraste:"):
                return _("Contraste insuffisant (%s:1, il en faut 4).", raison.split(":")[1]), True
            return _("Couleur non reconnue : écrivez-la en hexadécimal, comme #1A2B3C."), True
        if self.avec_logo and self.cote_mm < QR_MIN_LOGO_MM:
            return _("Code de %(c).1f mm : trop petit pour porter un logo (il faut %(m)s mm). "
                     "Retirez le logo ou le texte, ou prenez un format plus grand.",
                     c=self.cote_mm, m=int(QR_MIN_LOGO_MM)), True
        if self.cote_mm < QR_MIN_MM:
            # Un petit code s'imprime quand même : la personne a été prévenue.
            return _("Code de %(c).1f mm : il se lira seulement de près (20 mm conseillés).",
                     c=self.cote_mm), False
        return False, False

    def _logo_png(self):
        """Le logo en PNG, ou lève avec la raison. Le SVG est nommé : c'est le cas courant."""
        from PIL import Image, UnidentifiedImageError

        brut = self.with_context(bin_size=False).logo \
            or self.env.company.with_context(bin_size=False).logo
        if not brut:
            raise UserError(_("Aucun logo : téléversez-en un, ou décochez « Logo au centre »."))
        donnees = base64.b64decode(brut)
        if donnees.lstrip()[:5] in (b"<?xml", b"<svg ") or b"<svg" in donnees[:400]:
            raise UserError(_("Le logo est un SVG, que le tracé des étiquettes ne lit pas. "
                              "Téléversez une version PNG dans ce formulaire."))
        try:
            image = Image.open(io.BytesIO(donnees))
            image.load()
        except (UnidentifiedImageError, OSError):
            raise UserError(_("Le logo n'est pas une image lisible. Téléversez un PNG ou un JPEG."))
        sortie = io.BytesIO()
        image.convert("RGBA").save(sortie, format="PNG")
        return sortie.getvalue()

    def _texte_de(self, tag):
        reference = tag.qr_reference or tag.code
        if self.texte == "reference":
            return reference
        if self.texte == "libelle":
            return tag.name
        if self.texte == "les_deux":
            return reference if tag.name == reference else "%s · %s" % (reference, tag.name)
        return ""

    def _pdf(self):
        self.ensure_one()
        etiquettes = self.tag_ids.sudo()
        if not etiquettes:
            raise UserError(_("Aucune étiquette à imprimer."))
        if not self.tag_ids._peut_associer():
            raise UserError(_("L'impression des étiquettes est réservée à qui peut les associer."))
        signees = etiquettes.filtered("sdm_enabled")
        if signees:
            raise UserError(_("Une pastille signée n'a pas d'adresse fixe : elle ne s'imprime pas "
                              "en code QR (%s).", ", ".join(signees.mapped("name")[:5])))
        phrase, bloquant = self._probleme()
        if bloquant:
            raise UserError(phrase)
        lignes = [{"url": tag.url, "texte": self._texte_de(tag)}
                  for tag in etiquettes.sorted(lambda t: (t.qr_prefixe or "", t.qr_numero, t.name))]
        return planche(self.format_id, lignes, decalage=self.decalage,
                       code=self.couleur_code, fond=self.couleur_fond,
                       logo_png=self._logo_png() if self.avec_logo else None,
                       contours=self.contours)

    def action_imprimer(self):
        self.ensure_one()
        # Contrôle ici aussi : une erreur dans la fenêtre vaut mieux qu'un onglet
        # qui s'ouvre sur une page d'erreur.
        self._pdf()
        return {
            "type": "ir.actions.act_url",
            "url": "/bf_qr/planche/%s" % self.id,
            "target": "new",
        }
