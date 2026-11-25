"""Le relevé : une grille remplie, qui la signe, et ce qu'on a fait des anomalies.

🔴 **Un relevé ne se modifie pas après coup.** Ses lignes sont en lecture seule
pour tout le monde, gestion comprise : un registre qu'on corrige en silence ne
prouve plus rien. Ce qui s'ajoute, c'est la CORRECTION (ce qui a été fait, par qui,
à quelle date), qui est justement ce que le registre doit porter.

⚠️ Le relevé est écrit en sudo, après la lecture de la pastille et de sa grille :
faire la ronde n'est pas un privilège de gestion. Il porte la personne qui tape,
ou, par la porte signée, le nom qu'elle a donné.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class BfNfcReading(models.Model):
    _name = "bf.nfc.reading"
    _description = "Reading by tag"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "tapped_at desc, id desc"

    name = fields.Char(string="Reading", required=True, readonly=True, copy=False, default="/")
    checklist_id = fields.Many2one("bf.nfc.checklist", string="Checklist", required=True,
                                   readonly=True, ondelete="restrict", index=True)
    tag_id = fields.Many2one("bf.nfc.tag", string="Tag", readonly=True, ondelete="set null", index=True)
    # 🔴 Le nom ET l'endroit, tous deux recopiés au moment du relevé. Le registre
    # d'une inspection doit identifier CE QUI a été vérifié (« Extincteur 3 »), pas
    # seulement où on se tenait (« Hall ») : un bâtiment a plusieurs extincteurs au
    # même endroit, et la fiche de la RBQ demande un résultat par appareil.
    tag_name = fields.Char(string="Item checked", readonly=True)
    place = fields.Char(string="Place", readonly=True,
                        help="Where the tag is stuck, copied at the time "
                             "of the reading.")
    res_model = fields.Char(readonly=True)
    res_id = fields.Many2oneReference(model_field="res_model", readonly=True)
    user_id = fields.Many2one("res.users", string="By", readonly=True, index=True)
    signed_name = fields.Char(string="Name given", readonly=True,
                              help="With a signed tag, the person tapping "
                                   "has no account: this is the name they "
                                   "gave.")
    verifier = fields.Char(string="Checked by", compute="_compute_verifier", store=True)
    tapped_at = fields.Datetime(string="Taken on", readonly=True, index=True)
    offline = fields.Boolean(string="Sent later", readonly=True)
    door = fields.Char(string="Door", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True, index=True)
    line_ids = fields.One2many("bf.nfc.reading.line", "reading_id", string="Results", readonly=True)
    anomaly_count = fields.Integer(string="Issues", readonly=True)
    state = fields.Selection(
        [("conforme", "Compliant"), ("anomalie", "Issue"), ("corrige", "Issue "
                                                                         "corrected")],
        string="Status", readonly=True, index=True, tracking=True)
    correction = fields.Text(string="Correction", tracking=True,
                             help="What was done to correct the issue.")
    correction_date = fields.Date(string="Corrected on", tracking=True)
    corrected_by_id = fields.Many2one("res.users", string="Corrected by", readonly=True, tracking=True)
    resume = fields.Char(string="Result", compute="_compute_resume")

    @api.depends("user_id", "signed_name")
    def _compute_verifier(self):
        for releve in self:
            releve.verifier = releve.signed_name or releve.user_id.name

    @api.depends("line_ids")
    def _compute_resume(self):
        for releve in self:
            releve.resume = " · ".join(ligne._texte_court() for ligne in releve.line_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code("bf.nfc.reading") or "/"
        return super().create(vals_list)

    def write(self, vals):
        """🔴 Seule la correction se modifie, et seulement par la gestion."""
        # 🔴 Ni `state` ni `corrected_by_id` : le seul chemin qui les écrit est
        # `action_consigner_correction`, qui passe en sudo. Les laisser ici
        # permettait de marquer « Anomalie corrigée » par XML-RPC, sans dire ce qui
        # avait été fait ni quand, et le registre sortait avec une colonne vide.
        permis = {"correction", "correction_date",
                  "activity_ids", "message_follower_ids", "message_main_attachment_id"}
        if not self.env.su and set(vals) - permis:
            raise AccessError(_("A reading cannot be changed: only its "
                                "correction can be logged."))
        return super().write(vals)

    def action_consigner_correction(self):
        """Marque l'anomalie corrigée : il faut dire ce qui a été fait, et quand."""
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Logging a correction is reserved for tag "
                                "managers."))
        for releve in self:
            if releve.state != "anomalie":
                raise UserError(_("“%s” has no open issue.", releve.name))
            if not (releve.correction or "").strip():
                raise UserError(_("Describe the correction before logging it."))
            releve.sudo().write({
                "state": "corrige",
                "correction_date": releve.correction_date or fields.Date.context_today(releve),
                "corrected_by_id": self.env.user.id,
            })
            releve.sudo().activity_ids.action_feedback(feedback=releve.correction)
        return True


class BfNfcReadingLine(models.Model):
    _name = "bf.nfc.reading.line"
    _description = "Result of a reading item"
    _order = "reading_id, sequence, id"

    reading_id = fields.Many2one("bf.nfc.reading", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer()
    item_id = fields.Many2one("bf.nfc.checklist.item", string="Checklist item", ondelete="set null")
    name = fields.Char(string="Item", required=True)
    kind = fields.Char()
    value_bool = fields.Boolean(string="Compliant")
    value_float = fields.Float(string="Value")
    value_text = fields.Char(string="Answer")
    unit = fields.Char(string="Unit")
    range_text = fields.Char(string="Range")
    answered = fields.Boolean(string="Answered")
    anomaly = fields.Boolean(string="Issue")

    def _texte_court(self):
        self.ensure_one()
        if not self.answered:
            return "%s : -" % self.name
        if self.kind == "conforme":
            valeur = _("compliant") if self.value_bool else _("NOT compliant")
        elif self.kind == "nombre":
            valeur = ("%g %s" % (self.value_float, self.unit or "")).strip()
        else:
            valeur = self.value_text or ""
        return "%s : %s%s" % (self.name, valeur, " ⚠" if self.anomaly else "")
