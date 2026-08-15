# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .structure import ACTIVITES


class BfProcess(models.Model):
    """Une cartographie de processus, versionnée."""

    _name = "bf.process"
    _description = "Cartographie de processus"
    _inherit = ["mail.thread"]
    _order = "name, version"

    name = fields.Char(string="Processus", required=True, tracking=True)
    code = fields.Char(string="Code", help="Préfixe des identifiants BPMN exportés.")
    version = fields.Char(string="Version", default="1.0", required=True, tracking=True)
    state = fields.Selection(
        [("brouillon", "Brouillon"), ("valide", "Validée"), ("obsolete", "Obsolète")],
        string="État", default="brouillon", required=True, tracking=True)
    pool_name = fields.Char(
        string="Pool principal", required=True,
        help="L'organisation qui exécute le processus. Les exécutants sont des"
             " couloirs, les participants externes des pools boîte noire.")
    partner_id = fields.Many2one("res.partner", string="Client", tracking=True)
    project_id = fields.Many2one("project.project", string="Projet", tracking=True)
    source = fields.Text(
        string="Source",
        help="D'où vient la carte, et ce qui n'a pas été fait. Une carte AS-IS"
             " sans sa source n'est pas un livrable.")
    diagram_ids = fields.One2many("bf.process.diagram", "process_id", string="Niveaux")
    diagram_count = fields.Integer(compute="_compute_counts")
    node_count = fields.Integer(compute="_compute_counts")
    active = fields.Boolean(default=True)

    # --- lignée des versions -------------------------------------------------
    version_precedente_id = fields.Many2one(
        "bf.process", string="Version précédente", ondelete="set null", copy=False)
    version_suivante_ids = fields.One2many(
        "bf.process", "version_precedente_id", string="Versions suivantes")

    # --- registre de validation ---------------------------------------------
    activite_count = fields.Integer(compute="_compute_validation")
    validee_count = fields.Integer(compute="_compute_validation")
    taux_validation = fields.Float(
        string="Validation", compute="_compute_validation",
        help="Part des activités validées par le propriétaire ET par un exécutant.")
    conteste_count = fields.Integer(compute="_compute_validation")

    _sql_constraints = [
        ("name_version_uniq", "unique (name, version)",
         "Cette version de ce processus existe déjà."),
    ]

    @api.depends("diagram_ids", "diagram_ids.node_ids")
    def _compute_counts(self):
        for rec in self:
            rec.diagram_count = len(rec.diagram_ids)
            rec.node_count = sum(len(d.node_ids) for d in rec.diagram_ids)

    @api.depends("diagram_ids.node_ids.validation")
    def _compute_validation(self):
        for rec in self:
            activites = rec.diagram_ids.mapped("node_ids").filtered(
                lambda n: n.kind in ACTIVITES)
            validees = activites.filtered(lambda n: n.validation == "validee")
            rec.activite_count = len(activites)
            rec.validee_count = len(validees)
            rec.conteste_count = len(activites.filtered(
                lambda n: n.validation == "conteste"))
            rec.taux_validation = (len(validees) / len(activites) * 100
                                   if activites else 0.0)

    def name_get(self):
        return [(r.id, f"{r.name} v{r.version}") for r in self]

    # --- gel ------------------------------------------------------------------
    MODIFIABLE_APRES_VALIDATION = {
        "state", "active", "partner_id", "project_id", "source",
        "version_precedente_id", "message_follower_ids", "message_ids",
        "activity_ids",
    }

    def write(self, vals):
        if not self.env.context.get("bf_process_degel"):
            touche = set(vals) - self.MODIFIABLE_APRES_VALIDATION
            geles = self.filtered(lambda p: p.state == "valide")
            if touche and geles:
                raise UserError(_(
                    "« %s » est une version validée : son contenu ne se modifie "
                    "plus. Créez la version suivante, ou rouvrez celle-ci."
                ) % ", ".join(f"{p.name} v{p.version}" for p in geles))
        return super().write(vals)

    def unlink(self):
        geles = self.filtered(lambda p: p.state == "valide")
        if geles and not self.env.context.get("bf_process_degel"):
            raise UserError(_(
                "Une version validée ne se supprime pas. Marquez-la obsolète."))
        return super(BfProcess, self.with_context(bf_process_degel=True)).unlink()

    def action_valider(self):
        """Fige la version. C'est ce qui rend la carte cit​able."""
        for rec in self:
            if not rec.diagram_ids:
                raise UserError(_("Une cartographie sans niveau ne se valide pas."))
            rec.with_context(bf_process_degel=True).state = "valide"
            rec.message_post(body=_(
                "Version <b>%s</b> validée et gelée — %d activité(s) sur %d "
                "validées au registre.") % (rec.version, rec.validee_count,
                                            rec.activite_count))
        return True

    def action_rouvrir(self):
        """Dégèle, et le dit. Rouvrir en silence serait pire que ne pas geler."""
        for rec in self:
            rec.with_context(bf_process_degel=True).state = "brouillon"
            rec.message_post(body=_(
                "Version <b>%s</b> rouverte : elle redevient modifiable.")
                % rec.version)
        return True

    def _version_suivante(self):
        """1.0 → 1.1 ; 2 → 3 ; sinon on suffixe.

        Incrémente au-delà d'un numéro déjà pris : une version supprimée puis
        recréée, ou un essai laissé derrière, ne doit pas coller sur un numéro
        vivant — la contrainte d'unicité le refuserait, en aval, sans dire
        pourquoi le numéro choisi ne convenait pas.
        """
        self.ensure_one()
        prises = set(self.env["bf.process"].search(
            [("name", "=", self.name)]).mapped("version"))
        morceaux = (self.version or "1.0").split(".")
        if not morceaux[-1].isdigit():
            candidate, n = f"{self.version}-bis", 2
            while candidate in prises:
                candidate, n = f"{self.version}-bis{n}", n + 1
            return candidate
        while True:
            morceaux[-1] = str(int(morceaux[-1]) + 1)
            candidate = ".".join(morceaux)
            if candidate not in prises:
                return candidate

    def action_nouvelle_version(self):
        """Repart de cette version, sans toucher à celle-ci.

        Le contenu passe par la forme d'échange puis par le chargeur : mêmes
        codes, mêmes identifiants BPMN, donc les deux versions se comparent
        ligne à ligne. La validation, elle, ne se recopie pas — une nouvelle
        version se revalide.
        """
        self.ensure_one()
        nouvelle = self.copy({
            "name": self.name,
            "version": self._version_suivante(),
            "state": "brouillon",
            "version_precedente_id": self.id,
            "diagram_ids": [],
        })
        nouvelle._charger_niveaux(self.to_dicts())
        nouvelle.message_post(body=_(
            "Reprise de la version <b>%s</b> : %d niveaux, %d nœuds. Le registre "
            "de validation repart à zéro.") % (self.version, len(nouvelle.diagram_ids),
                                               nouvelle.node_count))
        return {
            "type": "ir.actions.act_window",
            "name": _("Nouvelle version"),
            "res_model": "bf.process",
            "res_id": nouvelle.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_comparer(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Comparer deux versions"),
            "res_model": "bf.process.compare.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_source_id": self.id,
                        "default_cible_id": self.version_precedente_id.id or False},
        }

    def action_ouvrir_niveaux(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Niveaux"),
            "res_model": "bf.process.diagram",
            "view_mode": "list,form",
            "domain": [("process_id", "=", self.id)],
            "context": {"default_process_id": self.id},
        }


class BfProcessDiagram(models.Model):
    """Un niveau : une page de la carte, autonome, avec son propre plan.

    Le niveau 1 donne la vue d'ensemble ; chaque sous-processus replié y ouvre
    son propre niveau. La règle « les portes du parent portent les états de fin
    de l'enfant » s'encode par `parent_node_ids` — au pluriel, parce
    qu'un sous-processus commun est appelé de plusieurs endroits.
    """

    _name = "bf.process.diagram"
    _inherit = ["bf.process.gel"]
    _description = "Niveau de cartographie"
    _order = "process_id, sequence, id"

    process_id = fields.Many2one(
        "bf.process", string="Processus", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    title = fields.Char(string="Titre", required=True)
    level = fields.Char(
        string="Niveau", help="Libellé affiché, par exemple « Niveau 2 ».")
    code = fields.Char(
        string="Code", required=True,
        help="Identifiant court du niveau, unique dans le processus.")
    bpmn_id = fields.Char(
        string="Identifiant BPMN", required=True, copy=False,
        help="Stable, jamais régénéré : c'est lui qui permet à un réimport de"
             " fusionner au lieu de dupliquer.")
    parent_node_ids = fields.One2many(
        "bf.process.node", "child_diagram_id", string="Ouvert depuis",
        help="Les sous-processus qui ouvrent cette page. Il peut y en avoir"
             " plusieurs : un sous-processus commun est appelé de plusieurs"
             " endroits.")
    parent_count = fields.Integer(compute="_compute_parent_count")
    pool_name = fields.Char(
        string="Pool principal",
        help="Vide : celui du processus s'applique.")
    flat = fields.Boolean(
        string="Carte dépliée",
        help="Page unique rassemblant tous les niveaux.")

    # --- géométrie ----------------------------------------------------------
    col_w = fields.Float(string="Largeur de colonne", default=168.0)
    row_h = fields.Float(string="Hauteur de rangée", default=100.0)
    lane_pad = fields.Float(string="Marge de couloir", default=50.0)
    ext_header = fields.Float(
        string="Hauteur des pools externes", default=62.0,
        help="Mesurée au rendu, puis conservée : le serveur n'a pas de moteur"
             " typographique pour la recalculer.")

    lane_ids = fields.One2many("bf.process.lane", "diagram_id", string="Couloirs")
    pool_ids = fields.One2many("bf.process.pool", "diagram_id", string="Pools externes")
    node_ids = fields.One2many("bf.process.node", "diagram_id", string="Nœuds")
    flow_ids = fields.One2many("bf.process.flow", "diagram_id", string="Flux")
    message_ids = fields.One2many(
        "bf.process.message", "diagram_id", string="Flux de message")
    node_count = fields.Integer(compute="_compute_node_count")

    _sql_constraints = [
        ("code_uniq", "unique (process_id, code)",
         "Ce code de niveau est déjà pris dans ce processus."),
        ("bpmn_id_uniq", "unique (process_id, bpmn_id)",
         "Cet identifiant BPMN est déjà pris dans ce processus."),
    ]

    @api.depends("node_ids")
    def _compute_node_count(self):
        for rec in self:
            rec.node_count = len(rec.node_ids)

    @api.depends("parent_node_ids")
    def _compute_parent_count(self):
        for rec in self:
            rec.parent_count = len(rec.parent_node_ids)

    def name_get(self):
        return [(r.id, " — ".join(filter(None, (r.level, r.title)))) for r in self]

    def to_dict(self):
        """Rend le niveau sous la forme que les générateurs attendent."""
        self.ensure_one()
        d = {
            "title": self.title,
            "pool": self.pool_name or self.process_id.pool_name,
            "col_w": self.col_w,
            "row_h": self.row_h,
            "lane_pad": self.lane_pad,
            "ext_header": self.ext_header,
            "lanes": [ln.to_dict() for ln in self.lane_ids],
            "ext": [p.to_dict() for p in self.pool_ids],
            "nodes": [n.to_dict() for n in self.node_ids],
            "flows": [f.to_dict() for f in self.flow_ids],
            "msgs": [m.to_dict() for m in self.message_ids],
        }
        if self.level:
            d["level"] = self.level
        if self.flat:
            d["flat"] = True
        return d
