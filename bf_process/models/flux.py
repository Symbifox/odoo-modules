# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

ROUTAGES = [
    ("auto", "Automatique"),
    ("h", "Horizontal"),
    ("v", "Vertical"),
    ("hv", "Horizontal puis vertical"),
    ("vh", "Vertical puis horizontal"),
    ("above", "Détour par le haut"),
    ("below", "Détour par le bas"),
    ("assoc", "Association vers une annotation"),
]
COTES = [("T", "Haut"), ("B", "Bas"), ("L", "Gauche"), ("R", "Droite")]


class BfProcessFlow(models.Model):
    """Un flux de séquence, ou une association vers une annotation."""

    _name = "bf.process.flow"
    _inherit = ["bf.process.gel"]
    _description = "Flux de séquence"
    _order = "diagram_id, sequence, id"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Niveau", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Processus", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    bpmn_id = fields.Char(string="Identifiant BPMN", required=True, copy=False)
    source_id = fields.Many2one(
        "bf.process.node", string="Origine", required=True, ondelete="cascade")
    target_id = fields.Many2one(
        "bf.process.node", string="Cible", required=True, ondelete="cascade")
    label = fields.Char(
        string="Porte",
        help="Le nom de la porte. Les portes d'une passerelle parallèle ou"
             " inclusive ne se nomment jamais.")
    routing = fields.Selection(ROUTAGES, string="Routage", default="auto", required=True)
    detour = fields.Float(string="Profondeur du détour")
    anchor_source = fields.Selection(COTES, string="Accroche à l'origine")
    anchor_target = fields.Selection(COTES, string="Accroche à la cible")
    label_dx = fields.Float(string="Décalage d'étiquette en X")
    label_dy = fields.Float(string="Décalage d'étiquette en Y")
    label_width = fields.Float(string="Largeur d'étiquette")

    _sql_constraints = [
        ("bpmn_id_uniq", "unique (process_id, bpmn_id)",
         "Cet identifiant BPMN est déjà pris dans ce processus."),
    ]

    @api.constrains("source_id", "target_id", "routing")
    def _check_extremites(self):
        for rec in self:
            if rec.source_id.diagram_id != rec.diagram_id or \
               rec.target_id.diagram_id != rec.diagram_id:
                raise ValidationError(
                    _("Un flux ne relie que des nœuds du même niveau."))
            if rec.source_id == rec.target_id:
                raise ValidationError(_("Un flux ne peut pas boucler sur lui-même."))
            vers_note = rec.target_id.kind == "note" or rec.source_id.kind == "note"
            if vers_note and rec.routing != "assoc":
                raise ValidationError(
                    _("Une annotation se relie par une association, pas par un"
                      " flux de séquence."))
            if rec.routing == "assoc" and not vers_note:
                raise ValidationError(
                    _("Une association doit toucher une annotation."))

    def to_dict(self):
        self.ensure_one()
        d = {"src": self.source_id.code, "tgt": self.target_id.code}
        if self.routing and self.routing != "auto":
            d["r"] = self.routing
        if self.label:
            d["label"] = self.label
        if self.detour:
            d["d"] = self.detour
        if self.anchor_source:
            d["a"] = self.anchor_source
        if self.anchor_target:
            d["b"] = self.anchor_target
        if self.label_dx or self.label_dy:
            d["lp"] = (self.label_dx, self.label_dy)
        if self.label_width:
            d["lw"] = self.label_width
        return d


class BfProcessMessage(models.Model):
    """Un flux de message entre un nœud et un participant externe."""

    _name = "bf.process.message"
    _inherit = ["bf.process.gel"]
    _description = "Flux de message"
    _order = "diagram_id, sequence, id"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Niveau", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Processus", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    bpmn_id = fields.Char(string="Identifiant BPMN", required=True, copy=False)
    node_id = fields.Many2one(
        "bf.process.node", string="Nœud", required=True, ondelete="cascade")
    pool_id = fields.Many2one(
        "bf.process.pool", string="Participant externe", required=True,
        ondelete="cascade")
    direction = fields.Selection(
        [("out", "Vers le participant"), ("in", "Depuis le participant")],
        string="Sens", default="out", required=True)
    label = fields.Char(
        string="Message", required=True,
        help="Un nom de chose échangée, jamais un verbe ni un état.")
    dx = fields.Float(string="Décalage du point d'arrivée")
    label_dx = fields.Float(string="Décalage d'étiquette en X")
    label_dy = fields.Float(string="Décalage d'étiquette en Y")
    label_width = fields.Float(string="Largeur d'étiquette")

    _sql_constraints = [
        ("bpmn_id_uniq", "unique (process_id, bpmn_id)",
         "Cet identifiant BPMN est déjà pris dans ce processus."),
    ]

    @api.constrains("node_id", "pool_id")
    def _check_niveau(self):
        for rec in self:
            if rec.node_id.diagram_id != rec.diagram_id or \
               rec.pool_id.diagram_id != rec.diagram_id:
                raise ValidationError(
                    _("Un flux de message relie un nœud et un pool du même niveau."))

    def to_dict(self):
        self.ensure_one()
        d = {"node": self.node_id.code, "pool": self.pool_id.code,
             "label": self.label}
        if self.direction != "out":
            d["dir"] = self.direction
        if self.dx:
            d["dx"] = self.dx
        if self.label_dx:
            d["lx"] = self.label_dx
        if self.label_dy:
            d["ly"] = self.label_dy
        if self.label_width:
            d["lw"] = self.label_width
        return d
