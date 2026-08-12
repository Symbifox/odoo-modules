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
    _description = "Modèle de simulation d'hameçonnage"
    _order = "name"

    name = fields.Char(string="Nom", required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Société",
        default=lambda self: self.env.company,
    )

    # --- The lure email -------------------------------------------------
    subject = fields.Char(
        string="Sujet du courriel", required=True, translate=True,
        help="Sujet du courriel-leurre. Les expressions QWeb (object.*) sont "
             "rendues par destinataire.",
    )
    email_from = fields.Char(
        string="Adresse d'expéditeur",
        help="Adresse affichée comme expéditeur du leurre (ex. "
             "support@exemple-banque.com).",
    )
    sender_name = fields.Char(string="Nom de l'expéditeur")
    email_body = fields.Html(
        string="Corps du courriel", sanitize=False, translate=True,
        default=DEFAULT_EMAIL_BODY,
        help="Corps HTML du leurre. Insérez le lien piégé avec "
             "<a t-att-href=\"object.landing_url\">…</a>.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment", string="Pièces jointes",
        help="Pièces jointes ajoutées au leurre (ex. faux document à ouvrir) "
             "pour tester les personnes qui ouvrent les fichiers.",
    )
    qr_code = fields.Boolean(
        string="Inclure un code QR (quishing)", default=False,
        help="Ajoute un code QR encodant le lien piégé, pour tester le réflexe "
             "de numérisation sur mobile.",
    )

    # --- Difficulty & classification ------------------------------------
    difficulty = fields.Selection(
        [("easy", "Facile"), ("medium", "Moyen"), ("hard", "Difficile")],
        string="Difficulté", default="medium", required=True,
        help="Pondère le score de risque : échouer à un leurre facile pèse "
             "plus lourd qu'à un leurre difficile.",
    )
    category = fields.Selection(
        [
            ("credential_harvest", "Vol d'identifiants"),
            ("attachment", "Pièce jointe piégée"),
            ("wire_fraud", "Fraude au virement"),
            ("mfa_fatigue", "Fatigue MFA"),
            ("generic", "Générique"),
        ],
        string="Catégorie", default="credential_harvest", required=True,
    )

    # --- Landing behaviour ----------------------------------------------
    landing_mode = fields.Selection(
        [
            ("credential", "Faux formulaire de connexion puis page éducative"),
            ("awareness", "Page éducative directe"),
            ("link_only", "Aucune page (suivi du clic uniquement)"),
        ],
        string="Comportement de la page d'atterrissage",
        default="credential", required=True,
    )
    landing_title = fields.Char(
        string="Titre de la fausse page", translate=True,
        default="Connexion à votre compte",
    )
    landing_html = fields.Html(
        string="Habillage de la fausse page", sanitize=False, translate=True,
        help="En-tête de la fausse page (mode « faux formulaire »), rendu AVANT "
             "le titre et le formulaire. Peut contenir un logo et un bloc "
             "<style>…</style> pour imiter fidèlement la marque visée.",
    )
    landing_button_label = fields.Char(
        string="Libellé du bouton", translate=True, default="Se connecter",
        help="Texte du bouton du faux formulaire (ex. « Se connecter », "
             "« Suivant », « Continuer »).",
    )
    capture_field_lengths = fields.Boolean(
        string="Mesurer la longueur des champs saisis", default=False,
        help="Si activé, enregistre uniquement la LONGUEUR des champs soumis "
             "(jamais leur valeur). Le mot de passe n'est jamais stocké.",
    )
    teachable_html = fields.Html(
        string="Page éducative", sanitize=False, translate=True,
        default=DEFAULT_TEACHABLE_HTML,
        help="Message « moment d'apprentissage » affiché après le clic ou la "
             "soumission. Doit rappeler qu'il s'agit d'une simulation autorisée.",
    )

    # --- Remediation ----------------------------------------------------
    training_channel_id = fields.Many2one(
        "slide.channel", string="Formation assignée à l'échec",
        help="Cours de sensibilisation assigné automatiquement à toute "
             "personne qui clique ou soumet ses identifiants.",
    )

    campaign_count = fields.Integer(
        string="Campagnes", compute="_compute_campaign_count",
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
            "name": _("Campagnes"),
            "res_model": "bf.phishing.campaign",
            "view_mode": "list,form",
            "domain": [("template_id", "=", self.id)],
            "context": {"default_template_id": self.id},
        }
