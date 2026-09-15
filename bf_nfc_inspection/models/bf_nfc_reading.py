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
    _description = "Relevé par pastille"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "tapped_at desc, id desc"

    name = fields.Char(string="Relevé", required=True, readonly=True, copy=False, default="/")
    checklist_id = fields.Many2one("bf.nfc.checklist", string="Grille", required=True,
                                   readonly=True, ondelete="restrict", index=True)
    tag_id = fields.Many2one("bf.nfc.tag", string="Pastille", readonly=True, ondelete="set null", index=True)
    # 🔴 Le nom ET l'endroit, tous deux recopiés au moment du relevé. Le registre
    # d'une inspection doit identifier CE QUI a été vérifié (« Extincteur 3 »), pas
    # seulement où on se tenait (« Hall ») : un bâtiment a plusieurs extincteurs au
    # même endroit, et la fiche de la RBQ demande un résultat par appareil.
    tag_name = fields.Char(string="Élément vérifié", readonly=True)
    place = fields.Char(string="Endroit", readonly=True,
                        help="Où la pastille est posée, recopié au moment du relevé.")
    res_model = fields.Char(readonly=True)
    res_id = fields.Many2oneReference(model_field="res_model", readonly=True)
    user_id = fields.Many2one("res.users", string="Par", readonly=True, index=True)
    signed_name = fields.Char(string="Nom donné", readonly=True,
                              help="Par une pastille signée, la personne qui tape n'a pas de compte : "
                                   "c'est le nom qu'elle a donné.")
    verifier = fields.Char(string="Vérifié par", compute="_compute_verifier", store=True)
    tapped_at = fields.Datetime(string="Relevé le", readonly=True, index=True)
    offline = fields.Boolean(string="Envoyé en différé", readonly=True)
    door = fields.Char(string="Porte", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True, index=True)
    line_ids = fields.One2many("bf.nfc.reading.line", "reading_id", string="Résultats", readonly=True)
    anomaly_count = fields.Integer(string="Anomalies", readonly=True)
    state = fields.Selection(
        [("conforme", "Conforme"), ("anomalie", "Anomalie"), ("corrige", "Anomalie corrigée")],
        string="État", readonly=True, index=True, tracking=True)
    correction = fields.Text(string="Correction", tracking=True,
                             help="Ce qui a été fait pour corriger l'anomalie.")
    correction_date = fields.Date(string="Corrigé le", tracking=True)
    corrected_by_id = fields.Many2one("res.users", string="Corrigé par", readonly=True, tracking=True)
    resume = fields.Char(string="Résultat", compute="_compute_resume")

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
            raise AccessError(_("Un relevé ne se modifie pas : seule sa correction se consigne."))
        return super().write(vals)

    def action_consigner_correction(self):
        """Marque l'anomalie corrigée : il faut dire ce qui a été fait, et quand."""
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Consigner une correction est réservé à la gestion des pastilles."))
        for releve in self:
            if releve.state != "anomalie":
                raise UserError(_("« %s » n'a pas d'anomalie ouverte.", releve.name))
            if not (releve.correction or "").strip():
                raise UserError(_("Décrivez la correction avant de la consigner."))
            releve.sudo().write({
                "state": "corrige",
                "correction_date": releve.correction_date or fields.Date.context_today(releve),
                "corrected_by_id": self.env.user.id,
            })
            releve.sudo().activity_ids.action_feedback(feedback=releve.correction)
        return True


class BfNfcReadingLine(models.Model):
    _name = "bf.nfc.reading.line"
    _description = "Résultat d'un élément de relevé"
    _order = "reading_id, sequence, id"

    reading_id = fields.Many2one("bf.nfc.reading", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer()
    item_id = fields.Many2one("bf.nfc.checklist.item", string="Élément de la grille", ondelete="set null")
    name = fields.Char(string="Élément", required=True)
    kind = fields.Char()
    value_bool = fields.Boolean(string="Conforme")
    value_float = fields.Float(string="Valeur")
    value_text = fields.Char(string="Réponse")
    unit = fields.Char(string="Unité")
    range_text = fields.Char(string="Plage")
    answered = fields.Boolean(string="Répondu")
    anomaly = fields.Boolean(string="Anomalie")

    def _texte_court(self):
        self.ensure_one()
        if not self.answered:
            return "%s : -" % self.name
        if self.kind == "conforme":
            valeur = _("conforme") if self.value_bool else _("NON conforme")
        elif self.kind == "nombre":
            valeur = ("%g %s" % (self.value_float, self.unit or "")).strip()
        else:
            valeur = self.value_text or ""
        return "%s : %s%s" % (self.name, valeur, " ⚠" if self.anomaly else "")
