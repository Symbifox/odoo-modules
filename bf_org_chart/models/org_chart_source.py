# -*- coding: utf-8 -*-
"""Le contrat qu'un modèle signe pour savoir se dessiner.

Un satellite hérite de ce modèle abstrait, déclare les organigrammes qu'il
sait produire, et rend une `Carte`. Il ne calcule aucune coordonnée et ne
connaît ni le SVG ni le PDF : c'est le socle qui dispose et qui trace.

⚠️ Plusieurs satellites peuvent équiper le MÊME modèle (les personnes et la
détention équipent tous les deux `res.partner`). C'est pour ça que
`_org_chart_genres` appelle `super()` et ajoute au lieu de remplacer : deux
satellites qui s'écraseraient l'un l'autre feraient disparaître un bouton sans
rien dire.
"""
from odoo import _, models
from odoo.exceptions import UserError

from ..moteur import disposition, modele, pdf, svg


class OrgChartSource(models.AbstractModel):
    _name = "bf.org.chart.source"
    _description = "Source d'organigramme"

    #: Nombre de boîtes au-delà duquel on refuse de dessiner. Une carte de
    #: mille nœuds n'est pas un organigramme, c'est un mur, et elle prendrait
    #: le processus Odoo pour elle seule.
    PLAFOND_BOITES = 400

    def _org_chart_genres(self):
        """Rend la liste des genres disponibles pour CE modèle.

        Chaque entrée : ``{"code": ..., "libelle": ..., "sequence": 10}``.
        """
        return []

    def _org_chart_carte(self, code):
        """Rend une `modele.Carte`. À surcharger par chaque satellite."""
        raise UserError(_("Ce genre d'organigramme n'existe pas ici : %s.", code))

    # --- ce que le socle fait, et que les satellites n'ont pas à refaire ----
    def _org_chart_plan(self, code):
        """Rend le plan, ou un message clair. Jamais une page 500.

        ⚠️ Le plafond ci-dessous est un FILET : il compte des boîtes déjà
        construites, donc tout le travail est déjà payé quand il se déclenche.
        C'est au satellite de borner sa recherche en amont, avec
        `PLAFOND_BOITES` comme limite; voir `_org_chart_carte`.
        """
        self.ensure_one()
        carte = self._org_chart_carte(code)
        if not isinstance(carte, modele.Carte):
            raise UserError(_("La source n'a pas rendu une carte."))
        if len(carte.boites) > self.PLAFOND_BOITES:
            raise UserError(_(
                "L'organigramme compte %(n)s boîtes, au-delà des %(max)s que "
                "le dessin sait tenir. Réduire la portée avant de recommencer.",
                n=len(carte.boites), max=self.PLAFOND_BOITES))
        try:
            return disposition.disposer(carte)
        except modele.CarteTropGrande as trop:
            raise UserError(_(
                "Cet organigramme ne peut pas être dessiné : %s", str(trop)))

    def _org_chart_svg(self, code):
        return svg.rendre(self._org_chart_plan(code))

    def _org_chart_pdf(self, code):
        plan = self._org_chart_plan(code)
        # Les boîtes portent un lien RELATIF, qui va bien à l'écran et ne mène
        # nulle part dans un PDF : on lui donne ici la base de l'instance.
        return pdf.rendre(plan, titre_document=plan.titre,
                          base_url=self.get_base_url())

    def _org_chart_url(self, code, format_=""):
        self.ensure_one()
        base = "/bf/organigramme/%s/%s/%s" % (self._name, self.id, code)
        return base + ("/pdf" if format_ == "pdf" else "")

    def action_org_chart(self, code=None):
        """Ouvre la page de l'organigramme dans un onglet."""
        self.ensure_one()
        genres = self._org_chart_genres()
        if not code:
            if not genres:
                raise UserError(_("Aucun organigramme n'est disponible ici."))
            code = sorted(genres, key=lambda g: g.get("sequence", 10))[0]["code"]
        return {
            "type": "ir.actions.act_url",
            "url": self._org_chart_url(code),
            "target": "new",
        }
