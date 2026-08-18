# -*- coding: utf-8 -*-
"""La page qu'un code QR ouvre, pour quelqu'un qui n'a pas de compte.

Le machiniste devant sa presse n'a pas de session Odoo, et n'en aura jamais.
Il a un téléphone et une carte punaisée au mur. Le lien doit donc porter sa
propre autorisation, et ne donner accès qu'à ce que la case montre : les
consignes de cette étape-là, rien d'autre.

On reprend le patron portail d'Odoo, celui des factures et celui de
`bf_document_portal` : `portal.mixin` pose un `access_token` tiré au sort, la
route est `auth="public"`, et c'est la comparaison du jeton qui décide. Pas de
nouvelle idée de sécurité — les nouvelles idées de sécurité sont exactement ce
qu'il ne faut pas avoir.

⚠️ Le jeton vaut pour l'étape entière : qui l'a peut lire toutes les ressources
de cette étape. C'est voulu — c'est ce qui est imprimé sur le mur de l'atelier.
Ce n'est PAS un endroit où poser une pièce confidentielle.
"""
from odoo import _, api, fields, models


class BfProcessNodePortail(models.Model):
    _name = "bf.process.node"
    _inherit = ["bf.process.node", "portal.mixin"]

    def _compute_access_url(self):
        super()._compute_access_url()
        for rec in self:
            rec.access_url = "/carte/etape/%s" % rec.id

    def action_url_atelier(self):
        """L'adresse complète que le code QR encode, jeton compris."""
        self.ensure_one()
        self._portal_ensure_token()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return "%s/carte/etape/%s?access_token=%s" % (
            base.rstrip("/"), self.id, self.access_token)

    def action_ouvrir_page_atelier(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.action_url_atelier(),
                "target": "new"}


class BfProcessAtelier(models.Model):
    _inherit = "bf.process"

    def _noeuds_avec_ressources(self):
        """Les étapes qui ont quelque chose à ouvrir, et rien d'autre.

        Un code QR sur une case qui ne mène nulle part est pire qu'aucun code
        QR : la personne se déplace, scanne, et n'obtient rien.
        """
        self.ensure_one()
        return self.mapped("diagram_ids.node_ids").filtered(
            lambda n: n.resource_ids and n.kind != "note")

    def _dicts_atelier(self):
        """La forme d'échange, enrichie de l'adresse de chaque code QR.

        Le générateur ne connaît ni Odoo ni les jetons : il reçoit une clé
        `qr` sur les nœuds concernés, et dessine si elle est là.
        """
        self.ensure_one()
        porteurs = self._noeuds_avec_ressources()
        adresses = {}
        for noeud in porteurs:
            adresses.setdefault(noeud.diagram_id.id, {})[noeud.code] = \
                noeud.action_url_atelier()
        diagrammes = []
        for niveau in self.diagram_ids:
            d = niveau.to_dict()
            par_code = adresses.get(niveau.id, {})
            for n in d["nodes"]:
                if n["id"] in par_code:
                    n["qr"] = par_code[n["id"]]
            diagrammes.append(d)
        return diagrammes

    def action_telecharger_pdf_atelier(self):
        """Le tirage d'atelier : pavé en tabloïd, codes QR sur les porteuses.

        Un artefact à part, pas une option cochée sur le PDF client. Le PDF
        remis au client est une pièce de dossier ; celui-ci est un outil de
        plancher, et les deux ne se lisent pas au même endroit ni de la même
        façon.
        """
        self.ensure_one()
        from ..generateur import pdf as gen_pdf
        if not self._noeuds_avec_ressources():
            from odoo.exceptions import UserError
            raise UserError(_(
                "Aucune étape de « %s » ne porte de ressource : le tirage"
                " d'atelier serait la même carte, en plus de pages.\n\n"
                "Accrochez d'abord une procédure, une fiche ou une vidéo aux"
                " étapes qui en demandent."
            ) % self.name)
        pied = (self.source or "").strip().splitlines()
        octets = gen_pdf.to_pdf(
            self._dicts_atelier(),
            titre="%s · v%s" % (self.name, self.version),
            sous_titre=self.partner_id.display_name or "",
            pied=pied[0] if pied else "",
            pave=gen_pdf.TABLOID)
        return self._telecharger(octets, "pdf", "application/pdf",
                                 suffixe="-atelier")
