from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SlideSlide(models.Model):
    _inherit = "slide.slide"

    #: ⚠️ `selection_add` avec son `ondelete` : sans lui, désinstaller ce module
    #: laisserait des contenus dans un type que plus rien ne sait lire, et Odoo
    #: refuse la désinstallation.
    slide_category = fields.Selection(
        selection_add=[("scorm", "Paquet SCORM")],
        ondelete={"scorm": "set default"})

    #: 🔴 Le MÊME compteur que sur le canal, et c'est celui-ci qu'on oublie.
    #: `_compute_slides_statistics` est défini sur CE modèle : une section de
    #: cours est une `slide.slide` dont `is_category` est vrai, et elle compte
    #: ses contenus par catégorie. Sans ce champ, `record.update(...)` lève
    #: `KeyError: 'nbr_scorm'` — et le défaut ne s'est montré qu'en installant
    #: ce module À CÔTÉ des autres : seul un essai d'un AUTRE module, qui crée
    #: un cours avec des sections, le déclenchait.
    nbr_scorm = fields.Integer(
        "Number of SCORM packages", compute="_compute_slides_statistics", store=True)

    scorm_package_id = fields.Many2one(
        "bf.scorm.package", string="Paquet SCORM", copy=False)
    scorm_version = fields.Selection(
        related="scorm_package_id.version", string="Version SCORM")
    scorm_uses_sequencing = fields.Boolean(
        related="scorm_package_id.uses_sequencing")

    @api.constrains("slide_category", "scorm_package_id")
    def _check_scorm_package(self):
        """Un contenu SCORM sans paquet est un contenu qui ne joue rien.

        Le laisser passer donnerait un cours dont une leçon s'ouvre sur du vide,
        et personne ne saurait si c'est le contenu ou le lecteur qui a lâché.
        """
        for diapo in self:
            if diapo.slide_category == "scorm" and not diapo.scorm_package_id:
                raise ValidationError(
                    _("Un contenu SCORM doit porter un paquet."))

    def _get_completion_time(self):
        """La durée d'un SCORM n'est pas devinable : ne rien inventer.

        Le natif estime la durée d'un document à partir de son nombre de pages.
        Un paquet SCORM n'a pas de page, et une durée inventée entrerait au
        registre comme des heures de formation. On laisse ce que la personne a
        saisi, et à défaut zéro — que le registre signale comme incomplet plutôt
        que de le compter.
        """
        self.ensure_one()
        if self.slide_category == "scorm":
            return self.completion_time or 0.0
        return super()._get_completion_time()
