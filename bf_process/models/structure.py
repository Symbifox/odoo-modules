# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

KINDS = [
    ("start", "Début"),
    ("msgStart", "Début sur message"),
    ("timerCatch", "Attente d'un délai"),
    ("msgCatch", "Attente d'un message"),
    ("end", "Fin"),
    ("task", "Tâche"),
    ("send", "Tâche d'envoi"),
    ("receive", "Tâche de réception"),
    ("user", "Tâche humaine"),
    ("sub", "Sous-processus"),
    ("xor", "Passerelle exclusive"),
    ("and", "Passerelle parallèle"),
    ("or", "Passerelle inclusive"),
    ("note", "Annotation"),
    ("store", "Réserve de données"),
]
ACTIVITES = ("task", "send", "receive", "user", "sub")


class BfProcessLane(models.Model):
    """Un couloir : un exécutant du processus, à l'intérieur du pool."""

    _name = "bf.process.lane"
    _inherit = ["bf.process.gel"]
    _description = "Couloir"
    _order = "diagram_id, sequence, id"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Niveau", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Processus", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    name = fields.Char(string="Couloir", required=True)
    code = fields.Char(string="Code", required=True)
    node_ids = fields.One2many("bf.process.node", "lane_id", string="Nœuds")

    _sql_constraints = [
        ("code_uniq", "unique (diagram_id, code)",
         "Ce code de couloir est déjà pris dans ce niveau."),
    ]

    def to_dict(self):
        self.ensure_one()
        return {"id": self.code, "name": self.name}


class BfProcessPool(models.Model):
    """Un pool boîte noire : un participant externe, dont on ne voit rien."""

    _name = "bf.process.pool"
    _inherit = ["bf.process.gel"]
    _description = "Pool externe"
    _order = "diagram_id, sequence, id"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Niveau", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Processus", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    name = fields.Char(string="Participant", required=True)
    code = fields.Char(string="Code", required=True)
    position = fields.Selection(
        [("top", "Au-dessus"), ("bottom", "En dessous")],
        string="Position", default="top", required=True)

    _sql_constraints = [
        ("code_uniq", "unique (diagram_id, code)",
         "Ce code de pool est déjà pris dans ce niveau."),
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
    _description = "Nœud de processus"
    _inherit = ["mail.thread", "mail.activity.mixin", "bf.process.gel"]
    _order = "diagram_id, sequence, id"
    # discuter d'une étape ne doit pas exiger le droit de la déplacer
    _mail_post_access = "read"

    diagram_id = fields.Many2one(
        "bf.process.diagram", string="Niveau", required=True,
        ondelete="cascade", index=True)
    process_id = fields.Many2one(
        "bf.process", string="Processus", related="diagram_id.process_id",
        store=True, index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    lane_id = fields.Many2one("bf.process.lane", string="Couloir", ondelete="set null")
    code = fields.Char(string="Code", required=True)
    bpmn_id = fields.Char(
        string="Identifiant BPMN", required=True, copy=False,
        help="Stable, jamais régénéré : sans lui, un réimport duplique.")
    name = fields.Char(string="Libellé", tracking=True)
    kind = fields.Selection(KINDS, string="Type", required=True, default="task")
    tone = fields.Selection(
        [("ai", "Piste d'amélioration"), ("risk", "Fragilité ou information manquante")],
        string="Ton", help="Annotations seulement. Deux tons, et seulement deux.")

    col = fields.Float(string="Colonne", default=0.0, help="L'avancement.")
    row = fields.Float(string="Rangée", default=0.0, help="Le décalage dans le couloir.")
    width = fields.Float(string="Largeur", help="Surcharge ; vide = calculée.")
    height = fields.Float(string="Hauteur", help="Surcharge ; vide = calculée.")

    # --- registre de validation ---------------------------------------------
    # « Une carte non validée décrit une séquence, pas une performance. »
    # Le propriétaire du processus et un exécutant signent séparément : ce sont
    # deux regards différents, et c'est leur désaccord qui est instructif.
    valide_proprietaire = fields.Boolean(
        string="Validé par le propriétaire", tracking=True)
    valide_executant = fields.Boolean(
        string="Validé par un exécutant", tracking=True)
    conteste = fields.Boolean(
        string="Contesté", tracking=True,
        help="Quelqu'un dit que l'étape ne se passe pas comme ça.")
    note_validation = fields.Char(string="Remarque de validation")
    validation = fields.Selection(
        [("a_valider", "À valider"), ("partielle", "Validation partielle"),
         ("validee", "Validée"), ("conteste", "Contestée")],
        string="Validation", compute="_compute_validation", store=True)

    child_diagram_id = fields.Many2one(
        "bf.process.diagram", string="Niveau déplié", ondelete="set null",
        help="La page que ce sous-processus ouvre. Une même page peut être"
             " ouverte depuis plusieurs endroits : un sous-processus commun"
             " n'a pas un appelant unique.")

    _sql_constraints = [
        ("code_uniq", "unique (diagram_id, code)",
         "Ce code de nœud est déjà pris dans ce niveau."),
        # un identifiant BPMN n'a besoin d'être unique que dans son fichier,
        # et un fichier, c'est un processus.
        ("bpmn_id_uniq", "unique (process_id, bpmn_id)",
         "Cet identifiant BPMN est déjà pris dans ce processus."),
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
                    _("Seul un sous-processus ouvre un niveau : « %s » est un %s.")
                    % (rec.name, rec.kind))
            if enfant == rec.diagram_id:
                raise ValidationError(
                    _("Un nœud ne peut pas ouvrir sa propre page."))
            if enfant.process_id != rec.diagram_id.process_id:
                raise ValidationError(
                    _("La page dépliée doit appartenir au même processus."))

    @api.constrains("kind", "name")
    def _check_nommage(self):
        """Les règles de style qui se vérifient sans juger du fond."""
        for rec in self:
            if rec.kind in ACTIVITES and not rec.name:
                raise ValidationError(
                    _("Une activité doit être nommée, en verbe + complément."))
            if rec.kind == "note" and not rec.name:
                raise ValidationError(_("Une annotation sans texte ne dit rien."))
            if rec.tone and rec.kind != "note":
                raise ValidationError(
                    _("Le ton ne s'applique qu'aux annotations."))

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
