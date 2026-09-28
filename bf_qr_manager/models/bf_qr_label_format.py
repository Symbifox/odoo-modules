"""Un format de planche d'étiquettes : la page, la grille, et rien d'autre.

🔴 **Des dimensions, jamais des fichiers de gabarit.** Les formats semés
reprennent les FAITS de la base de gabarits de gLabels (licence MIT, « no
copyright is claimed on the facts »). Aucun fichier de gabarit Avery n'est
copié : leur licence interdit de les redistribuer. Le nom « compatible Avery
5160 » est un usage nominatif, en texte seul : aucune affiliation.

Tout est en millimètres. Une grille se décrit par le coin de la première
étiquette (``marge_gauche``, ``marge_haut``) et par le PAS entre deux étiquettes,
qui inclut l'espace qui les sépare : c'est ce que publient les fabricants et
ce que les imprimantes respectent.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

PAPIERS = {
    "letter": (215.9, 279.4),
    "a4": (210.0, 297.0),
}

# En deçà, un téléphone tenu à 25 cm ne lit plus un code de façon fiable ; au
# niveau de correction H, qu'impose un logo, il faut encore plus de place.
QR_MIN_MM = 20.0
QR_MIN_LOGO_MM = 25.0
# Quand l'étiquette porte un texte sous le code.
HAUTEUR_TEXTE_MM = 4.0


class BfQrLabelFormat(models.Model):
    _name = "bf.qr.label.format"
    _description = "Format de planche d'étiquettes"
    _order = "sequence, papier, name"

    name = fields.Char(string="Nom", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    reference = fields.Char(
        string="Référence compatible",
        help="La référence du commerce que ce format reproduit, en texte seul : "
             "« Avery 5160 ». Aucune affiliation n'est suggérée.")
    papier = fields.Selection(
        [("letter", "Lettre (8,5 × 11 po)"), ("a4", "A4 (210 × 297 mm)"),
         ("custom", "Autre")],
        string="Feuille", required=True, default="letter")
    page_largeur = fields.Float(string="Largeur de feuille (mm)", digits=(6, 3))
    page_hauteur = fields.Float(string="Hauteur de feuille (mm)", digits=(6, 3))
    forme = fields.Selection([("rect", "Rectangle"), ("rond", "Rond")],
                             string="Forme", required=True, default="rect")
    largeur = fields.Float(string="Largeur (mm)", required=True, digits=(6, 3),
                           help="Pour une étiquette ronde : son diamètre.")
    hauteur = fields.Float(string="Hauteur (mm)", required=True, digits=(6, 3))
    colonnes = fields.Integer(required=True, default=1)
    lignes = fields.Integer(required=True, default=1)
    marge_gauche = fields.Float(string="Bord gauche de la 1re (mm)", digits=(6, 3))
    marge_haut = fields.Float(string="Bord haut de la 1re (mm)", digits=(6, 3))
    pas_x = fields.Float(string="Pas horizontal (mm)", digits=(6, 3),
                         help="D'un bord gauche d'étiquette au suivant.")
    pas_y = fields.Float(string="Pas vertical (mm)", digits=(6, 3),
                         help="D'un bord haut d'étiquette au suivant.")
    marge_interne = fields.Float(
        string="Marge intérieure (mm)", digits=(6, 3), default=1.5,
        help="Ce qu'on laisse vide au bord de chaque étiquette : une coupe "
             "d'imprimante n'est jamais parfaite.")
    source = fields.Char(help="D'où viennent ces dimensions.")
    par_feuille = fields.Integer(string="Par feuille", compute="_compute_par_feuille")
    qr_max_mm = fields.Float(string="Côté du QR, marge blanche comprise (mm)", compute="_compute_qr_max",
                             digits=(6, 1),
                             help="Le plus grand code qui tient sur l'étiquette, sans texte.")

    @api.depends("colonnes", "lignes")
    def _compute_par_feuille(self):
        for fmt in self:
            fmt.par_feuille = max(fmt.colonnes, 0) * max(fmt.lignes, 0)

    @api.depends("largeur", "hauteur", "marge_interne", "forme")
    def _compute_qr_max(self):
        for fmt in self:
            fmt.qr_max_mm = fmt._cote_qr(avec_texte=False)

    def _feuille(self):
        """(largeur, hauteur) de la feuille, en mm."""
        self.ensure_one()
        if self.papier in PAPIERS:
            return PAPIERS[self.papier]
        return self.page_largeur, self.page_hauteur

    def _cote_qr(self, avec_texte=False):
        """Le côté du plus grand code qui tient dans une étiquette, en mm.

        Un code carré dans une étiquette ronde tient dans le carré inscrit :
        diamètre ÷ √2. Le texte, s'il y en a, se place à côté du code quand
        l'étiquette est large, sous le code sinon ; ``_disposition`` décide.
        """
        self.ensure_one()
        m = self.marge_interne or 0.0
        if self.forme == "rond":
            cote = self.largeur / 2 ** 0.5 - 2 * m
            if avec_texte:
                cote -= HAUTEUR_TEXTE_MM
            return max(cote, 0.0)
        largeur, hauteur = self.largeur - 2 * m, self.hauteur - 2 * m
        if avec_texte:
            if self._texte_a_cote():
                return max(min(hauteur, largeur * 0.5), 0.0)
            hauteur -= HAUTEUR_TEXTE_MM
        return max(min(largeur, hauteur), 0.0)

    def _texte_a_cote(self):
        """Une étiquette nettement plus large que haute porte son texte à droite."""
        self.ensure_one()
        return self.forme == "rect" and self.largeur >= 1.6 * self.hauteur

    def _position(self, rang):
        """Coin bas-gauche (x, y) de l'étiquette ``rang`` sur sa page, en mm, repère PDF."""
        self.ensure_one()
        par_page = self.par_feuille
        rang = rang % par_page
        ligne, colonne = divmod(rang, self.colonnes)
        _l, hauteur_page = self._feuille()
        x = self.marge_gauche + colonne * self.pas_x
        haut = self.marge_haut + ligne * self.pas_y
        return x, hauteur_page - haut - self.hauteur

    @api.constrains("largeur", "hauteur", "colonnes", "lignes", "marge_gauche",
                    "marge_haut", "pas_x", "pas_y", "papier", "page_largeur",
                    "page_hauteur", "forme", "marge_interne")
    def _check_geometrie(self):
        for fmt in self:
            largeur_page, hauteur_page = fmt._feuille()
            if not largeur_page or not hauteur_page:
                raise ValidationError(_("Donnez les dimensions de la feuille."))
            if fmt.colonnes < 1 or fmt.lignes < 1:
                raise ValidationError(_("Il faut au moins une colonne et une ligne."))
            if fmt.largeur <= 0 or fmt.hauteur <= 0:
                raise ValidationError(_("Une étiquette a une largeur et une hauteur."))
            if fmt.colonnes > 1 and fmt.pas_x < fmt.largeur - 0.01:
                raise ValidationError(_("Le pas horizontal est plus petit que l'étiquette : "
                                        "elles se chevaucheraient."))
            if fmt.lignes > 1 and fmt.pas_y < fmt.hauteur - 0.01:
                raise ValidationError(_("Le pas vertical est plus petit que l'étiquette : "
                                        "elles se chevaucheraient."))
            droite = fmt.marge_gauche + (fmt.colonnes - 1) * fmt.pas_x + fmt.largeur
            bas = fmt.marge_haut + (fmt.lignes - 1) * fmt.pas_y + fmt.hauteur
            if fmt.marge_gauche < 0 or fmt.marge_haut < 0 \
                    or droite > largeur_page + 0.05 or bas > hauteur_page + 0.05:
                raise ValidationError(_(
                    "La grille de « %(nom)s » déborde de la feuille (%(d).1f × %(b).1f mm "
                    "pour une feuille de %(l).1f × %(h).1f mm).",
                    nom=fmt.name, d=droite, b=bas, l=largeur_page, h=hauteur_page))
            if fmt._cote_qr() <= 0:
                raise ValidationError(_("La marge intérieure ne laisse aucune place au code."))
