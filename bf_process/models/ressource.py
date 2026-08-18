# -*- coding: utf-8 -*-
"""Ce qu'il faut avoir sous la main pour exécuter une étape.

Le fil de discussion d'un nœud porte déjà des pièces jointes, et c'est très
bien pour une photo d'atelier ou une capture posée entre deux séances. Ce
n'est pas ce dont parle un contremaître : lui veut, accrochée à la case,
*la* procédure, *la* fiche signalétique du produit, *la* vidéo qui montre le
geste. Une pièce parmi vingt messages n'est pas une ressource, c'est un
souvenir de conversation.

Une ressource est donc typée, ordonnée, et pointe vers exactement une cible :
un fichier, une adresse, ou une politique déjà versionnée dans la base de
connaissances. C'est ce qui permet ensuite d'imprimer la carte avec un code QR
sur les seules cases qui ont quelque chose à ouvrir.

Le gel de la version validée s'applique : une carte citée ne voit pas ses
ressources changer sous elle.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

GENRES = [
    ("procedure", "Procédure"),
    ("fiche", "Fiche signalétique"),
    ("formation", "Formation"),
    ("video", "Vidéo"),
    ("gabarit", "Gabarit"),
    ("reference", "Référence"),
]

# Ce qui doit survivre à l'impression : une case qui porte une consigne de
# sécurité ou un geste à reproduire mérite son code QR même en noir et blanc.
GENRES_CRITIQUES = ("fiche", "formation", "video")


class BfProcessNodeResource(models.Model):
    """Une ressource accrochée à une étape."""

    _name = "bf.process.node.resource"
    _description = "Ressource d'une étape"
    _order = "node_id, sequence, id"

    node_id = fields.Many2one(
        "bf.process.node", string="Étape", required=True,
        ondelete="cascade", index=True)
    # stocké : le gel se lit sur le processus, et une ressource doit pouvoir
    # se chercher par processus sans traverser deux jointures
    process_id = fields.Many2one(
        "bf.process", string="Processus", related="node_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    name = fields.Char(
        string="Libellé", required=True,
        help="Ce que la personne cherche, pas le nom du fichier.")
    kind = fields.Selection(
        GENRES, string="Genre", required=True, default="procedure")
    note = fields.Char(
        string="Précision",
        help="Quand s'en servir, ou ce qu'il faut en retenir.")

    attachment_id = fields.Many2one(
        "ir.attachment", string="Fichier", ondelete="restrict")
    url = fields.Char(string="Adresse")
    document_id = fields.Many2one(
        "project.document", string="Politique ou procédure",
        ondelete="restrict",
        help="La pièce versionnée de la base de connaissances. C'est elle qui"
             " porte les versions, les approbations et la diffusion : la"
             " cartographie s'y raccorde plutôt que d'en garder une copie.")

    cible = fields.Char(
        string="Cible", compute="_compute_cible",
        help="L'adresse que le code QR ouvre.")
    critique = fields.Boolean(
        string="Critique", compute="_compute_critique", store=True,
        help="Sécurité ou geste à reproduire : passe en tête à l'impression.")

    _sql_constraints = [
        ("libelle_non_vide", "check (length(trim(name)) > 0)",
         "Une ressource sans libellé ne s'ouvre pas, elle s'ignore."),
    ]

    @api.depends("kind")
    def _compute_critique(self):
        for rec in self:
            rec.critique = rec.kind in GENRES_CRITIQUES

    @api.depends("attachment_id", "url", "document_id")
    def _compute_cible(self):
        for rec in self:
            if rec.attachment_id:
                rec.cible = "/web/content/%s?download=false" % rec.attachment_id.id
            elif rec.document_id:
                rec.cible = "/web#id=%s&model=project.document&view_type=form" \
                    % rec.document_id.id
            else:
                rec.cible = rec.url or ""

    # `node_id` est dans la contrainte pour qu'elle s'exécute à la CRÉATION :
    # Odoo ne valide que les champs présents dans les valeurs, et une ressource
    # créée sans aucune cible n'en mentionne aucun des trois. Sans ce quatrième
    # nom, la ressource muette passait, et ne se faisait refuser qu'à la
    # première modification.
    @api.constrains("attachment_id", "url", "document_id", "node_id")
    def _check_cible_unique(self):
        """Exactement une cible.

        Zéro cible fait une ressource qui ne mène nulle part — le pire des cas
        en atelier, parce que le code QR est imprimé et que le geste de le
        scanner ne rend rien. Deux cibles font une ambiguïté qu'aucun affichage
        ne peut résoudre : le QR n'en ouvre qu'une.
        """
        for rec in self:
            cibles = [bool(rec.attachment_id), bool(rec.url),
                      bool(rec.document_id)]
            if sum(cibles) == 0:
                raise ValidationError(_(
                    "« %s » ne mène nulle part : donnez-lui un fichier, une"
                    " adresse, ou une politique de la base de connaissances."
                ) % rec.name)
            if sum(cibles) > 1:
                raise ValidationError(_(
                    "« %s » a plusieurs cibles. Un code QR n'en ouvre qu'une :"
                    " choisissez laquelle, et faites-en deux ressources si les"
                    " deux comptent."
                ) % rec.name)

    @api.constrains("node_id")
    def _check_porteur(self):
        """Une annotation n'exécute rien, donc elle ne porte pas de consigne."""
        for rec in self:
            if rec.node_id.kind == "note":
                raise ValidationError(_(
                    "Une annotation ne porte pas de ressource : accrochez-la à"
                    " l'étape que l'annotation commente."))

    # --- gel ------------------------------------------------------------------
    # Le mixin `bf.process.gel` résout le processus depuis `diagram_id` ou
    # `process_id` **dans les valeurs de création**. Une ressource se crée avec
    # `node_id` seul, donc le mixin ne verrait rien : la garde est posée ici,
    # sur le processus du nœud, et délègue la phrase de refus au mixin pour
    # qu'il n'y en ait qu'une seule dans le module.
    def _processus_concernes(self, vals_list=None):
        if vals_list is None:
            return self.mapped("process_id")
        noeuds = self.env["bf.process.node"].browse(
            [v["node_id"] for v in vals_list if v.get("node_id")])
        return noeuds.mapped("process_id")

    def _garde(self, processus):
        self.env["bf.process.gel"]._garde_gel(processus=processus)

    @api.model_create_multi
    def create(self, vals_list):
        self._garde(self._processus_concernes(vals_list))
        return super().create(vals_list)

    def write(self, vals):
        self._garde(self._processus_concernes())
        return super().write(vals)

    def unlink(self):
        self._garde(self._processus_concernes())
        return super().unlink()

    # --- ce que la page d'atelier a le droit d'ouvrir -------------------------
    # La règle vit ici, pas dans le contrôleur : elle se prouve alors sans
    # serveur. Un rendu PDF traversé par une requête HTTP ne s'éprouve pas dans
    # un `HttpCase` — le serveur de test partage un seul curseur, et l'appel que
    # wkhtmltopdf refait au serveur pour aller chercher la feuille de style
    # bloque sur celui que la requête en cours tient déjà. En production, les
    # ouvriers sont plusieurs et la question ne se pose pas.

    def _piece_publique(self):
        """Le fichier qu'on peut servir tel quel, ou rien.

        Celui de la ressource d'abord, puis celui de la version **publiée** du
        document. Une version en brouillon ne compte pas.
        """
        self.ensure_one()
        return self.attachment_id \
            or self.document_id.latest_version_id.attachment_id

    def _corps_a_rendre(self):
        """Le document dont il faut rendre le corps, ou un jeu vide.

        Dans une base de connaissances tenue dans Odoo, le contenu d'une
        procédure vit dans ses sections, pas en pièce jointe : sur les 191
        versions publiées de la nôtre, deux portaient un fichier. S'en tenir au
        fichier revenait donc à n'ouvrir à peu près rien, et la cible
        « politique ou procédure » ne servait qu'à l'écran, jamais au mur.

        Deux garde-fous : une version **publiée** (un brouillon n'a pas à se
        retrouver au-dessus d'un poste de travail) et un corps qui vit **ici**
        (un document qui pointe vers un fichier externe n'a rien à rendre).
        """
        self.ensure_one()
        vide = self.env["project.document"]
        doc = self.document_id
        if not doc or self._piece_publique():
            return vide
        if doc.body_source != "internal" or not doc.latest_version_id:
            return vide
        return doc if doc._report_sections() else vide

    def action_ouvrir(self):
        """Ouvre la ressource là où elle vit."""
        self.ensure_one()
        if self.document_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "project.document",
                "res_id": self.document_id.id,
                "views": [[False, "form"]],
            }
        return {"type": "ir.actions.act_url", "url": self.cible,
                "target": "new"}


class BfProcessNodeAvecRessources(models.Model):
    _inherit = "bf.process.node"

    resource_ids = fields.One2many(
        "bf.process.node.resource", "node_id", string="Ressources")
    # libellés distincts de `resource_ids` : deux champs du même modèle qui
    # portent le même libellé font râler Odoo à chaque upgrade.
    resource_count = fields.Integer(
        string="Nombre de ressources", compute="_compute_resource_count")
    resource_critique_count = fields.Integer(
        string="Dont critiques", compute="_compute_resource_count")

    @api.depends("resource_ids", "resource_ids.critique")
    def _compute_resource_count(self):
        for rec in self:
            rec.resource_count = len(rec.resource_ids)
            rec.resource_critique_count = len(
                rec.resource_ids.filtered("critique"))
