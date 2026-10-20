# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

ROUTAGES = [
    ("auto", "Automatic"),
    ("h", "Horizontal"),
    ("v", "Vertical"),
    ("hv", "Horizontal then vertical"),
    ("vh", "Vertical then horizontal"),
    ("above", "Detour above"),
    ("below", "Detour below"),
    ("assoc", "Association to an annotation"),
]
COTES = [("T", "Top"), ("B", "Bottom"), ("L", "Left"), ("R", "Right")]


class BfProcessFlow(models.Model):
    """Un flux de séquence, ou une association vers une annotation."""

    _name = "bf.process.flow"
    _inherit = ["bf.process.gel"]
    _description = "Sequence flow"
    _order = "diagram_id, sequence, id"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Level", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Process", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Order", default=10)
    bpmn_id = fields.Char(string="BPMN ID", required=True, copy=False)
    source_id = fields.Many2one(
        "bf.process.node", string="Origin", required=True, ondelete="cascade")
    target_id = fields.Many2one(
        "bf.process.node", string="Target", required=True, ondelete="cascade")
    label = fields.Char(
        string="Gate",
        help="The gate's name. The gates of a parallel or inclusive "
             "gateway are never named.")
    routing = fields.Selection(ROUTAGES, string="Routing", default="auto", required=True)
    detour = fields.Float(string="Detour depth")
    anchor_source = fields.Selection(COTES, string="Anchor at origin")
    anchor_target = fields.Selection(COTES, string="Anchor at target")
    label_dx = fields.Float(string="Label offset X")
    label_dy = fields.Float(string="Label offset Y")
    label_width = fields.Float(string="Label width")

    _sql_constraints = [
        ("bpmn_id_uniq", "unique (process_id, bpmn_id)",
         "This BPMN ID is already used in this process."),
    ]

    @api.constrains("source_id", "target_id", "routing")
    def _check_extremites(self):
        for rec in self:
            if rec.source_id.diagram_id != rec.diagram_id or \
               rec.target_id.diagram_id != rec.diagram_id:
                raise ValidationError(
                    _("A flow only links nodes of the same level."))
            if rec.source_id == rec.target_id:
                raise ValidationError(_("A flow cannot loop back on itself."))
            vers_note = rec.target_id.kind == "note" or rec.source_id.kind == "note"
            if vers_note and rec.routing != "assoc":
                raise ValidationError(
                    _("An annotation is linked by an association, not by "
                      "a sequence flow."))
            if rec.routing == "assoc" and not vers_note:
                raise ValidationError(
                    _("An association must touch an annotation."))

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
    _description = "Message flow"
    _order = "diagram_id, sequence, id"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Level", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Process", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Order", default=10)
    bpmn_id = fields.Char(string="BPMN ID", required=True, copy=False)
    node_id = fields.Many2one(
        "bf.process.node", string="Node", required=True, ondelete="cascade")
    pool_id = fields.Many2one(
        "bf.process.pool", string="External participant", required=True,
        ondelete="cascade")
    direction = fields.Selection(
        [("out", "To the participant"), ("in", "From the participant")],
        string="Direction", default="out", required=True)
    label = fields.Char(
        string="Message", required=True,
        help="The name of the thing exchanged, never a verb or a state.")
    dx = fields.Float(string="Arrival point offset")
    label_dx = fields.Float(string="Label offset X")
    label_dy = fields.Float(string="Label offset Y")
    label_width = fields.Float(string="Label width")

    _sql_constraints = [
        ("bpmn_id_uniq", "unique (process_id, bpmn_id)",
         "This BPMN ID is already used in this process."),
    ]

    @api.constrains("node_id", "pool_id")
    def _check_niveau(self):
        for rec in self:
            if rec.node_id.diagram_id != rec.diagram_id or \
               rec.pool_id.diagram_id != rec.diagram_id:
                raise ValidationError(
                    _("A message flow links a node and a pool of the same "
                      "level."))

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
