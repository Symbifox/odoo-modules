from odoo import _, api, fields, models

# Default lure body. The CTA link MUST resolve per-recipient: in mass-mailing
# QWeb rendering the current record is exposed as ``object``, so we point the
# anchor at ``object.landing_url`` (an absolute /phish/<token> URL). A hidden
# open-pixel is appended automatically by the campaign at send time.
DEFAULT_EMAIL_BODY = """
<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;">
    <p>Bonjour,</p>
    <p>Une activité inhabituelle a été détectée sur votre compte. Pour éviter
       une suspension, veuillez confirmer vos informations dès maintenant.</p>
    <p style="text-align:center;margin:28px 0;">
        <a t-att-href="object.landing_url"
           style="background:#1a73e8;color:#fff;text-decoration:none;
                  padding:12px 22px;border-radius:4px;font-weight:bold;">
            Vérifier mon compte
        </a>
    </p>
    <p>Merci,<br/>L'équipe de support</p>
</div>
"""

DEFAULT_TEACHABLE_HTML = """
<p>Bonne nouvelle : <strong>aucune donnée n'a été compromise</strong>. Ce
   message faisait partie d'une simulation d'hameçonnage interne et autorisée,
   destinée à renforcer nos réflexes en cybersécurité.</p>
<p>Quelques signaux qui auraient dû éveiller vos soupçons :</p>
<ul>
    <li>un ton alarmiste qui pousse à agir vite;</li>
    <li>un lien dont l'adresse réelle ne correspond pas à l'expéditeur;</li>
    <li>une demande de saisir vos identifiants hors du site officiel.</li>
</ul>
<p>En cas de doute, ne cliquez pas : signalez le courriel à votre équipe TI.</p>
"""


class BfPhishingTemplate(models.Model):
    _name = "bf.phishing.template"
    _description = "Phishing simulation template"
    _order = "name"

    name = fields.Char(string="Name", required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Company",
        default=lambda self: self.env.company,
    )

    # --- The lure email -------------------------------------------------
    subject = fields.Char(
        string="Email subject", required=True, translate=True,
        help="Subject of the lure email. QWeb expressions (object.*) are "
             "rendered per recipient.",
    )
    email_from = fields.Char(
        string="Sender address",
        help="Address shown as the lure's sender (e.g. "
             "support@example-bank.com).",
    )
    sender_name = fields.Char(string="Sender name")
    # Même nettoyage que le corps d'un envoi de masse (où ce corps est recopié) et
    # qu'un modèle de courriel d'Odoo : ni script ni « on… » ni « javascript: »,
    # mais les styles, tableaux, commentaires Outlook et t-att-href restent, pour
    # qu'un leurre imite un vrai courriel.
    email_body = fields.Html(
        string="Email body", sanitize="email_outgoing", translate=True,
        default=DEFAULT_EMAIL_BODY,
        help="HTML body of the lure. Insert the trap link with <a "
             "t-att-href=\"object.landing_url\">…</a>.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment", string="Attachments",
        help="Attachments added to the lure (e.g. a fake document to "
             "open) to test people who open files.",
    )
    qr_code = fields.Boolean(
        string="Include a QR code (quishing)", default=False,
        help="Adds a QR code encoding the trap link, to test the mobile "
             "scanning reflex.",
    )

    # --- Difficulty & classification ------------------------------------
    difficulty = fields.Selection(
        [("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard")],
        string="Difficulty", default="medium", required=True,
        help="Weights the risk score: failing an easy lure counts more "
             "than failing a hard one.",
    )
    category = fields.Selection(
        [
            ("credential_harvest", "Credential theft"),
            ("attachment", "Booby-trapped attachment"),
            ("wire_fraud", "Wire fraud"),
            ("mfa_fatigue", "MFA fatigue"),
            ("generic", "Generic"),
        ],
        string="Category", default="credential_harvest", required=True,
    )

    # --- Landing behaviour ----------------------------------------------
    landing_mode = fields.Selection(
        [
            ("credential", "Fake login form, then learning page"),
            ("awareness", "Learning page only"),
            ("link_only", "No page (click tracking only)"),
        ],
        string="Landing page behavior",
        default="credential", required=True,
    )
    landing_title = fields.Char(
        string="Fake page title", translate=True,
        default="Connexion à votre compte",
    )
    landing_html = fields.Html(
        string="Fake page styling", sanitize=False, translate=True,
        help="Header of the fake page (\"fake form\" mode), rendered "
             "BEFORE the title and the form. Can hold a logo and a "
             "<style>…</style> block to closely imitate the targeted "
             "brand.",
    )
    landing_button_label = fields.Char(
        string="Button label", translate=True, default="Se connecter",
        help="Text of the fake form's button (e.g. \"Sign in\", \"Next\", "
             "\"Continue\").",
    )
    capture_field_lengths = fields.Boolean(
        string="Measure the length of entered fields", default=False,
        help="When enabled, records only the LENGTH of the submitted "
             "fields (never their value). The password is never stored.",
    )
    teachable_html = fields.Html(
        string="Learning page", sanitize=False, translate=True,
        default=DEFAULT_TEACHABLE_HTML,
        help="\"Teachable moment\" message shown after the click or "
             "submission. Must remind people that this is an authorized "
             "simulation.",
    )

    # --- Remediation ----------------------------------------------------
    training_channel_id = fields.Many2one(
        "slide.channel", string="Training assigned on failure",
        help="Security awareness course assigned automatically to anyone "
             "who clicks or submits their credentials.",
    )

    campaign_count = fields.Integer(
        string="Campaigns", compute="_compute_campaign_count",
    )

    def _compute_campaign_count(self):
        data = self.env["bf.phishing.campaign"]._read_group(
            [("template_id", "in", self.ids)],
            groupby=["template_id"], aggregates=["__count"],
        )
        mapped = {tpl.id: count for tpl, count in data}
        for tpl in self:
            tpl.campaign_count = mapped.get(tpl.id, 0)

    def action_view_campaigns(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Campaigns"),
            "res_model": "bf.phishing.campaign",
            "view_mode": "list,form",
            "domain": [("template_id", "=", self.id)],
            "context": {"default_template_id": self.id},
        }
