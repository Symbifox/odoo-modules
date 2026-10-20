# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

KINDS = [
    ("start", "Start"),
    ("msgStart", "Message start"),
    ("timerCatch", "Wait for a delay"),
    ("msgCatch", "Wait for a message"),
    ("end", "End"),
    ("task", "Task"),
    ("send", "Send task"),
    ("receive", "Receive task"),
    ("user", "User task"),
    ("sub", "Subprocess"),
    ("xor", "Exclusive gateway"),
    ("and", "Parallel gateway"),
    ("or", "Inclusive gateway"),
    ("note", "Annotation"),
    ("store", "Data store"),
]
ACTIVITES = ("task", "send", "receive", "user", "sub")


class BfProcessLane(models.Model):
    """Un couloir : un exécutant du processus, à l'intérieur du pool."""

    _name = "bf.process.lane"
    _inherit = ["bf.process.gel"]
    _description = "Lane"
    _order = "diagram_id, sequence, id"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Level", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Process", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Order", default=10)
    name = fields.Char(string="Lane", required=True)
    code = fields.Char(string="Code", required=True)
    node_ids = fields.One2many("bf.process.node", "lane_id", string="Nodes")

    _sql_constraints = [
        ("code_uniq", "unique (diagram_id, code)",
         "This lane code is already used in this level."),
    ]

    def to_dict(self):
        self.ensure_one()
        return {"id": self.code, "name": self.name}


class BfProcessPool(models.Model):
    """Un pool boîte noire : un participant externe, dont on ne voit rien."""

    _name = "bf.process.pool"
    _inherit = ["bf.process.gel"]
    _description = "External pool"
    _order = "diagram_id, sequence, id"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Level", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Process", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Order", default=10)
    name = fields.Char(string="Participant", required=True)
    code = fields.Char(string="Code", required=True)
    position = fields.Selection(
        [("top", "Above"), ("bottom", "Below")],
        string="Position", default="top", required=True)

    _sql_constraints = [
        ("code_uniq", "unique (diagram_id, code)",
         "This pool code is already used in this level."),
    ]

    def to_dict(self):
        self.ensure_one()
        return {"id": self.code, "name": self.name, "pos": self.position}


class BfProcessNode(models.Model):
    """Un nœud : activité, passerelle, événement, annotation.

    Sur `mail.thread`, parce que c'est l'étape qui porte la matière — photos
    d'atelier, gabarits, captures — et que le fil garde la trace de qui a dit
    quoi entre deux séances.
    """

    _name = "bf.process.node"
    _description = "Process node"
    _inherit = ["mail.thread", "mail.activity.mixin", "bf.process.gel"]
    _order = "diagram_id, sequence, id"
    # discuter d'une étape ne doit pas exiger le droit de la déplacer
    _mail_post_access = "read"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Level", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Process", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Order", default=10)
    lane_id = fields.Many2one("bf.process.lane", string="Lane", ondelete="set null")
    code = fields.Char(string="Code", required=True)
    bpmn_id = fields.Char(
        string="BPMN ID", required=True, copy=False,
        help="Stable, never regenerated: without it, a reimport duplicates.")
    name = fields.Char(string="Label", tracking=True)
    kind = fields.Selection(KINDS, string="Type", required=True, default="task")
    tone = fields.Selection(
        [("ai", "Improvement idea"), ("risk", "Weak point or missing "
                                                  "information")],
        string="Tone", help="Annotations only. Two tones, and only two.")

    col = fields.Float(string="Column", default=0.0, help="Progress.")
    row = fields.Float(string="Row", default=0.0, help="The offset "
                                                          "within the "
                                                          "lane.")
    width = fields.Float(string="Width", help="Override; empty = computed.")
    height = fields.Float(string="Height", help="Override; empty = computed.")

    # --- registre de validation ---------------------------------------------
    # « Une carte non validée décrit une séquence, pas une performance. »
    # Le propriétaire du processus et un exécutant signent séparément : ce sont
    # deux regards différents, et c'est leur désaccord qui est instructif.
    valide_proprietaire = fields.Boolean(
        string="Validated by the owner", tracking=True)
    valide_executant = fields.Boolean(
        string="Validated by a performer", tracking=True)
    conteste = fields.Boolean(
        string="Contested", tracking=True,
        help="Someone says the step does not happen this way.")
    note_validation = fields.Char(string="Validation note")
    validation = fields.Selection(
        [("a_valider", "To validate"), ("partielle", "Partial validation"),
         ("validee", "Validated"), ("conteste", "Disputed")],
        string="Validation", compute="_compute_validation", store=True)

    child_diagram_id = fields.Many2one(
        "bf.process.diagram", string="Expanded level", ondelete="set null",
        help="The page this subprocess opens. The same page can be opened "
             "from several places: a shared subprocess does not have a "
             "single caller.")

    _sql_constraints = [
        ("code_uniq", "unique (diagram_id, code)",
         "This node code is already used in this level."),
        # un identifiant BPMN n'a besoin d'être unique que dans son fichier,
        # et un fichier, c'est un processus.
        ("bpmn_id_uniq", "unique (process_id, bpmn_id)",
         "This BPMN ID is already used in this process."),
    ]

    @api.depends("valide_proprietaire", "valide_executant", "conteste")
    def _compute_validation(self):
        for rec in self:
            if rec.conteste:
                rec.validation = "conteste"
            elif rec.valide_proprietaire and rec.valide_executant:
                rec.validation = "validee"
            elif rec.valide_proprietaire or rec.valide_executant:
                rec.validation = "partielle"
            else:
                rec.validation = "a_valider"

    @api.constrains("child_diagram_id", "kind")
    def _check_niveau_deplie(self):
        for rec in self:
            enfant = rec.child_diagram_id
            if not enfant:
                continue
            if rec.kind != "sub":
                raise ValidationError(
                    _("Only a subprocess opens a level: \"%s\" is of type %s.")
                    % (rec.name, rec.kind))
            if enfant == rec.diagram_id:
                raise ValidationError(
                    _("A node cannot open its own page."))
            if enfant.process_id != rec.diagram_id.process_id:
                raise ValidationError(
                    _("The expanded page must belong to the same process."))

    @api.constrains("kind", "name")
    def _check_nommage(self):
        """Les règles de style qui se vérifient sans juger du fond."""
        for rec in self:
            if rec.kind in ACTIVITES and not rec.name:
                raise ValidationError(
                    _("An activity must be named, as verb + object."))
            if rec.kind == "note" and not rec.name:
                raise ValidationError(_("An annotation without text says "
                                        "nothing."))
            if rec.tone and rec.kind != "note":
                raise ValidationError(
                    _("Tone only applies to annotations."))

    @api.depends("name", "code")
    def _compute_display_name(self):
        """Une passerelle n'a pas toujours de libellé : son code la nomme."""
        for rec in self:
            rec.display_name = rec.name or rec.code

    def to_dict(self, teinte=None):
        """La forme d'échange. `teinte` n'existe que pour la vue delta.

        Elle ne se stocke pas : elle se calcule au moment du tracé, depuis les
        écarts. Une teinte enregistrée sur le nœud aurait vieilli en silence
        dès qu'un écart change d'état.
        """
        self.ensure_one()
        d = {"id": self.code, "kind": self.kind, "name": self.name or "",
             "col": self.col, "row": self.row}
        if self.lane_id:
            d["lane"] = self.lane_id.code
        if self.tone:
            d["tone"] = self.tone
        if self.width:
            d["w"] = self.width
        if self.height:
            d["h"] = self.height
        if teinte:
            d["teinte"] = teinte
        return d
