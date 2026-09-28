"""Un lot d'étiquettes QR imprimées d'avance.

Un lot ne fait que tirer des étiquettes vierges et les numéroter. Tout le reste
(ce qu'elles font, qui les a scannées) vit sur les étiquettes, qui sont des
pastilles du socle.

⚠️ **La numérotation suit le préfixe, pas le lot.** Un deuxième lot « QR » part
après le dernier numéro du premier : « QR-0501 » ne désigne jamais deux
étiquettes dans la même société. C'est ce numéro, et non le code aléatoire,
qu'on lit à voix haute et qu'on met dans un tableur.
"""
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

QUANTITE_MAX = 5000
PREFIXE_RE = re.compile(r"^[A-Z0-9]{1,8}$")


class BfQrBatch(models.Model):
    _name = "bf.qr.batch"
    _description = "Lot d'étiquettes QR"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(string="Lot", required=True, tracking=True,
                       default=lambda self: _("Lot du %s", fields.Date.context_today(self)))
    prefixe = fields.Char(
        string="Préfixe", required=True, default="QR", tracking=True,
        help="Imprimé devant le numéro : « QR-0042 ». Lettres et chiffres, 8 au plus.")
    quantite = fields.Integer(string="Quantité", default=100, tracking=True)
    company_id = fields.Many2one("res.company", string="Société", required=True,
                                 default=lambda self: self.env.company)
    state = fields.Selection([("draft", "Brouillon"), ("done", "Généré")],
                             string="État", default="draft", required=True, tracking=True)
    tag_ids = fields.One2many("bf.nfc.tag", "qr_batch_id", string="Étiquettes",
                              context={"active_test": False})
    numero_premier = fields.Integer(string="Du n°", readonly=True)
    numero_dernier = fields.Integer(string="Au n°", readonly=True)
    count_total = fields.Integer(string="Nombre d'étiquettes", compute="_compute_counts")
    count_vierges = fields.Integer(string="À associer", compute="_compute_counts")
    count_associees = fields.Integer(string="Associées", compute="_compute_counts")
    note = fields.Text()

    @api.depends("tag_ids", "tag_ids.qr_vierge", "tag_ids.active")
    def _compute_counts(self):
        for lot in self:
            etiquettes = lot.with_context(active_test=False).tag_ids.filtered("active")
            lot.count_total = len(etiquettes)
            lot.count_vierges = len(etiquettes.filtered("qr_vierge"))
            lot.count_associees = lot.count_total - lot.count_vierges

    @api.onchange("prefixe")
    def _onchange_prefixe(self):
        if self.prefixe:
            self.prefixe = self.prefixe.strip().upper()

    def _exiger_la_gestion(self):
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Les lots d'étiquettes sont réservés à la gestion des pastilles."))

    def action_generer(self):
        """Tire les étiquettes vierges du lot, numérotées à la suite du préfixe."""
        self.ensure_one()
        self._exiger_la_gestion()
        if self.state != "draft":
            raise UserError(_("Ce lot a déjà été généré."))
        prefixe = (self.prefixe or "").strip().upper()
        if not PREFIXE_RE.match(prefixe):
            raise UserError(_("Le préfixe s'écrit en lettres et chiffres, 8 au plus."))
        if not 1 <= self.quantite <= QUANTITE_MAX:
            raise UserError(_("Un lot compte de 1 à %s étiquettes.", QUANTITE_MAX))
        Tag = self.env["bf.nfc.tag"]
        dernier = Tag.sudo().with_context(active_test=False).search([
            ("company_id", "=", self.company_id.id),
            ("qr_prefixe", "=", prefixe),
        ], order="qr_numero desc", limit=1).qr_numero or 0
        vierge = self.env.ref("bf_qr_manager.gesture_vierge")
        largeur = max(4, len(str(dernier + self.quantite)))
        valeurs = []
        for numero in range(dernier + 1, dernier + self.quantite + 1):
            reference = "%s-%s" % (prefixe, str(numero).zfill(largeur))
            valeurs.append({
                "name": reference,
                "qr_reference": reference,
                "qr_prefixe": prefixe,
                "qr_numero": numero,
                "qr_batch_id": self.id,
                "gesture_id": vierge.id,
                "company_id": self.company_id.id,
                "confirm_required": True,
            })
        Tag.create(valeurs)
        self.write({
            "prefixe": prefixe,
            "state": "done",
            "numero_premier": dernier + 1,
            "numero_dernier": dernier + self.quantite,
        })
        self.message_post(body=_("%(n)s étiquettes tirées, de %(a)s à %(b)s.",
                                 n=self.quantite, a=valeurs[0]["qr_reference"],
                                 b=valeurs[-1]["qr_reference"]))
        return True

    def action_voir_etiquettes(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_qr_manager.action_bf_qr_etiquettes")
        action["domain"] = [("qr_batch_id", "=", self.id)]
        action["context"] = {"search_default_en_service": 1}
        return action

    def action_imprimer(self):
        self.ensure_one()
        return self.tag_ids.filtered("active").action_imprimer_qr()

    def action_importer(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Associer par tableur"),
            "res_model": "bf.qr.import",
            "view_mode": "form",
            "target": "new",
            "context": {"default_batch_id": self.id},
        }

    def action_telecharger_modele(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": "/bf_qr/modele/%s" % self.id, "target": "self"}

    def unlink(self):
        if self.with_context(active_test=False).tag_ids:
            raise UserError(_("Un lot généré ne se supprime pas : ses étiquettes sont peut-être "
                              "déjà collées. Archivez les étiquettes plutôt."))
        return super().unlink()
