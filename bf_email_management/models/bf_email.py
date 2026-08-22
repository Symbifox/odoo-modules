import base64
import email as email_mod
import email.message
import email.policy
import email.utils
import logging
import mimetypes
import html
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from markupsafe import Markup

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError

from . import bf_email_imap
from . import imip
from .subject_utils import dedup_subject_prefix

_logger = logging.getLogger(__name__)

_DIRECTION_LABELS = {
    "in": "\u2190",
    "out": "\u2192",
}

# Heuristic signals \u2014 see README \u00a7Research for citations.
_QUESTION_RE = re.compile(r"\?")
_ACTION_REQUEST_RE = re.compile(
    r"\b(could you|can you|would you|please|kindly|"
    r"pouvez-vous|pourriez-vous|pourrais-tu|peux-tu|merci de|svp|s\.v\.p\.|"
    r"besoin de|j'ai besoin)\b",
    re.IGNORECASE,
)
_BULK_DOMAINS = (
    "mailchimp", "sendgrid", "constantcontact", "campaignmonitor",
    "amazonses", "mandrillapp", "substack", "convertkit", "klaviyo",
    "hubspot", "marketo", "eloqua", "salesforce", "intercom",
)


class BfEmail(models.Model):
    _name = "bf.email"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Courriel"
    _order = "date desc, id desc"
    _rec_name = "subject"

    # ------------------------------------------------------------------
    # Core fields
    # ------------------------------------------------------------------
    date = fields.Datetime(
        string="Date",
        required=True,
        index=True,
    )
    email_from = fields.Char(
        string="De",
        index=True,
    )
    email_to = fields.Char(
        string="\u00c0",
    )
    email_cc = fields.Char(
        string="CC",
    )
    subject = fields.Char(
        string="Objet",
        index=True,
    )
    body_preview = fields.Char(
        string="Aper\u00e7u",
        size=300,
        compute="_compute_body_preview",
        store=True,
    )
    body_html = fields.Html(
        string="Corps",
        compute="_compute_body_html",
        store=True,
        readonly=True,
        sanitize=False,
        help="Corps HTML du courriel. Pour une rangée chatter/gateway, "
             "synchronisé depuis mail.message.body. Pour une rangée IMAP "
             "orpheline, parsé depuis raw_rfc822.",
    )
    body_html_display = fields.Html(
        string="Corps (affichage)",
        compute="_compute_body_html_display",
        sanitize=False,
        readonly=True,
        help="Version assainie de body_html, rendue dans le formulaire. Le "
             "HTML brut d'un courriel entrant n'est jamais affiché tel quel "
             "(défense anti-XSS) ; body_html reste brut pour les "
             "constructeurs de réponse/transfert, qui assainissent au besoin.",
    )
    direction = fields.Selection(
        selection=[
            ("in", "Re\u00e7u"),
            ("out", "Envoy\u00e9"),
        ],
        string="Direction",
        required=True,
        index=True,
    )
    source = fields.Selection(
        selection=[
            ("gateway", "Passerelle courriel"),
            ("chatter", "Chatter"),
            ("imap", "IMAP direct"),
        ],
        string="Source",
        index=True,
        help="Origine du message\u00a0: passerelle courriel entrante/sortante, "
             "commentaire post\u00e9 via le chatter et notifi\u00e9 par courriel, "
             "ou ingestion IMAP directe (orphelin sans chatter Odoo).",
    )
    message_id_header = fields.Char(
        string="Message-ID",
        index=True,
        help="RFC 2822 Message-ID pour la d\u00e9duplication",
    )
    in_reply_to = fields.Char(
        string="In-Reply-To",
        index=True,
    )
    thread_root_id = fields.Char(
        string="Racine du fil",
        index=True,
        help="Message-ID racine du fil RFC 2822 (premier message). "
             "Permet de regrouper la conversation enti\u00e8re.",
    )
    thread_count = fields.Integer(
        string="Nb dans le fil",
        compute="_compute_thread_count",
    )

    # ------------------------------------------------------------------
    # IMAP-direct ingestion (orphan rows without mail.message)
    # ------------------------------------------------------------------
    imap_uid = fields.Char(
        string="IMAP UID",
        index=True,
        help="UID IMAP du message dans son dossier d'origine.",
    )
    imap_folder = fields.Char(
        string="Dossier IMAP",
        help="Dossier IMAP de provenance (INBOX, Sent, Archives/2026, etc.).",
    )
    raw_rfc822 = fields.Binary(
        string="RFC 2822 brut",
        attachment=True,
        help="Message RFC 2822 complet (brut). Conserv\u00e9 pour permettre "
             "le re-routage vers un chatter Odoo.",
    )

    # ------------------------------------------------------------------
    # Links to source
    # ------------------------------------------------------------------
    mail_message_id = fields.Many2one(
        comodel_name="mail.message",
        string="Message source",
        ondelete="set null",
        index=True,
    )
    res_model = fields.Char(
        string="Mod\u00e8le li\u00e9",
        index=True,
    )
    res_id = fields.Many2oneReference(
        string="Enregistrement li\u00e9",
        model_field="res_model",
    )
    record_name = fields.Char(
        string="Enregistrement",
        help="Nom de l'enregistrement li\u00e9 (d\u00e9normalis\u00e9 pour la performance)",
    )

    # ------------------------------------------------------------------
    # Partners
    # ------------------------------------------------------------------
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contact",
        index=True,
        help="Contact principal (exp\u00e9diteur pour entrant, destinataire pour sortant)",
    )
    author_id = fields.Many2one(
        comodel_name="res.partner",
        string="Auteur",
    )

    # ------------------------------------------------------------------
    # Enrichment
    # ------------------------------------------------------------------
    category = fields.Selection(
        selection=[
            ("client", "Client"),
            ("internal", "Interne"),
            ("vendor", "Fournisseur"),
            ("notification", "Notification"),
            ("marketing", "Marketing"),
        ],
        string="Cat\u00e9gorie",
        compute="_compute_category",
        store=True,
        readonly=False,
    )
    priority = fields.Selection(
        selection=[
            ("0", "Normal"),
            ("1", "Faible"),
            ("2", "\u00c9lev\u00e9e"),
            ("3", "Urgente"),
        ],
        string="Priorit\u00e9",
        default="0",
    )
    status = fields.Selection(
        selection=[
            ("new", "Nouveau"),
            ("read", "Lu"),
            ("replied", "R\u00e9pondu"),
            ("archived", "Archiv\u00e9"),
        ],
        string="Statut",
        default="new",
        required=True,
        index=True,
    )
    response_time_hours = fields.Float(
        string="Temps de r\u00e9ponse (h)",
        compute="_compute_response_time",
        store=True,
        help="Heures entre la r\u00e9ception et la premi\u00e8re r\u00e9ponse",
    )
    has_attachments = fields.Boolean(
        string="Pi\u00e8ces jointes",
        default=False,
    )
    attachment_count = fields.Integer(
        string="Nb pi\u00e8ces jointes",
        default=0,
    )
    attachment_ids = fields.Many2many(
        comodel_name="ir.attachment",
        string="Fichiers joints",
        related="mail_message_id.attachment_ids",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Operational
    # ------------------------------------------------------------------
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Soci\u00e9t\u00e9",
        default=lambda self: self.env.company,
        index=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Propri\u00e9taire",
        required=True,
        index=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
        help="Utilisateur qui poss\u00e8de cette ligne. La r\u00e8gle d'acc\u00e8s "
             "filtre par ce champ \u2014 aucun autre utilisateur ne le voit.",
    )
    account_id = fields.Many2one(
        comodel_name="bf.email.account",
        string="Compte IMAP",
        index=True,
        ondelete="set null",
        help="Compte IMAP d'ingestion. Vide pour les lignes chatter/passerelle.",
    )
    active = fields.Boolean(
        string="Actif",
        default=True,
    )

    is_foreign_owner = fields.Boolean(
        string="Propriété d'un autre utilisateur",
        compute="_compute_is_foreign_owner",
        help="Vrai lorsque la ligne appartient à un autre utilisateur que "
             "l'utilisateur courant. Pilote la bannière et la coloration de la "
             "zone admin (lecture seule).",
    )

    # Optional cross-app availability — drives the "Nouveau ▾" dropdown items.
    # Non-stored: no DB column, recomputed at load. See README §Création.
    has_helpdesk = fields.Boolean(
        string="Helpdesk installé",
        compute="_compute_optional_apps",
    )
    has_expense = fields.Boolean(
        string="Notes de frais installées",
        compute="_compute_optional_apps",
    )
    has_crm = fields.Boolean(
        string="CRM installé",
        compute="_compute_optional_apps",
    )

    # ------------------------------------------------------------------
    # Inbox-Zero workflow (decoupled from status)
    # ------------------------------------------------------------------
    is_handled = fields.Boolean(
        string="Traité",
        default=False,
        index=True,
        help="Sorti de la boîte de réception. Indépendant du statut "
             "(read/replied préservé). Inverse via « Remettre en boîte ».",
    )
    handled_at = fields.Datetime(
        string="Traité le",
        readonly=True,
    )
    snoozed_until = fields.Datetime(
        string="Reporté jusqu'à",
        index=True,
        help="Masqué de la boîte de réception jusqu'à cette date. "
             "Le cron de réveil flippe is_handled à False à l'échéance.",
    )

    imap_in_inbox = fields.Boolean(
        string="Dans INBOX IMAP",
        default=False,
        index=True,
        help="Reflète l'état IMAP réel : True si le message est encore "
             "dans INBOX selon le dernier scan. Mis à jour par "
             "_cron_imap_mirror toutes les 5 minutes. Faux par défaut : "
             "seules les lignes ingérées depuis un dossier IMAP le posent "
             "(voir _ingest_rfc822), les lignes chatter/gateway n'ont aucune "
             "contrepartie IMAP et entrent dans la boîte de réception par "
             "leur `source`, pas par ce drapeau.",
    )

    raw_headers = fields.Text(
        string="En-têtes RFC 2822",
        help="En-têtes complets (extraits de raw_rfc822 pour les rangées "
             "IMAP, ou de mail.message pour le chatter). Sert à la "
             "détection bulk via List-Unsubscribe.",
    )

    # ------------------------------------------------------------------
    # Heuristic signals (evidence-based — see README §Research)
    # ------------------------------------------------------------------
    is_short = fields.Boolean(
        string="Court",
        compute="_compute_signals",
        store=True,
        help="Aperçu < 280 caractères. Whittaker & Sidner 1996 — "
             "courriels courts répondus rapidement.",
    )
    is_question = fields.Boolean(
        string="Question posée",
        compute="_compute_signals",
        store=True,
        help="Point d'interrogation dans l'objet ou la dernière phrase. "
             "Dabbish & Kraut 2006 — questions ~4× plus susceptibles "
             "d'obtenir une réponse.",
    )
    is_action_request = fields.Boolean(
        string="Demande d'action",
        compute="_compute_signals",
        store=True,
        help="Verbe modal ou impératif détecté (please/pouvez-vous/merci de/…). "
             "Dabbish & Kraut 2006 — corrélé à la priorité perçue.",
    )
    is_to_me = fields.Boolean(
        string="À moi",
        compute="_compute_signals",
        store=True,
        help="Mon adresse dans To: (pas seulement CC/BCC). "
             "Dabbish & Kraut 2006 — direct-To ~3× plus de chance "
             "de réponse vs CC.",
    )
    is_cc_to_me = fields.Boolean(
        string="En CC à moi",
        compute="_compute_signals",
        store=True,
        help="Mon adresse présente dans CC: (et pas dans To:). "
             "Signal plus faible que is_to_me — FYI plutôt qu'action.",
    )
    is_from_me = fields.Boolean(
        string="De moi",
        compute="_compute_signals",
        store=True,
        help="Mon adresse dans From: (alias inclus). Couvre les envois "
             "via catchall (bonjour@, info@) où author_id n'est pas lié "
             "à l'utilisateur courant.",
    )
    is_late_night = fields.Boolean(
        string="Hors heures",
        compute="_compute_signals",
        store=True,
        help="Heure ∉ [8..18] ou fin de semaine. Kooti et al. 2015 — "
             "courriels hors heures penchent vers moins urgents.",
    )
    is_likely_thread = fields.Boolean(
        string="Fil actif",
        compute="_compute_signals",
        store=True,
        help="Plus d'un message dans le fil ET activité < 48h. "
             "Whittaker 2011 — fils actifs = à traiter par lots.",
    )
    is_bulk = fields.Boolean(
        string="En masse",
        compute="_compute_signals",
        store=True,
        index=True,
        help="List-Unsubscribe présent OU domaine connu (Mailchimp, etc.). "
             "Grbovic et al. 2014 — signal le plus fort pour marketing.",
    )
    external_age_hours = fields.Float(
        string="Âge en attente (h)",
        compute="_compute_external_age",
        help="Heures depuis réception jusqu'à réponse (ou maintenant). "
             "Inbound seulement. Kooti 2015 — médiane des réponses < 47 min, "
             "queue > 24h = vrai backlog.",
    )
    expected_reply_minutes = fields.Float(
        string="Réponse attendue (min)",
        help="Médiane glissante du temps de réponse pour ce contact (30j). "
             "Mise à jour par cron nocturne. Kooti 2015 — référence "
             "par-correspondant > seuils globaux.",
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    _sql_constraints = [
        (
            "message_id_header_uniq",
            "UNIQUE(message_id_header, company_id, user_id)",
            "Ce courriel existe d\u00e9j\u00e0 (Message-ID dupliqu\u00e9 pour ce\u00b7tte utilisateur\u00b7trice).",
        ),
    ]

    # ------------------------------------------------------------------
    # Display name
    # ------------------------------------------------------------------
    @api.depends("direction", "email_from", "email_to", "subject", "date")
    def _compute_display_name(self):
        for rec in self:
            arrow = _DIRECTION_LABELS.get(rec.direction, "?")
            contact = rec.email_from if rec.direction == "in" else rec.email_to
            contact = (contact or "")[:40]
            subj = (rec.subject or "")[:50]
            rec.display_name = f"{arrow} {contact} \u2014 {subj}"

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------
    @api.depends("user_id")
    def _compute_is_foreign_owner(self):
        me = self.env.user
        for rec in self:
            rec.is_foreign_owner = bool(rec.user_id and rec.user_id != me)

    @api.depends_context("uid")
    def _compute_optional_apps(self):
        has_h = "helpdesk.ticket" in self.env
        has_e = "hr.expense" in self.env
        has_c = "crm.lead" in self.env
        for rec in self:
            rec.has_helpdesk = has_h
            rec.has_expense = has_e
            rec.has_crm = has_c

    @staticmethod
    def _scrub_body(text):
        """Strip NUL bytes (PG TEXT refuses 0x00) and normalize."""
        if not text:
            return ""
        # PostgreSQL TEXT columns reject 0x00. Some clients embed them in
        # inline images or quoted-printable artifacts.
        return text.replace("\x00", "")

    @api.depends("mail_message_id.body", "raw_rfc822", "source")
    def _compute_body_html(self):
        for rec in self:
            if rec.mail_message_id and rec.mail_message_id.body:
                rec.body_html = self._scrub_body(rec.mail_message_id.body)
                continue
            if rec.source == "imap" and rec.raw_rfc822:
                try:
                    raw = base64.b64decode(rec.raw_rfc822)
                    parsed = bf_email_imap.parse_rfc822(raw)
                    body_html, body_plain = bf_email_imap.extract_body(parsed)
                    if body_html:
                        rec.body_html = self._scrub_body(body_html)
                    elif body_plain:
                        # Wrap plain text in <pre> for chatter-like rendering.
                        escaped = (body_plain
                                   .replace("&", "&amp;")
                                   .replace("<", "&lt;")
                                   .replace(">", "&gt;"))
                        rec.body_html = self._scrub_body(
                            f"<pre style=\"white-space:pre-wrap\">{escaped}</pre>"
                        )
                    else:
                        rec.body_html = ""
                except Exception:
                    _logger.warning(
                        "bf.email #%s: failed to parse raw_rfc822 for body",
                        rec.id, exc_info=True,
                    )
                    rec.body_html = ""
            else:
                rec.body_html = ""

    @api.depends("body_html")
    def _compute_body_html_display(self):
        """Sanitized view of ``body_html`` for safe rendering in the form.

        Inbound email HTML is stored raw (only NUL-stripped); rendering it
        verbatim in a readonly Html widget would execute attacker-controlled
        markup (e.g. ``<img onerror=…>``) in the owner's Odoo session. We
        sanitize on display while keeping ``body_html`` raw for the reply/
        forward builders (which sanitize at their own use sites).
        """
        for rec in self:
            rec.body_html_display = tools.html_sanitize(rec.body_html or "")

    @api.depends("body_html")
    def _compute_body_preview(self):
        r"""Aperçu texte du courriel, tel qu'il s'affiche en liste et sur mobile.

        ⚠️ Retirer les balises ne suffit pas : il faut aussi DÉCODER les
        entités. Sans ça, un corps qui commence par ``&#xA0;`` — ce que font
        Outlook et la plupart des infolettres — donne un aperçu qui commence
        littéralement par « &#xA0; ». Mesuré avant correction sur cette
        instance : 3 798 aperçus avec ``&nbsp;``, 499 avec ``&#xA0;``, et
        quelques centaines de ``&amp;``, ``&#39;``, ``&quot;``.

        L'ordre compte. Décoder AVANT de replier les blancs, parce que
        ``&#xA0;`` devient une espace insécable (U+00A0) que ``\s+`` sait
        alors absorber ; l'inverse laisserait une espace invisible en tête.
        """
        for rec in self:
            body = rec.body_html or ""
            text = re.sub(r"<[^>]+>", " ", body)
            text = html.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            rec.body_preview = text[:300]

    _NOTIFICATION_PATTERNS = re.compile(
        r"^(noreply|no-reply|notification|mailer-daemon|postmaster|bounce)"
        r"@",
        re.IGNORECASE,
    )

    @api.depends("partner_id", "author_id", "email_from")
    def _compute_category(self):
        for rec in self:
            # Detect notification senders by email pattern
            if rec.email_from and self._NOTIFICATION_PATTERNS.search(
                rec.email_from.strip()
            ):
                rec.category = "notification"
                continue

            partner = rec.partner_id or rec.author_id
            if not partner:
                rec.category = False
                continue
            # customer_rank / supplier_rank live on res.partner only when the
            # sale_team / purchase modules are installed. Tenants without
            # those modules would AttributeError otherwise.
            customer_rank = getattr(partner, "customer_rank", 0) or 0
            supplier_rank = getattr(partner, "supplier_rank", 0) or 0
            if partner.user_ids:
                rec.category = "internal"
            elif customer_rank > 0:
                rec.category = "client"
            elif supplier_rank > 0:
                rec.category = "vendor"
            else:
                rec.category = False

    @api.depends("date", "direction", "mail_message_id")
    def _compute_response_time(self):
        # Pre-load originals in one query to avoid N+1
        reply_to_ids = [
            r.in_reply_to for r in self
            if r.direction == "out" and r.in_reply_to
        ]
        originals = {}
        if reply_to_ids:
            # Scope to the owner of each rec — but in batch we can search
            # broadly under sudo and filter per-rec via user_id below.
            candidates = self.sudo().search([
                ("message_id_header", "in", reply_to_ids),
                ("user_id", "in", self.mapped("user_id").ids),
            ])
            for c in candidates:
                originals[(c.user_id.id, c.message_id_header)] = c.date

        for rec in self:
            rec.response_time_hours = 0.0
            if rec.direction != "out" or not rec.in_reply_to:
                continue
            orig_date = originals.get((rec.user_id.id, rec.in_reply_to))
            if orig_date and rec.date:
                delta = rec.date - orig_date
                rec.response_time_hours = round(
                    delta.total_seconds() / 3600, 2
                )

    # ------------------------------------------------------------------
    # Heuristic signal computation (evidence-based — see README §Research)
    # ------------------------------------------------------------------
    def _get_self_addresses(self, user=None):
        """Lowercase set of email addresses owned by ``user``.

        Combines the logins of all active bf.email.account rows owned by
        the user with the user's partner.email. Falls back to ``env.user``
        when no argument is passed (compat for callers without a user
        context).
        """
        target = user or self.env.user
        addrs = set()
        accounts = self.env["bf.email.account"].sudo().search([
            ("user_id", "=", target.id),
            ("active", "=", True),
        ])
        for login in accounts.mapped("login"):
            if login:
                addrs.add(login.strip().lower())
        for alias_blob in accounts.mapped("email_aliases"):
            if not alias_blob:
                continue
            for piece in re.split(r"[,;\s]+", alias_blob):
                piece = piece.strip().lower()
                if piece:
                    addrs.add(piece)
        if target.partner_id and target.partner_id.email:
            addrs.add(target.partner_id.email.strip().lower())
        if target.email:
            addrs.add(target.email.strip().lower())
        return addrs

    def _resolve_user_partner(self, user=None):
        """Return the res.partner row associated with ``user`` (or env.user)."""
        target = user or self.env.user
        return target.partner_id or self.env["res.partner"].browse()

    @api.depends(
        "subject", "body_preview", "email_to", "email_cc", "date",
        "raw_headers", "email_from", "thread_root_id",
    )
    def _compute_signals(self):
        # Group records by owner so each user's self-address set is fetched
        # once. Owners with no accounts get an empty set.
        addr_cache = {}
        def get_addrs(user):
            if user.id not in addr_cache:
                addr_cache[user.id] = self._get_self_addresses(user=user)
            return addr_cache[user.id]
        for rec in self:
            self_addrs = get_addrs(rec.user_id or self.env.user)
            preview = (rec.body_preview or "").strip()
            subject = (rec.subject or "").strip()

            rec.is_short = bool(preview) and len(preview) < 280

            last_sentence = preview.rsplit(".", 1)[-1] if preview else ""
            rec.is_question = bool(
                _QUESTION_RE.search(subject) or _QUESTION_RE.search(last_sentence)
            )

            rec.is_action_request = bool(
                _ACTION_REQUEST_RE.search(subject)
                or _ACTION_REQUEST_RE.search(preview)
            )

            to_addrs = (rec.email_to or "").lower()
            cc_addrs = (rec.email_cc or "").lower()
            from_addrs = (rec.email_from or "").lower()
            rec.is_to_me = any(addr in to_addrs for addr in self_addrs)
            rec.is_cc_to_me = (
                not rec.is_to_me
                and any(addr in cc_addrs for addr in self_addrs)
            )
            rec.is_from_me = any(addr in from_addrs for addr in self_addrs)

            if rec.date:
                hour = rec.date.hour
                weekday = rec.date.weekday()
                rec.is_late_night = hour < 8 or hour >= 18 or weekday >= 5
            else:
                rec.is_late_night = False

            rec.is_likely_thread = False  # set below by SQL recomputation

            headers = (rec.raw_headers or "").lower()
            email_from = (rec.email_from or "").lower()
            sender_domain = email_from.split("@")[-1] if "@" in email_from else ""
            rec.is_bulk = (
                "list-unsubscribe" in headers
                or any(d in sender_domain for d in _BULK_DOMAINS)
                or bool(self._NOTIFICATION_PATTERNS.search(email_from))
            )

        # is_likely_thread: per-thread sibling count + most-recent activity.
        roots = [r.thread_root_id for r in self if r.thread_root_id]
        if roots:
            self.env.cr.execute(
                """
                SELECT thread_root_id, COUNT(*) AS cnt, MAX(date) AS last_date
                FROM bf_email
                WHERE thread_root_id = ANY(%s)
                  AND active = TRUE
                GROUP BY thread_root_id
                """,
                [roots],
            )
            stats = {row[0]: (row[1], row[2]) for row in self.env.cr.fetchall()}
            cutoff = fields.Datetime.now() - timedelta(hours=48)
            for rec in self:
                if not rec.thread_root_id:
                    continue
                cnt, last_date = stats.get(rec.thread_root_id, (1, None))
                rec.is_likely_thread = cnt > 1 and last_date and last_date >= cutoff

    @api.depends("date", "direction", "status")
    def _compute_external_age(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.direction != "in" or not rec.date:
                rec.external_age_hours = 0.0
                continue
            if rec.status == "replied":
                rec.external_age_hours = rec.response_time_hours or 0.0
            else:
                delta = now - rec.date
                rec.external_age_hours = round(delta.total_seconds() / 3600, 2)

    # ------------------------------------------------------------------
    # Nightly cron: recompute expected_reply_minutes per partner
    # ------------------------------------------------------------------
    @api.model
    def _cron_recompute_expected_reply(self):
        """Update expected_reply_minutes per partner via 30d rolling median.

        Lightweight: one SQL aggregating reply times by partner_id, written
        back to all matching bf.email rows. Runs once daily.
        """
        cutoff = fields.Datetime.now() - timedelta(days=30)
        self.env.cr.execute(
            """
            WITH medians AS (
                SELECT partner_id,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (
                           ORDER BY response_time_hours
                       ) * 60 AS median_minutes
                  FROM bf_email
                 WHERE response_time_hours > 0
                   AND date >= %s
                   AND partner_id IS NOT NULL
                 GROUP BY partner_id
            )
            UPDATE bf_email be
               SET expected_reply_minutes = m.median_minutes
              FROM medians m
             WHERE be.partner_id = m.partner_id
            """,
            [cutoff],
        )
        _logger.info(
            "bf.email expected_reply_minutes: %s rows updated", self.env.cr.rowcount,
        )

    # ------------------------------------------------------------------
    # Auto-link orphans cron: associate IMAP rows to partners' open task
    # ------------------------------------------------------------------
    @api.model
    def _cron_auto_link_orphans(self):
        """Auto-link IMAP orphan emails to a partner's single open task/ticket.

        Conservative: only links if exactly one open project.task (or
        helpdesk.ticket if installed) belongs to the partner — never if
        ambiguous. Lookup window controlled by
        ``bf_email.auto_link_threshold_days`` (default 14).

        Does not post on the target chatter — it's a soft link only. The
        user can still rerouter to push the body via the wizard.

        Per-account: the threshold lives on bf.email.account.auto_link_threshold_days.
        Falls back to 14d for chatter-only rows (no account_id).
        """
        now = fields.Datetime.now()
        Account = self.env["bf.email.account"].sudo()
        # Per-account cutoffs — pick the most permissive (oldest cutoff) and
        # filter again per-row below.
        accounts = Account.search([("active", "=", True)])
        default_threshold = 14
        max_threshold = max(
            [a.auto_link_threshold_days or default_threshold for a in accounts],
            default=default_threshold,
        )
        cutoff_max = now - timedelta(days=max_threshold)
        orphans = self.sudo().search([
            ("source", "=", "imap"),
            ("res_model", "=", False),
            ("partner_id", "!=", False),
            ("date", ">=", cutoff_max),
            ("is_handled", "=", False),
            ("category", "in", ["client", "vendor"]),
        ], limit=200)
        # Filter to each row's own per-account threshold.
        def within_threshold(rec):
            t = (rec.account_id.auto_link_threshold_days
                 if rec.account_id else default_threshold) or default_threshold
            return rec.date >= now - timedelta(days=t)
        orphans = orphans.filtered(within_threshold)
        Task = self.env["project.task"]
        Ticket = self.env["helpdesk.ticket"] if "helpdesk.ticket" in self.env else None
        linked = 0
        for email in orphans:
            partner_id = email.partner_id.id
            target = None
            tasks = Task.search([
                ("partner_id", "=", partner_id),
                ("state", "in", ["01_in_progress", "02_changes_requested"]),
                ("active", "=", True),
            ], limit=2)
            if len(tasks) == 1:
                target = tasks
            elif Ticket is not None:
                tickets = Ticket.search([
                    ("partner_id", "=", partner_id),
                    ("closed", "=", False),
                ], limit=2)
                if len(tickets) == 1:
                    target = tickets
            if target:
                email.write({
                    "res_model": target._name,
                    "res_id": target.id,
                    "record_name": (target.display_name or "")[:200],
                })
                linked += 1
        _logger.info(
            "bf.email auto-link orphans: %s/%s rows linked",
            linked, len(orphans),
        )

    @api.depends("thread_root_id", "user_id")
    def _compute_thread_count(self):
        for rec in self:
            if not rec.thread_root_id:
                rec.thread_count = 1
                continue
            rec.thread_count = self.with_context(active_test=False).search_count([
                ("thread_root_id", "=", rec.thread_root_id),
                ("user_id", "=", rec.user_id.id),
            ])

    # ------------------------------------------------------------------
    # Needaction (menu badge for unread count)
    # ------------------------------------------------------------------
    @api.model
    def _needaction_domain_get(self):
        return [("status", "=", "new")]

    # ------------------------------------------------------------------
    # Auto mark-as-read on form open
    # ------------------------------------------------------------------
    def web_read(self, specification):
        result = super().web_read(specification)
        new_recs = self.filtered(lambda r: r.status == "new")
        if new_recs:
            # Only flip rows the current user may actually write. The
            # "admin sees all (read-only)" rule grants read on other people's
            # mail, so any list surfacing a foreign row (thread view, filter
            # cleared) would otherwise raise AccessError for the whole
            # request. Reading someone else's mailbox must not mark it read
            # on their behalf either.
            new_recs._filtered_access("write").write({"status": "read"})
        return result

    # ------------------------------------------------------------------
    # Auto-replied: when an outbound row is created with in_reply_to set,
    # flip the matching inbound row's status to 'replied'.
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.direction != "out" or not rec.in_reply_to:
                continue
            parent = self.search([
                ("message_id_header", "=", rec.in_reply_to),
                ("direction", "=", "in"),
                ("status", "in", ("new", "read")),
                ("user_id", "=", rec.user_id.id),
            ], limit=1)
            if parent:
                parent.write({"status": "replied"})
        # Apply user-defined rules (auto-categorization, set_handled, etc.)
        try:
            records._apply_rules()
        except Exception:
            _logger.warning(
                "bf.email: rule engine failed during create()", exc_info=True,
            )
        return records

    def _apply_rules(self):
        """Run active bf.email.rule definitions over each record.

        Rules are evaluated in (sequence, id) order. First matching rule per
        target field wins; later rules can still set untouched fields unless
        an earlier one set ``stop_processing=True``.
        """
        if not self:
            return
        Rule = self.env["bf.email.rule"].sudo()
        # Group records by owner so each user's rule set is fetched once.
        by_user = {}
        for rec in self:
            by_user[rec.user_id.id] = by_user.get(rec.user_id.id, self.env["bf.email"]) | rec
        auto_handled = self.browse()
        for uid, records in by_user.items():
            rules = Rule.search([("user_id", "=", uid)])
            if not rules:
                continue
            for rec in records:
                written_fields = set()
                for rule in rules:
                    if not rule._match(rec):
                        continue
                    vals = {}
                    if rule.set_category and "category" not in written_fields:
                        vals["category"] = rule.set_category
                        written_fields.add("category")
                    if rule.set_priority and "priority" not in written_fields:
                        vals["priority"] = rule.set_priority
                        written_fields.add("priority")
                    if rule.set_partner_id and "partner_id" not in written_fields:
                        vals["partner_id"] = rule.set_partner_id.id
                        written_fields.add("partner_id")
                    if rule.set_handled and not rec.is_handled:
                        vals["is_handled"] = True
                        vals["handled_at"] = fields.Datetime.now()
                        written_fields.add("is_handled")
                        auto_handled |= rec
                    if vals:
                        rec.write(vals)
                    if rule.stop_processing:
                        break
        # Mirror action_archive's bilateral IMAP writeback for rule-driven
        # auto-handles. Skip rows whose account has writeback disabled.
        if auto_handled:
            writeback_rows = auto_handled.filtered(
                lambda r: r.account_id and r.account_id.writeback_archive
            )
            if writeback_rows:
                try:
                    writeback_rows._imap_writeback_archive()
                except Exception:
                    _logger.warning(
                        "bf.email rule auto-handle IMAP writeback failed",
                        exc_info=True,
                    )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_mark_read(self):
        self.filtered(lambda r: r.status == "new").write({"status": "read"})

    def action_mark_replied(self):
        self.write({"status": "replied"})

    def action_archive(self):
        """Sortir de la boîte de réception sans toucher au statut.

        Préserve `read`/`replied` afin de conserver l'historique. Par défaut,
        déplace aussi le message côté IMAP vers `Archives/{YYYY}` selon
        ``bf.email.account.writeback_archive`` (ON par défaut depuis 18.0.4.0.0).

        Pour un seul enregistrement, ré-ouvre le formulaire avec un flag
        de contexte ``bf_email_just_handled`` afin que le bouton « Remettre »
        agisse comme un undo transitoire (disparaît au rechargement).
        """
        self.write({
            "is_handled": True,
            "handled_at": fields.Datetime.now(),
        })
        # Bilateral IMAP archive: per-account gate. Rows without an account
        # (chatter/gateway) skip naturally; account.writeback_archive=False
        # also skips.
        writeback_rows = self.filtered(
            lambda r: r.account_id and r.account_id.writeback_archive
        )
        if writeback_rows:
            try:
                writeback_rows._imap_writeback_archive()
            except Exception:
                _logger.warning(
                    "bf.email IMAP writeback archive failed", exc_info=True,
                )
        # Close the row's own open reminder activities — a treated email
        # shouldn't keep nagging. Only activities carried by the bf.email
        # row itself; task/ticket activities are never touched.
        open_activities = self.activity_ids
        if open_activities:
            try:
                open_activities.action_feedback(
                    feedback=_("Courriel marqué « Traité »."),
                )
            except Exception:
                _logger.warning(
                    "bf.email: closing activities on archive failed",
                    exc_info=True,
                )
        # Only re-open the form (with undo context flag) when explicitly
        # asked by the form-header caller. List inline / bulk callers
        # don't pass the flag, so they get None back and the list simply
        # refreshes in place — no navigation.
        if len(self) == 1 and self.env.context.get("with_undo_redirect"):
            return {
                "type": "ir.actions.act_window",
                "res_model": self._name,
                "res_id": self.id,
                "view_mode": "form",
                "views": [[False, "form"]],
                "target": "current",
                "context": {**self.env.context, "bf_email_just_handled": True},
            }

    def action_unhandle(self):
        """Remettre dans la boîte de réception — des DEUX côtés.

        Archiver était bilatéral (le message part vers ``Archives/{YYYY}`` sur
        le serveur IMAP) alors que restaurer ne touchait que les champs Odoo.
        Conséquence : la ligne revenait « en boîte » sans que le message y
        revienne, et comme le cron miroir avait déjà mis ``imap_in_inbox`` à
        faux, elle échouait au filtre de la boîte de réception. Le message
        n'était plus joignable ni depuis l'app, ni depuis la vraie INBOX —
        il fallait aller le rechercher à la main dans le webmail.

        Supportable tant qu'archiver était un clic délibéré sur un formulaire.
        Depuis que c'est un balayage du doigt sur un téléphone, c'est l'erreur
        que tout le monde fait.
        """
        restorable = self.filtered(
            lambda r: r.is_handled and r.account_id
            and r.account_id.writeback_archive and r.message_id_header
            and not r.imap_in_inbox
        )
        self.write({"is_handled": False, "handled_at": False, "snoozed_until": False})
        if restorable:
            try:
                restorable._imap_writeback_restore()
            except Exception:
                _logger.warning(
                    "bf.email IMAP writeback restore failed", exc_info=True,
                )

    def _imap_writeback_restore(self):
        """Ramener les messages de leur dossier d'archive vers INBOX.

        Contrepartie de ``_imap_writeback_archive``. La recherche se fait par
        Message-ID et non par ``imap_uid`` : l'archivage y a laissé l'UID que
        le message avait en INBOX, alors que la copie lui en a donné un autre
        dans le dossier d'archive.

        Même discipline que l'archivage : un ``COPY`` refusé laisse le message
        où il est plutôt que de le supprimer d'un côté sans l'avoir posé de
        l'autre.
        """
        candidates = self.filtered(lambda r: r.message_id_header and r.account_id)
        if not candidates:
            return

        by_account = {}
        for rec in candidates:
            by_account.setdefault(rec.account_id, self.env["bf.email"])
            by_account[rec.account_id] |= rec

        for account, recs in by_account.items():
            if not (account.host and account.login and account.password):
                continue
            try:
                conn = bf_email_imap.open_connection(
                    account.host, account.port, account.login, account.password,
                )
            except bf_email_imap.ImapConnectionError as exc:
                _logger.warning(
                    "bf.email IMAP restore (%s): %s", account.display_name, exc,
                )
                continue
            try:
                for rec in recs:
                    source = rec.imap_folder or ""
                    if not source or source.upper() == "INBOX":
                        continue
                    if not bf_email_imap.select_folder(conn, source, readonly=False):
                        _logger.info(
                            "bf.email IMAP restore: dossier %r non sélectionnable "
                            "pour #%s", source, rec.id)
                        continue
                    uid = self._imap_find_uid(conn, rec.message_id_header)
                    if not uid:
                        _logger.info(
                            "bf.email IMAP restore: #%s introuvable dans %r",
                            rec.id, source)
                        continue
                    try:
                        uid_tok = bf_email_imap.imap_uid_token(uid)
                        status, resp = conn.uid(
                            "COPY", uid_tok,
                            bf_email_imap.imap_quote_mailbox("INBOX"),
                        )
                        if status != "OK":
                            _logger.warning(
                                "bf.email IMAP restore: COPY vers INBOX refusé "
                                "pour #%s (UID %s): %s — message laissé dans %r",
                                rec.id, uid, resp, source)
                            continue
                        conn.uid("STORE", uid_tok, "+FLAGS", "(\\Deleted)")
                        conn.expunge()
                    except Exception:
                        _logger.warning(
                            "bf.email IMAP restore failed for UID %s", uid,
                            exc_info=True)
                        continue
                    # Le message porte un nouvel UID en INBOX ; le retrouver
                    # évite de laisser la ligne pointer sur un UID périmé.
                    new_uid = None
                    if bf_email_imap.select_folder(conn, "INBOX", readonly=True):
                        new_uid = self._imap_find_uid(conn, rec.message_id_header)
                    rec.write({
                        "imap_folder": "INBOX",
                        "imap_in_inbox": True,
                        "imap_uid": str(new_uid) if new_uid else rec.imap_uid,
                    })
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

    @api.model
    def _imap_find_uid(self, conn, message_id):
        """Premier UID du dossier courant portant ce Message-ID, ou None."""
        try:
            status, data = conn.uid(
                "SEARCH", None, "HEADER", "Message-ID",
                bf_email_imap.imap_reject_crlf(message_id, "Message-ID"),
            )
        except Exception:
            _logger.debug("bf.email: recherche Message-ID échouée (%s)",
                          message_id, exc_info=True)
            return None
        if status != "OK" or not data or not data[0]:
            return None
        raw = data[0]
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", errors="ignore")
        found = [x for x in raw.split() if x.isdigit()]
        return found[0] if found else None

    def action_snooze(self):
        """Open snooze wizard for selected emails."""
        return {
            "type": "ir.actions.act_window",
            "name": "Reporter",
            "res_model": "bf.email.snooze",
            "view_mode": "form",
            # Explicit `views`: also reached via orm.call (imap_browser_snooze
            # in the OWL browser), which bypasses clean_action()/generate_views().
            "views": [[False, "form"]],
            "target": "new",
            "context": {"default_bf_email_ids": [(6, 0, self.ids)]},
        }

    def _imap_writeback_archive(self):
        """Move corresponding IMAP messages from INBOX to Archives/{YYYY}.

        Per-account: rows are grouped by ``account_id`` and one connection
        is opened per account. Rows without an account_id (chatter/gateway
        origin) are skipped — there's no IMAP server to write to.
        """
        candidates = self.filtered(lambda r: r.message_id_header and r.account_id)
        if not candidates:
            return

        by_account = {}
        for rec in candidates:
            by_account.setdefault(rec.account_id, self.env["bf.email"])
            by_account[rec.account_id] |= rec

        for account, recs in by_account.items():
            if not (account.host and account.login and account.password):
                continue
            try:
                conn = bf_email_imap.open_connection(
                    account.host, account.port, account.login, account.password,
                )
            except bf_email_imap.ImapConnectionError as exc:
                _logger.warning(
                    "bf.email IMAP writeback (%s): %s", account.display_name, exc,
                )
                continue
            try:
                if not bf_email_imap.select_folder(conn, "INBOX", readonly=False):
                    continue
                tpl = account.archive_folder or "Archives/{YYYY}"
                for rec in recs:
                    uid = None
                    if rec.imap_uid and (rec.imap_folder or "").upper() == "INBOX":
                        uid = rec.imap_uid
                    else:
                        try:
                            status, data = conn.uid(
                                "SEARCH", None, "HEADER", "Message-ID",
                                bf_email_imap.imap_reject_crlf(
                                    rec.message_id_header, "Message-ID"),
                            )
                            if status == "OK" and data and data[0]:
                                raw = data[0]
                                if isinstance(raw, bytes):
                                    raw = raw.decode("ascii", errors="ignore")
                                found = [x for x in raw.split() if x.isdigit()]
                                uid = found[0] if found else None
                        except Exception:
                            _logger.debug(
                                "bf.email writeback HEADER search failed "
                                "for #%s (%s)", rec.id, rec.message_id_header,
                                exc_info=True,
                            )
                    if not uid:
                        continue
                    year = (rec.date or fields.Datetime.now()).strftime("%Y")
                    target = tpl.replace("{YYYY}", year)
                    try:
                        uid_tok = bf_email_imap.imap_uid_token(uid)
                        status, resp = conn.uid(
                            "COPY", uid_tok,
                            bf_email_imap.imap_quote_mailbox(target),
                        )
                        # imaplib only raises on BAD, never on NO. Without this
                        # guard a refused COPY (folder absent, quota, lock) is
                        # still followed by STORE \Deleted + EXPUNGE and the
                        # message is destroyed with no copy anywhere.
                        if status != "OK":
                            _logger.warning(
                                "bf.email IMAP writeback: COPY vers %r refusé "
                                "pour #%s (UID %s): %s — message laissé en "
                                "INBOX", target, rec.id, uid, resp,
                            )
                            continue
                        conn.uid("STORE", uid_tok, "+FLAGS", "(\\Deleted)")
                        rec.write({
                            "imap_uid": str(uid),
                            "imap_folder": target,
                            "imap_in_inbox": False,
                        })
                    except Exception:
                        _logger.warning(
                            "bf.email IMAP writeback failed for UID %s",
                            uid, exc_info=True,
                        )
                try:
                    conn.expunge()
                except Exception:
                    pass
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

    def action_open_source_record(self):
        self.ensure_one()
        if self.res_model and self.res_id:
            self.env[self.res_model].check_access_rights("read")
            self.env[self.res_model].browse(self.res_id).check_access_rule("read")
            return {
                "type": "ir.actions.act_window",
                "res_model": self.res_model,
                "res_id": self.res_id,
                "views": [[False, "form"]],
                "target": "current",
            }

    def action_reply(self):
        """Reply dispatcher \u2014 works for all 4 branches.

        | direction | res_model | composer target               |
        |-----------|-----------|-------------------------------|
        | in        | yes       | source record (chatter)       |
        | in        | no        | own res.partner (orphan IMAP) |
        | out       | yes       | source record (chatter)       |
        | out       | no        | own res.partner (orphan IMAP) |
        """
        self.ensure_one()
        return self._open_composer(mode="reply")

    def action_reply_all(self):
        """Reply-All: To = original sender, Cc = other thread participants.

        Excludes the current user's own addresses and the tenant's internal
        catchall/bounce aliases. Internal partners are kept (an internal
        colleague included on the thread should remain in Cc).
        """
        self.ensure_one()
        return self._open_composer(mode="reply_all")

    def action_forward(self):
        """Forward dispatcher \u2014 same 4 branches as reply, no default
        partners, body wraps the original behind a 'Forwarded message'
        header. Attachments re-attached for orphan IMAP rows."""
        self.ensure_one()
        return self._open_composer(mode="forward")

    def _open_composer(self, mode="reply"):
        """Build the composer action for reply / reply_all / forward.

        ``mode`` \u2208 {"reply", "reply_all", "forward"}.
        """
        self.ensure_one()
        is_forward = mode == "forward"
        is_reply_all = mode == "reply_all"

        # Mark as replied for genuine inbound replies only.
        if (not is_forward and self.direction == "in"
                and self.status in ("new", "read")):
            self.write({"status": "replied"})

        if is_forward:
            to_partner_ids = []
            cc_partner_ids = []
        elif is_reply_all:
            to_partner_ids, cc_partner_ids = self._build_reply_all_recipients()
        else:
            to_partner_ids = self._build_reply_recipients()
            cc_partner_ids = []

        prefix = "Fwd:" if is_forward else "Re:"
        # Collapse any stacked Re:/Fwd:/TR: the original subject already
        # carried into a single canonical prefix, instead of only guarding
        # against an exact "Re:" head (which let "Re: Re:" and French
        # "Re : " through).
        subject = dedup_subject_prefix(self.subject, force=prefix)

        if is_forward:
            quote_body = self._build_forward_body()
        else:
            quote_body = self._build_reply_quote_body()

        target_model, target_res_id = self._composer_target()

        # Cc/Bcc plumbing comes from mail_composer_cc_bcc (partner_cc_ids /
        # partner_bcc_ids fields, already wired into mail.message and outbound
        # rendering). We only feed the lists; the override on
        # _compute_partner_cc_bcc_ids in this module honors these defaults so
        # they survive the inherited recompute.
        ctx = {
            "default_model": target_model,
            "default_res_ids": [target_res_id],
            "default_composition_mode": "comment",
            "default_partner_ids": [(6, 0, to_partner_ids)],
            "default_partner_cc_ids": [(6, 0, cc_partner_ids)],
            "default_partner_bcc_ids": [(6, 0, [])],
            "default_subject": subject,
            "default_notify": True,
            "force_email": True,
            # mail_quoted_reply._compute_body only injects quote_body when
            # is_quoted_reply is truthy. Forwards must opt in too, otherwise the
            # forwarded body is silently dropped and the composer opens empty.
            "is_quoted_reply": True,
            "quote_body": quote_body,
            "mail_create_nosubscribe": True,
        }

        # Forwards on orphans: ship the original attachments.
        if is_forward and not self.mail_message_id and self.raw_rfc822:
            attachment_ids = self._extract_orphan_attachments()
            if attachment_ids:
                ctx["default_attachment_ids"] = [(6, 0, attachment_ids)]

        action = self.env["ir.actions.actions"]._for_xml_id(
            "mail.action_email_compose_message_wizard"
        )
        action["context"] = ctx
        return action

    def _build_reply_all_recipients(self):
        """Return (to_ids, cc_ids) for Reply-All.

        TO = original sender (or original recipients if outbound).
        CC = every other address in the thread's To+Cc, minus:
             * the current user's own emails,
             * the tenant's bounce + catchall aliases,
             * the company's noreply alias.
        """
        self.ensure_one()
        Partner = self.env["res.partner"].sudo()
        to_partners = self._build_reply_recipients()

        # Collect candidate Cc addresses from the source.
        cc_candidates = []
        if self.email_to:
            cc_candidates.extend(
                a.strip() for a in self.email_to.split(",") if a.strip()
            )
        if self.email_cc:
            cc_candidates.extend(
                a.strip() for a in self.email_cc.split(",") if a.strip()
            )

        # Build the exclusion set.
        exclude = set()
        user = self.env.user
        if user.partner_id.email:
            exclude.add(user.partner_id.email.lower())
        if user.email:
            exclude.add(user.email.lower())
        if user.company_id.email:
            exclude.add(user.company_id.email.lower())
        Param = self.env["ir.config_parameter"].sudo()
        for key in (
            "mail.bounce.alias",
            "mail.catchall.alias",
            "mail.default.from",
        ):
            val = Param.get_param(key)
            if val:
                exclude.add(str(val).lower())
        # Also exclude the row owner's own IMAP account logins.
        exclude |= self._get_self_addresses(user=self.user_id or user)
        # Also exclude the original sender (already in TO).
        if self.email_from:
            _name, bare = parseaddr(self.email_from)
            if bare:
                exclude.add(bare.lower())

        cc_ids = []
        for addr in cc_candidates:
            _name, bare = parseaddr(addr)
            bare = (bare or addr).strip()
            if not bare or bare.lower() in exclude:
                continue
            partner = Partner.search(
                [("email", "=ilike", bare)], limit=1,
            )
            if not partner:
                partner = Partner.search(
                    [("email_normalized", "=", bare.lower())], limit=1,
                )
            if not partner:
                try:
                    partner = Partner.create({
                        "name": _name or bare,
                        "email": bare,
                    })
                except Exception:
                    continue
            if (partner.id not in cc_ids
                    and partner.id not in to_partners):
                cc_ids.append(partner.id)
        return to_partners, cc_ids

    def _composer_target(self):
        """Resolve (model, res_id) for the composer.

        If the row has a chatter source record, post there. Otherwise post
        on the bf.email row itself (it inherits mail.thread): the reply
        stays with the email it answers and future responses thread back
        here. The historical fallback — the user's own res.partner — is
        gone on purpose: it silently piled every orphan conversation onto
        the user's own contact card (and correspondents' replies followed
        by References), polluting the card's chatter.
        """
        self.ensure_one()
        if self.res_model and self.res_id:
            return self.res_model, self.res_id
        return "bf.email", self.id

    def _build_reply_recipients(self):
        """Return [partner_id, ...] for a Reply.

        Inbound: the original sender. Outbound: the original To: recipients.
        Creates a transient res.partner for unknown emails so the composer
        can render the recipient chip.
        """
        self.ensure_one()
        Partner = self.env["res.partner"].sudo()
        addrs = []
        if self.direction == "in" and self.email_from:
            addrs = [self.email_from.strip()]
        elif self.direction == "out" and self.email_to:
            addrs = [a.strip() for a in self.email_to.split(",") if a.strip()]
        elif self.partner_id:
            return [self.partner_id.id]

        ids = []
        for addr in addrs:
            display_name, bare = parseaddr(addr)
            bare = (bare or addr).strip()
            if not bare:
                continue
            partner = Partner.search([("email", "=ilike", bare)], limit=1)
            if not partner:
                partner = Partner.search(
                    [("email_normalized", "=", bare.lower())], limit=1,
                )
            if not partner:
                try:
                    partner = Partner.create({
                        "name": display_name or bare,
                        "email": bare,
                    })
                except Exception:
                    continue
            if partner.id not in ids:
                ids.append(partner.id)
        return ids

    def _compose_signature_block(self):
        """Editable landing line + the current user's signature.

        Mirrors the top of ``mail.message._prep_quoted_reply_body`` so the
        composer always opens with a cursor above the quote and the user's
        signature present \u2014 even for orphan IMAP rows that have no chatter
        message to delegate to. ``signature`` is the same field the multi-company
        signature module renders, so replies pick up the normalized signature.
        """
        self.ensure_one()
        return (
            '<p style="margin:0 0 12px 0;"><br/></p>'
            f'{self.env.user.signature or ""}'
        )

    def _build_reply_quote_body(self):
        """Build the quoted-reply HTML for the composer."""
        self.ensure_one()
        if self.mail_message_id:
            try:
                return self.mail_message_id._prep_quoted_reply_body() or ""
            except Exception:
                pass
        # Orphan IMAP rows hold raw email HTML (only NUL-scrubbed, never
        # sanitized): full documents, <style> blocks, Outlook/mso cruft and
        # unclosed tags. Dropped straight into the OWL editor this corrupts
        # the DOM, swallowing the editable line + signature above and trapping
        # the cursor in the quote. Sanitize like the chatter path does.
        body = tools.html_sanitize(self.body_html or "")
        date = fields.Datetime.to_string(self.date) if self.date else ""
        sender = self.email_from or ""
        return (
            '<div>'
            f'{self._compose_signature_block()}'
            '<br/><br/>'
            '<blockquote style="border-left:3px solid #ccc;padding-left:8px;'
            'margin:8px 0;color:#666;">'
            f'<p><em>Le {date}, {sender} a \u00e9crit :</em></p>'
            f'{body}'
            '</blockquote>'
            '</div>'
        )

    def _build_forward_body(self):
        """Build the standard 'Forwarded message' wrapper for the composer."""
        self.ensure_one()
        date = fields.Datetime.to_string(self.date) if self.date else ""
        # Sanitize raw IMAP HTML before it reaches the OWL editor (see
        # _build_reply_quote_body for why).
        body = tools.html_sanitize(self.body_html or "")
        cc_line = (
            f'<br/><strong>CC&nbsp;:</strong> {self.email_cc}'
            if self.email_cc else ''
        )
        return (
            '<div>'
            f'{self._compose_signature_block()}'
            '<br/><br/>'
            '<p>---------- Forwarded message ---------- </p>'
            f'<p><strong>De&nbsp;:</strong> {self.email_from or ""}<br/>'
            f'<strong>Date&nbsp;:</strong> {date}<br/>'
            f'<strong>Objet&nbsp;:</strong> {self.subject or ""}<br/>'
            f'<strong>\u00c0&nbsp;:</strong> {self.email_to or ""}'
            f'{cc_line}'
            '</p>'
            f'<div>{body}</div>'
            '</div>'
        )

    def _extract_orphan_attachments(self):
        """Materialize attachments from raw_rfc822 as ir.attachment ids.

        Used by Forward on orphan IMAP rows \u2014 the composer expects
        ir.attachment IDs, so we create them on the fly bound to the
        bf.email row.
        """
        self.ensure_one()
        if not self.raw_rfc822:
            return []
        try:
            raw = base64.b64decode(self.raw_rfc822)
        except Exception:
            return []
        try:
            parsed = email_mod.message_from_bytes(raw, policy=email.policy.default)
        except Exception:
            return []
        items = bf_email_imap.extract_attachments(parsed)
        if not items:
            return []
        Attachment = self.env["ir.attachment"].sudo()
        ids = []
        for filename, payload in items:
            try:
                att = Attachment.create({
                    "name": filename,
                    "datas": base64.b64encode(payload).decode("ascii"),
                    "res_model": self._name,
                    "res_id": self.id,
                })
                ids.append(att.id)
            except Exception:
                continue
        return ids

    # ------------------------------------------------------------------
    # .eml download
    # ------------------------------------------------------------------
    def action_download_eml(self):
        """Materialize the message as an .eml file and stream it.

        Strategy:
        - If raw_rfc822 is populated (IMAP-direct rows), decode and use it
          verbatim — preserves Received/DKIM-Signature headers.
        - Otherwise (chatter/gateway rows), reconstruct an RFC 2822 message
          from the linked mail.message + mail.message.attachment_ids.
        """
        self.ensure_one()
        eml_bytes = self._build_eml_bytes()
        attachment = self.env["ir.attachment"].sudo().create({
            "name": self._eml_filename(),
            "datas": base64.b64encode(eml_bytes).decode("ascii"),
            "mimetype": "message/rfc822",
            "res_model": self._name,
            "res_id": self.id,
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def _build_eml_bytes(self):
        """Return the .eml bytes for this row.

        Falls through to mail.message reconstruction if no raw_rfc822 and
        a linked mail.message exists. Last resort: build from bf.email
        fields directly (orphan rows without raw — should be rare).
        """
        self.ensure_one()
        if self.raw_rfc822:
            try:
                return base64.b64decode(self.raw_rfc822)
            except Exception:
                _logger.warning("bf.email %s: raw_rfc822 base64 decode failed", self.id)
        if self.mail_message_id:
            return self.env["bf.email"]._build_eml_from_mail_message(self.mail_message_id)
        return self._build_eml_from_self()

    @api.model
    def _build_eml_from_mail_message(self, message):
        """Reconstruct .eml bytes from a mail.message record.

        Preserves: From, To, Cc, Subject, Date, Message-ID, In-Reply-To,
        References. Body becomes multipart/alternative (text + HTML).
        Attachments from message.attachment_ids are included.
        """
        message.ensure_one()
        msg = email.message.EmailMessage(policy=email.policy.SMTP)

        author_email = (message.email_from or "").strip()
        if not author_email and message.author_id:
            author_email = message.author_id.email_formatted or message.author_id.email or ""
        if author_email:
            msg["From"] = author_email

        recipient_addrs = []
        for partner in message.partner_ids:
            if partner.email_formatted:
                recipient_addrs.append(partner.email_formatted)
            elif partner.email:
                recipient_addrs.append(partner.email)
        if recipient_addrs:
            msg["To"] = ", ".join(recipient_addrs)

        if message.subject:
            msg["Subject"] = message.subject

        if message.date:
            msg["Date"] = email.utils.format_datetime(
                message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
            )

        if message.message_id:
            msg["Message-ID"] = message.message_id

        if message.parent_id and message.parent_id.message_id:
            msg["In-Reply-To"] = message.parent_id.message_id
            msg["References"] = message.parent_id.message_id

        html_body = message.body or ""
        text_body = tools.html2plaintext(html_body) if html_body else ""
        msg.set_content(text_body or "")
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        for att in message.attachment_ids:
            payload = att.raw if hasattr(att, "raw") and att.raw else (
                base64.b64decode(att.datas) if att.datas else b""
            )
            if not payload:
                continue
            mimetype = att.mimetype or mimetypes.guess_type(att.name or "")[0] or "application/octet-stream"
            maintype, _, subtype = mimetype.partition("/")
            if not subtype:
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(payload, maintype=maintype, subtype=subtype,
                               filename=att.name or "attachment")

        return msg.as_bytes()

    def _build_eml_from_self(self):
        """Last-resort reconstruction from bf.email fields only.

        Used when there is no raw_rfc822 AND no linked mail.message — rare,
        but possible for some legacy or edge-case rows.
        """
        self.ensure_one()
        msg = email.message.EmailMessage(policy=email.policy.SMTP)
        if self.email_from:
            msg["From"] = self.email_from
        if self.email_to:
            msg["To"] = self.email_to
        if self.email_cc:
            msg["Cc"] = self.email_cc
        if self.subject:
            msg["Subject"] = self.subject
        if self.date:
            msg["Date"] = email.utils.format_datetime(
                self.date if self.date.tzinfo else self.date.replace(tzinfo=timezone.utc)
            )
        if self.message_id_header:
            msg["Message-ID"] = self.message_id_header
        if self.in_reply_to:
            msg["In-Reply-To"] = self.in_reply_to
        html_body = self.body_html or ""
        text_body = tools.html2plaintext(html_body) if html_body else ""
        msg.set_content(text_body or "")
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        return msg.as_bytes()

    def _eml_filename(self):
        """Build a safe filename: YYYY-MM-DD_from_subject.eml."""
        self.ensure_one()
        date_part = self.date.strftime("%Y-%m-%d") if self.date else "undated"
        _, bare = parseaddr(self.email_from or "")
        local = (bare.split("@", 1)[0] if bare else "") or "unknown"
        local = self._eml_slug(local)
        subject = self._eml_slug(self.subject or "")
        parts = [p for p in (date_part, local, subject) if p]
        stem = "_".join(parts) or f"message_{self.id}"
        return f"{stem[:120]}.eml"

    @staticmethod
    def _eml_slug(text):
        """ASCII-safe slug, max 60 chars, alphanum + dashes."""
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKD", text)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-")
        return cleaned[:60].lower()

    def action_open_in_chatter(self):
        """Navigate to the source record form (chatter visible)."""
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return False
        self.env[self.res_model].check_access_rights("read")
        self.env[self.res_model].browse(self.res_id).check_access_rule("read")
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_open_conversation(self):
        """Open the list view filtered by RFC 2822 thread root.

        When the row has no thread_root_id, fall back to subject-prefix
        matching (Re:/Fwd:-stripped) for the same partner.
        """
        self.ensure_one()
        domain = []
        if self.thread_root_id:
            domain = [("thread_root_id", "=", self.thread_root_id)]
        else:
            cleaned = re.sub(r"^(re|fwd|tr|fw)\s*:\s*", "", (self.subject or ""), flags=re.IGNORECASE).strip()
            if cleaned and self.partner_id:
                domain = [
                    ("subject", "ilike", cleaned),
                    ("partner_id", "=", self.partner_id.id),
                ]
        return {
            "type": "ir.actions.act_window",
            "name": f"Fil : {(self.subject or '')[:60]}",
            "res_model": "bf.email",
            "view_mode": "list,form",
            "domain": domain,
            "context": {"search_default_group_date": 0},
        }

    def action_reroute(self):
        """Open the reroute wizard pre-filled with selected bf.email rows."""
        if not self:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": "Importer dans un chatter",
            "res_model": "bf.email.reroute",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_bf_email_ids": [(6, 0, self.ids)],
            },
        }

    # ------------------------------------------------------------------
    # "Nouveau ▾" — create a record (task / ticket / expense / invoice)
    # FROM the email. The record is created immediately and the *email
    # itself* is imported into its chatter (rendered body + original
    # attachments + the full .eml), exactly like "Lier à un dossier" — rather
    # than dumping the body into a description/narration text field. The
    # bf.email row is then filed under the new record. Triggered by the header
    # OWL widget. If the server-side create or the chatter import raises, we
    # fall back to the legacy create-only blank form so the button is never
    # left non-functional on a live record.
    # ------------------------------------------------------------------
    def _open_create_form(self, model, name, ctx):
        """Return an act_window opening a new (res_id=False) form of ``model``
        pre-filled via the ``default_*`` keys in ``ctx``. Legacy fallback for
        ``_spawn_from_email`` when an immediate create is not possible."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "form",
            "views": [[False, "form"]],
            "target": "current",
            "context": ctx,
        }

    @api.model
    def _import_param_bool(self, key, default=True):
        """Read an ``ir.config_parameter`` boolean (off = 0/false/no/off).

        ``get_param`` returns ``False`` (not ``None``) for a missing key, so
        guard the unset case before calling string methods.
        """
        val = self.env["ir.config_parameter"].sudo().get_param(key)
        if not val:
            return default
        return str(val).strip().lower() not in ("0", "false", "no", "off")

    def _materialize_email_attachments(self, res_model, res_id):
        """Return ir.attachment ids — the email's original attachments plus
        the full reconstructed .eml — bound to ``(res_model, res_id)`` so they
        can ride along in a chatter post.

        Originals come from ``raw_rfc822`` (IMAP rows) when present, else from
        the linked ``mail.message``. The .eml preserves the complete source
        (headers included) and stays forwardable. Either part can be turned
        off via the system parameters ``bf_email.import_attach_originals`` /
        ``bf_email.import_attach_eml`` (both default on) — the .eml already
        re-contains the originals, so storage-sensitive tenants can keep one.
        """
        self.ensure_one()
        Attachment = self.env["ir.attachment"].sudo()
        ids = []
        if self._import_param_bool("bf_email.import_attach_originals", True):
            if self.raw_rfc822:
                try:
                    parsed = bf_email_imap.parse_rfc822(base64.b64decode(self.raw_rfc822))
                    for filename, content in bf_email_imap.extract_attachments(parsed):
                        att = Attachment.create({
                            "name": filename,
                            "datas": bf_email_imap.attachment_to_b64(content),
                            "res_model": res_model,
                            "res_id": res_id,
                        })
                        ids.append(att.id)
                except Exception:
                    _logger.warning(
                        "bf.email #%s: attachment extraction from raw_rfc822 failed",
                        self.id, exc_info=True,
                    )
            elif self.mail_message_id:
                for att in self.mail_message_id.attachment_ids:
                    try:
                        ids.append(att.copy({"res_model": res_model, "res_id": res_id}).id)
                    except Exception:
                        continue
        if self._import_param_bool("bf_email.import_attach_eml", True):
            try:
                eml = Attachment.create({
                    "name": self._eml_filename(),
                    "datas": base64.b64encode(self._build_eml_bytes()).decode("ascii"),
                    "mimetype": "message/rfc822",
                    "res_model": res_model,
                    "res_id": res_id,
                })
                ids.append(eml.id)
            except Exception:
                _logger.warning(
                    "bf.email #%s: .eml reconstruction failed for chatter import",
                    self.id, exc_info=True,
                )
        return ids

    def _import_into_chatter(self, target, force_file=False):
        """Post this email into ``target``'s chatter as an email-type message
        (rendered body + original attachments + the full .eml) and file the
        bf.email row under ``target``.

        Single source of truth for "import an email into a chatter": used by
        both "Nouveau ▾" (``_spawn_from_email``) and "Lier à un dossier"
        (``bf.email.reroute._reroute_one``). Returns the posted ``mail.message``
        (or ``False``).

        Filing rule: orphan rows (no linked ``mail.message``, no ``res_id``)
        are always promoted; ``force_file=True`` (reroute) promotes regardless.
        A row already linked to a chatter is never re-filed — we only post a
        copy. The Message-ID is preserved whenever the row is not yet a chatter
        message, so a later reply threads onto the target; re-using it on an
        already-linked row would duplicate the identifier across two chatters.
        """
        self.ensure_one()
        target.ensure_one()
        target_model = target._name
        target_id = target.id
        preserve_mid = not self.mail_message_id
        should_file = force_file or (not self.mail_message_id and not self.res_id)

        attachment_ids = self._materialize_email_attachments(target_model, target_id)

        post_kwargs = {
            "body": self.body_html or "",
            "subject": self.subject or "",
            "message_type": "email",
            "subtype_xmlid": "mail.mt_comment",
            "email_from": self.email_from or "",
            "author_id": self.author_id.id if self.author_id else False,
            "body_is_html": True,
        }
        if preserve_mid and self.message_id_header:
            post_kwargs["message_id"] = self.message_id_header
        if self.date:
            post_kwargs["date"] = fields.Datetime.to_string(self.date)
        if attachment_ids:
            post_kwargs["attachment_ids"] = attachment_ids

        new_msg = target.with_context(
            mail_create_nosubscribe=True,
            mail_create_nolog=True,
            mail_notify_force_send=False,
            mail_auto_subscribe_no_notify=True,
            tracking_disable=True,
        ).message_post(**post_kwargs)

        if new_msg and new_msg.body:
            fixed = bf_email_imap.unwrap_double_encoded_html(new_msg.body)
            if fixed != new_msg.body:
                new_msg.write({"body": fixed})

        if should_file:
            self.write({
                "mail_message_id": new_msg.id if new_msg else False,
                "res_model": target_model,
                "res_id": target_id,
                "record_name": (target.display_name or "")[:200],
                "source": "gateway" if self.source == "imap" else self.source,
            })
        return new_msg

    def _move_chatter_message(self, target):
        """Re-router un courriel DÉJÀ posé sur un chatter : déplacer le
        ``mail.message`` existant vers ``target``.

        Ce n'est pas un second import. Reposter le corps sur la nouvelle fiche
        laisserait la copie fautive sur l'ancienne — or c'est précisément de
        s'en débarrasser qu'il s'agit — et deux chatters porteraient le même
        Message-ID, ce que la déduplication du cron de relève lit comme un
        doublon à écarter au hasard. On déplace donc le message : c'est le même
        courriel qui change de dossier, pas un nouveau.

        Retourne le ``mail.message`` déplacé.
        """
        self.ensure_one()
        target.ensure_one()
        msg = self.mail_message_id
        if not msg:
            raise UserError(_(
                "Le courriel #%s n'a pas de message de chatter à déplacer.",
                self.id,
            ))
        target_model, target_id = target._name, target.id
        if msg.model == target_model and msg.res_id == target_id:
            raise UserError(_(
                "Le courriel est déjà classé dans « %s ».",
                target.display_name or target_model,
            ))

        # Sortir un message d'une fiche est une écriture SUR CETTE FICHE. Sans
        # ce contrôle, quiconque peut lire le courriel pourrait le retirer du
        # chatter d'un dossier auquel il n'a pas droit en écriture — et la
        # trace disparaîtrait de là où quelqu'un d'autre la cherche.
        source = False
        if msg.model and msg.res_id and msg.model in self.env:
            source = self.env[msg.model].browse(msg.res_id).exists()
            if source:
                source.check_access("write")

        # Le déplacement lui-même est en sudo : ``mail.message`` n'accorde pas
        # l'écriture à un interne ordinaire, et les deux fiches concernées
        # viennent d'être vérifiées à son nom.
        msg.sudo().write({
            "model": target_model,
            "res_id": target_id,
            "record_name": (target.display_name or "")[:200],
        })
        # Les pièces jointes suivent le message. Seules celles qui pointaient
        # vers l'ancienne fiche : une pièce partagée avec un autre
        # enregistrement n'a pas à être ré-attribuée au passage.
        if source and msg.attachment_ids:
            moving = msg.attachment_ids.filtered(
                lambda a: a.res_model == source._name and a.res_id == source.id
            )
            if moving:
                moving.sudo().write({
                    "res_model": target_model,
                    "res_id": target_id,
                })

        if source:
            # L'ancienne fiche perd un message ; sans note, elle le perd sans
            # trace, et c'est le genre de disparition qu'on passe une heure à
            # comprendre six mois plus tard.
            try:
                source.sudo()._message_log(body=Markup(
                    "<p>Courriel « %s » re-routé vers <b>%s</b>.</p>"
                ) % (self.subject or _("(sans objet)"),
                     target.display_name or f"{target_model} #{target_id}"))
            except Exception:  # pragma: no cover (défensif)
                _logger.warning(
                    "bf.email #%s: note de re-routage impossible sur %s #%s",
                    self.id, source._name, source.id, exc_info=True,
                )

        self.write({
            "res_model": target_model,
            "res_id": target_id,
            "record_name": (target.display_name or "")[:200],
        })
        return msg

    def _spawn_from_email(self, model, create_vals, name, legacy_ctx):
        """Create ``model`` from this email, import the email into the new
        record's chatter, and open the saved record.

        Falls back to the legacy create-only blank form (``legacy_ctx``) when
        the model is unavailable or the create/import raises — so the "Nouveau"
        button always does something sensible on a live record.
        """
        self.ensure_one()
        if model not in self.env:
            return self._open_create_form(model, name, legacy_ctx)
        try:
            with self.env.cr.savepoint():
                record = self.env[model].create(create_vals)
                self._import_into_chatter(record)
                # Turning the email into a record handles it: drop it out of
                # the inbox (independent of read/replied status). Reversible
                # via « Remettre en boîte ».
                if not self.is_handled:
                    self.is_handled = True
        except Exception:
            _logger.warning(
                "bf.email #%s: immediate %s create + chatter import failed; "
                "falling back to the blank form", self.id, model, exc_info=True,
            )
            return self._open_create_form(model, name, legacy_ctx)
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "res_id": record.id,
            "view_mode": "form",
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_create_task(self):
        """Create a project.task from this email and import the email into its
        chatter (no longer dumped into the task description)."""
        self.ensure_one()
        name = self.subject or _("(Sans objet)")
        create_vals = {"name": name, "partner_id": self.partner_id.id}
        legacy_ctx = {
            "default_name": name,
            "default_partner_id": self.partner_id.id,
            "default_description": self.body_html or self.body_preview or "",
        }
        return self._spawn_from_email(
            "project.task", create_vals, _("Nouvelle tâche"), legacy_ctx,
        )

    def action_create_helpdesk_ticket(self):
        """Create a helpdesk.ticket from this email and import the email into
        its chatter. ``helpdesk.ticket.description`` is required, so it is
        seeded with a pointer to the chatter rather than the raw body."""
        self.ensure_one()
        if "helpdesk.ticket" not in self.env:
            raise UserError(_("Le module Centre d'assistance n'est pas installé."))
        name = self.subject or _("(Sans objet)")
        create_vals = {
            "name": name,
            "partner_id": self.partner_id.id,
            "partner_email": self.email_from or "",
            "description": _(
                "<p>Courriel d'origine importé dans le fil de discussion "
                "ci-dessous.</p>"
            ),
        }
        legacy_ctx = {
            "default_name": name,
            "default_partner_id": self.partner_id.id,
            "default_partner_email": self.email_from or "",
            "default_description": self.body_html or self.body_preview or "",
        }
        return self._spawn_from_email(
            "helpdesk.ticket", create_vals, _("Nouveau ticket"), legacy_ctx,
        )

    def action_create_expense(self):
        """Create an hr.expense from this email and import the email into its
        chatter."""
        self.ensure_one()
        if "hr.expense" not in self.env:
            raise UserError(_("Le module Notes de frais n'est pas installé."))
        name = self.subject or _("(Sans objet)")
        create_vals = {"name": name, "employee_id": self.env.user.employee_id.id}
        legacy_ctx = {
            "default_name": name,
            "default_employee_id": self.env.user.employee_id.id,
        }
        return self._spawn_from_email(
            "hr.expense", create_vals, _("Nouvelle dépense"), legacy_ctx,
        )

    def action_create_vendor_bill(self):
        """Create a vendor bill (account.move) from this email and import the
        email into its chatter (no longer dumped into the narration)."""
        self.ensure_one()
        create_vals = {
            "move_type": "in_invoice",
            "partner_id": self.partner_id.id,
            "ref": self.subject or "",
        }
        legacy_ctx = {
            "default_move_type": "in_invoice",
            "default_partner_id": self.partner_id.id,
            "default_ref": self.subject or "",
            "default_narration": self.body_html or self.body_preview or "",
        }
        return self._spawn_from_email(
            "account.move", create_vals, _("Nouvelle facture fournisseur"), legacy_ctx,
        )

    def action_create_customer_invoice(self):
        """Create a customer invoice (account.move) from this email and import
        the email into its chatter (no longer dumped into the narration)."""
        self.ensure_one()
        create_vals = {
            "move_type": "out_invoice",
            "partner_id": self.partner_id.id,
            "invoice_origin": self.subject or "",
        }
        legacy_ctx = {
            "default_move_type": "out_invoice",
            "default_partner_id": self.partner_id.id,
            "default_invoice_origin": self.subject or "",
            "default_narration": self.body_html or self.body_preview or "",
        }
        return self._spawn_from_email(
            "account.move", create_vals, _("Nouvelle facture client"), legacy_ctx,
        )

    def action_create_crm_lead(self):
        """Create a crm.lead from this email and import the email into its
        chatter. ``crm.lead.type`` defaults itself (lead/opportunity per the
        leads feature), so a minimal create is enough."""
        self.ensure_one()
        if "crm.lead" not in self.env:
            raise UserError(_("Le module CRM n'est pas installé."))
        name = self.subject or _("(Sans objet)")
        create_vals = {
            "name": name,
            "partner_id": self.partner_id.id,
            "email_from": self.email_from or "",
        }
        legacy_ctx = {
            "default_name": name,
            "default_partner_id": self.partner_id.id,
            "default_email_from": self.email_from or "",
            "default_description": self.body_html or self.body_preview or "",
        }
        return self._spawn_from_email(
            "crm.lead", create_vals, _("Nouvelle piste"), legacy_ctx,
        )

    # ------------------------------------------------------------------
    # Cron: incremental sync from mail.message
    # ------------------------------------------------------------------
    @api.model
    def _cron_sync_emails(self):
        """Sync new mail.message records of type 'email' into bf.email.

        Watermark uses ``create_date`` (insertion time), not ``date``
        (sender's send time). Backdated imports (manual IMAP imports,
        forwarded emails with original send dates) would otherwise fall
        below the watermark and never get picked up.

        Per-recipient fan-out (18.0.6.0.0): each message is projected once
        per involved internal user (author + notified recipients), each row
        owned by that user, so every user's unified inbox sees the chatter/
        gateway traffic that concerns them. Messages involving no internal
        user fall back to the cron user (nothing is lost).
        """
        ICP = self.env["ir.config_parameter"].sudo()
        last_sync = ICP.get_param("bf_email.last_sync_date", "2000-01-01 00:00:00")
        batch_size = int(ICP.get_param("bf_email.sync_batch_size", "200"))

        # ``>=`` (not ``>``): create_date is not unique. A bulk import can
        # insert a whole thread at one identical timestamp; with strict ``>``,
        # once the watermark lands on that exact second every sibling message
        # is skipped *permanently* (never retried) — the cause of the missing
        # task #6557 cluster. ``>=`` re-scans the boundary timestamp each run;
        # _should_sync dedups by (message_id, user) so no duplicate is created,
        # and the cluster size is always far below batch_size in practice.
        messages = self.env["mail.message"].sudo().search(
            [
                ("create_date", ">=", last_sync),
                "|",
                    ("message_type", "=", "email"),
                    "&",
                        ("message_type", "=", "comment"),
                        ("notification_ids.notification_type", "=", "email"),
            ],
            limit=batch_size,
            order="create_date asc",
        )

        if not messages:
            return

        created = 0
        skipped = 0
        latest_date = last_sync

        for msg in messages:
            msg_date_str = fields.Datetime.to_string(msg.create_date)
            if msg_date_str > latest_date:
                latest_date = msg_date_str

            for target in self._route_target_users(msg):
                # Run dedup + vals + create in the target's environment so
                # user_id/company_id defaults, direction (env.user-relative)
                # and rule application all belong to the row owner — same
                # pattern as _sync_account for IMAP rows.
                #
                # NOT with_company(): that one *prepends* to
                # allowed_company_ids instead of replacing it, so the
                # caller's own companies stay in the context. Run from the
                # web UI with several companies enabled, env.company then
                # raises AccessError ("Access to unauthorized or invalid
                # companies.") on every target who isn't in all of them —
                # in _prepare_email_vals, outside the try below, so the
                # whole sync aborts. The cron never hit it: it runs with an
                # empty allowed_company_ids.
                BfTarget = self.with_user(target).with_context(
                    allowed_company_ids=target.company_id.ids
                )
                if not BfTarget._should_sync(msg):
                    skipped += 1
                    continue

                vals = BfTarget._prepare_email_vals(msg)
                if not vals:
                    skipped += 1
                    continue

                try:
                    with self.env.cr.savepoint():
                        BfTarget.with_context(
                            mail_create_nosubscribe=True,
                            tracking_disable=True,
                        ).create(vals)
                    created += 1
                except Exception:
                    _logger.warning(
                        "Failed to sync mail.message %s for user %s",
                        msg.id, target.id, exc_info=True,
                    )
                    skipped += 1

        ICP.set_param("bf_email.last_sync_date", latest_date)
        _logger.info(
            "bf.email sync: %d created, %d skipped (from %d messages)",
            created,
            skipped,
            len(messages),
        )

    @api.model
    def _route_target_users(self, msg):
        """Internal users who should own a bf.email projection of ``msg``.

        = the internal user behind the author (their outbound copy) plus
        every internal user notified on the message (their inbound copy).
        Excludes portal/share users, inactive users, OdooBot and the uids
        listed in ICP ``bf_email.route_exclude_user_ids`` (comma-separated
        — service accounts like the meeting-processor API user shouldn't
        accumulate inbox rows nobody reads; same knob name as the reference
        copy of this module).

        When no internal user is *notified* — the classic case being an
        inbound customer reply that Odoo logs on a record as a bare "Note"
        (subtype ``mail.mt_note``), which notifies nobody: invoice
        (``account.move``) replies come in this way, unlike task replies
        which arrive as "Discussion" and do notify followers — fall back
        to the internal **followers of the underlying record**
        (``model``/``res_id``). This routes the reply to the people
        actually responsible for it (the invoice's salesperson, the task's
        followers…) instead of dumping every unattributable message on the
        cron runner. Only genuine orphans — no linked record, or a record
        with no internal follower (bounces, third-party notifications) —
        fall through to the current (cron) user, so nothing is ever lost.

        Rows are created ``with_user(target)`` so direction/dedup/rules are
        per-owner and orphan outbound is kept (fallback), not dropped.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email.route_exclude_user_ids", ""
        )
        excluded = {
            int(tok) for tok in raw.replace(";", ",").split(",")
            if tok.strip().isdigit()
        }
        excluded.add(1)  # OdooBot / superuser

        def _internal(partners):
            return partners.user_ids.filtered(
                lambda u: u.active and not u.share and u.id not in excluded
            )

        partners = msg.author_id | msg.notification_ids.res_partner_id
        users = _internal(partners)

        # Nobody internal was notified: attribute the row to the internal
        # followers of the record the message lives on, rather than the cron
        # runner. Best-effort and fully guarded (uninstalled model, non-
        # mail.thread target, deleted record) — a lookup failure must never
        # break the projection cron, which calls this outside its try/except.
        if not users and msg.model and msg.res_id and msg.model in self.env:
            try:
                record = self.env[msg.model].sudo().browse(msg.res_id).exists()
                if record and "message_follower_ids" in record._fields:
                    users = _internal(record.message_follower_ids.partner_id)
            except Exception:  # pragma: no cover - defensive
                _logger.warning(
                    "bf.email: follower fallback failed for %s,%s",
                    msg.model, msg.res_id, exc_info=True,
                )

        return users or self.env.user

    @api.model
    def _should_sync(self, msg):
        """Check if a mail.message should be synced (dedup).

        Three outcomes:
        - Returns ``False`` if a non-orphan row already represents this
          Message-ID (true duplicate, skip).
        - Returns ``False`` after PROMOTING an IMAP-orphan row (source='imap',
          ``mail_message_id=False``) to a chatter row by linking the new
          ``mail.message``. Preserves the original UNIQUE row.
        - Returns ``True`` if no row exists yet — caller should ``create``.

        Includes archived records via active_test=False — the UNIQUE
        constraint on (message_id_header, company_id) spans all rows
        regardless of active, so we must match that scope to avoid
        IntegrityError when a partner later archives and we re-sync.
        """
        if not msg.message_id:
            return True

        # Dedup by (message_id_header, user_id) — the UNIQUE constraint
        # spans (message_id_header, company_id, user_id) so per-user dedup
        # matches what would IntegrityError on create.
        existing = self.with_context(active_test=False).sudo().search([
            ("message_id_header", "=", msg.message_id),
            ("user_id", "=", self.env.uid),
        ], limit=1)
        if not existing:
            return True

        # Promote IMAP-orphan to chatter row if a real mail.message just landed.
        if existing.source == "imap" and not existing.mail_message_id:
            try:
                vals = self._prepare_email_vals(msg)
                if vals:
                    promote = {
                        "mail_message_id": msg.id,
                        "res_model": vals.get("res_model") or False,
                        "res_id": vals.get("res_id") or False,
                        "record_name": vals.get("record_name") or "",
                        "source": vals.get("source") or "gateway",
                        "partner_id": vals.get("partner_id") or existing.partner_id.id or False,
                        "author_id": vals.get("author_id") or existing.author_id.id or False,
                        "email_to": vals.get("email_to") or existing.email_to or "",
                        "email_cc": vals.get("email_cc") or existing.email_cc or "",
                        "has_attachments": vals.get("has_attachments") or existing.has_attachments,
                        "attachment_count": vals.get("attachment_count") or existing.attachment_count,
                        "in_reply_to": vals.get("in_reply_to") or existing.in_reply_to or False,
                        "thread_root_id": vals.get("thread_root_id") or existing.thread_root_id or False,
                    }
                    existing.write(promote)
                    _logger.info(
                        "bf.email: promoted IMAP-orphan #%s to chatter (mail.message #%s)",
                        existing.id, msg.id,
                    )
            except Exception:
                _logger.warning(
                    "bf.email: failed to promote orphan #%s for mail.message #%s",
                    existing.id, msg.id, exc_info=True,
                )
        return False

    @api.model
    def _prepare_email_vals(self, msg):
        """Build bf.email values dict from a mail.message record."""
        direction = self._detect_direction(msg)

        record_name = ""
        if msg.res_id and msg.model:
            try:
                record = self.env[msg.model].sudo().browse(msg.res_id)
                if record.exists():
                    record_name = record.display_name or ""
            except Exception:
                pass

        attachment_ids = msg.attachment_ids

        # email_to: use mail.message.email_to if available, else build from partners
        email_to_str = ""
        if hasattr(msg, "email_to") and msg.email_to:
            email_to_str = msg.email_to
        elif msg.partner_ids:
            email_to_str = ", ".join(
                p.email for p in msg.partner_ids if p.email
            )

        # email_cc: use mail.message.email_cc if available
        email_cc_str = ""
        if hasattr(msg, "email_cc") and msg.email_cc:
            email_cc_str = msg.email_cc

        # partner_id: main external contact
        if direction == "in":
            partner = msg.author_id or False
        else:
            # For outbound: first non-internal recipient
            partner = False
            for p in msg.partner_ids:
                if not p.user_ids:
                    partner = p
                    break
            if not partner and msg.partner_ids:
                partner = msg.partner_ids[0]

        # Drop partner / author refs whose row no longer exists. mail.message
        # holds raw int FKs and Odoo doesn't auto-null them when a partner is
        # deleted, so blindly forwarding the id would trip the bf_email FK.
        partner_id = partner.id if partner and partner.exists() else False
        author_id = msg.author_id.id if msg.author_id and msg.author_id.exists() else False

        in_reply_to = msg.parent_id.message_id if msg.parent_id else False
        # Walk parent chain for thread root; fall back to in_reply_to or self.
        thread_root = msg.message_id or False
        cursor = msg.parent_id
        seen = set()
        while cursor and cursor.id not in seen and cursor.message_id:
            seen.add(cursor.id)
            thread_root = cursor.message_id
            cursor = cursor.parent_id

        return {
            "date": msg.date,
            "email_from": msg.email_from or "",
            "email_to": email_to_str,
            "email_cc": email_cc_str,
            "subject": msg.subject or "",
            "direction": direction,
            "source": "gateway" if msg.message_type == "email" else "chatter",
            "message_id_header": msg.message_id or False,
            "in_reply_to": in_reply_to,
            "thread_root_id": thread_root,
            "mail_message_id": msg.id,
            "res_model": msg.model or False,
            "res_id": msg.res_id or False,
            "record_name": record_name[:200],
            "partner_id": partner_id,
            "author_id": author_id,
            "has_attachments": bool(attachment_ids),
            "attachment_count": len(attachment_ids),
            "company_id": self.env.company.id,
        }

    @api.model
    def _detect_direction(self, msg):
        """Detect if a message is inbound or outbound.

        Relative to ``env.user`` (the row owner being projected): a message
        is outbound only when *I* authored it. A colleague's message is
        inbound for me even though its author is an internal user —
        required for the per-recipient fan-out of ``_cron_sync_emails``.
        """
        if msg.author_id and self.env.user in msg.author_id.user_ids:
            return "out"
        return "in"

    # ------------------------------------------------------------------
    # Real-time wake-up (IMAP IDLE watcher)
    # ------------------------------------------------------------------
    @api.model
    def imap_wake(self, reason=False):
        """Run the IMAP ingestion now instead of waiting for the 5-minute cron.

        Meant to be called over XML-RPC by an external IMAP IDLE watcher: one
        process holding an IDLE connection per active account, firing this the
        moment the mail server announces an arrival. All it does is ask the
        scheduler to run ``ir_cron_sync_imap`` immediately — ``_trigger``
        writes an ``ir.cron.trigger`` row and NOTIFYs the cron worker, which is
        parked in ``select()`` on ``LISTEN cron_trigger`` and wakes within a
        second.

        Deliberately *not* a direct call to ``_cron_sync_imap``. Going through
        the scheduler keeps ingestion inside the single cron worker, so a wake
        can never run alongside the five-minute pass — ``_acquire_one_job``
        takes the row ``FOR NO KEY UPDATE SKIP LOCKED``. The watcher is an
        accelerator and never a second ingestion path: if it dies, the cron
        keeps its own schedule and nothing is lost but latency.

        Public (no leading underscore) because Odoo refuses RPC on private
        methods, and open to any internal user: the only thing it can do is
        make a cron that was going to run anyway run sooner. It returns no
        data. Repeated calls cannot pile up work — the scheduler runs the job
        one at a time — they can only keep it busy, which is bounded by how
        long one ingestion pass takes.

        No dedup guard on purpose. Skipping a wake because a trigger is
        already pending looks safe and is not: ``_reschedule_later`` deletes
        pending triggers *after* the job completes, so a message arriving
        while the pass is running would be skipped and then wait for the next
        scheduled pass.
        """
        cron = self.env.ref(
            "bf_email_management.ir_cron_sync_imap", raise_if_not_found=False
        )
        if not cron:
            _logger.warning("bf.email: réveil IMAP demandé, cron introuvable")
            return False
        cron = cron.sudo()
        if not cron.active:
            return False
        cron._trigger()
        # ``reason`` vient de l'appelant : un saut de ligne y forgerait des
        # lignes de journal, et une chaîne longue noierait le fichier.
        label = re.sub(r"\s+", " ", str(reason))[:120] if reason else "sans motif"
        _logger.info("bf.email: réveil IMAP déclenché (%s)", label)
        return True

    # ------------------------------------------------------------------
    # Cron: incremental sync from IMAP (Inbox + Sent live)
    # ------------------------------------------------------------------
    @api.model
    def _cron_sync_imap(self):
        """Pull new messages from IMAP for every active bf.email.account.

        Iterates over each active account, opening a separate IMAP connection
        per account, executing the sync in the owner's environment so that
        new ``bf.email`` rows inherit ``user_id`` and ``company_id``.
        Watermarks are stored on the account row itself.
        """
        Account = self.env["bf.email.account"].sudo()
        accounts = Account.search([("active", "=", True)])
        if not accounts:
            _logger.debug("bf.email: no active IMAP accounts, skipping cron")
            return
        for account in accounts:
            try:
                self._sync_account(account)
            except Exception as exc:
                account.write({"state": "error", "last_error": str(exc)})
                _logger.warning(
                    "bf.email IMAP cron account %s failed: %s",
                    account.display_name, exc, exc_info=True,
                )

    @api.model
    def _sync_account(self, account):
        """Pull new IMAP messages for one account, advance watermarks."""
        if not (account.host and account.login and account.password):
            return
        try:
            conn = bf_email_imap.open_connection(
                account.host, account.port, account.login, account.password,
            )
        except bf_email_imap.ImapConnectionError as exc:
            account.write({"state": "error", "last_error": str(exc)})
            _logger.warning(
                "bf.email IMAP (%s): %s", account.display_name, exc,
            )
            return

        # Run the sync in the account owner's environment so create() vals
        # default user_id/company_id correctly via env.user / env.company.
        # allowed_company_ids is *replaced*, not with_company()'d — see the
        # note in _cron_sync_emails.
        owner_env = self.with_user(account.user_id).with_context(
            allowed_company_ids=account.user_id.company_id.ids
        ).env
        BfEmail = owner_env["bf.email"]
        try:
            for folder in bf_email_imap.DEFAULT_LIVE_FOLDERS:
                BfEmail._sync_imap_folder(conn, folder, account)
            account.write({
                "state": "connected",
                "last_error": False,
                "last_sync_date": fields.Datetime.now(),
            })
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    @api.model
    def _cron_imap_reconcile(self, days=None, folders=None):
        """Watermark-independent gap-filler for IMAP capture.

        The live paths advance forward-only watermarks (``last_uid_sent`` /
        ``last_uid_inbox`` by UID, ``bf_email.last_sync_date`` by create_date)
        and move *past* anything they skip, fail on, or boundary-collide with
        — so a single missed message becomes a permanent hole that is never
        retried. This pass re-scans the last ``days`` of the live folders and
        ingests any message whose Message-ID has no ``bf.email`` row for the
        owner, regardless of watermark.

        Capture-side only: read-only IMAP (EXAMINE), no COPY/EXPUNGE/writeback.
        Idempotent — dedup on (message_id_header, user_id) means a second run
        finds nothing to do. ``days`` / ``folders`` override the defaults for
        a one-shot recovery (e.g. ``_cron_imap_reconcile(days=60)``).
        """
        ICP = self.env["ir.config_parameter"].sudo()
        lookback = days if days is not None else int(
            ICP.get_param("bf_email.reconcile_days", "30")
        )
        target_folders = folders or list(bf_email_imap.DEFAULT_LIVE_FOLDERS)
        since = (fields.Datetime.now() - timedelta(days=lookback)).date()

        Account = self.env["bf.email.account"].sudo()
        accounts = Account.search([("active", "=", True)])
        total = 0
        for account in accounts:
            if not (account.host and account.login and account.password):
                continue
            try:
                conn = bf_email_imap.open_connection(
                    account.host, account.port, account.login, account.password,
                )
            except bf_email_imap.ImapConnectionError as exc:
                _logger.warning(
                    "bf.email reconcile (%s): %s", account.display_name, exc,
                )
                continue

            # Owner environment so any new row inherits user_id / company_id.
            owner_env = self.with_user(account.user_id).with_context(
                allowed_company_ids=account.user_id.company_id.ids
            ).env
            BfEmail = owner_env["bf.email"]

            recovered = 0
            try:
                for folder in target_folders:
                    if not bf_email_imap.select_folder(conn, folder, readonly=True):
                        continue
                    uids = bf_email_imap.search_uids_in_range(conn, date_from=since)
                    if not uids:
                        continue
                    for i in range(0, len(uids), 200):
                        chunk = uids[i:i + 200]
                        headers = bf_email_imap.fetch_headers_bulk(conn, chunk)
                        for uid, (msg, _seen) in headers.items():
                            message_id = str(msg.get("Message-ID", "")).strip()
                            if not message_id:
                                continue
                            existing = BfEmail.with_context(
                                active_test=False
                            ).search([
                                ("message_id_header", "=", message_id),
                                ("user_id", "=", account.user_id.id),
                            ], limit=1)
                            if existing:
                                continue
                            raw = bf_email_imap.fetch_rfc822(conn, uid)
                            if not raw:
                                continue
                            try:
                                with self.env.cr.savepoint():
                                    if BfEmail._ingest_rfc822(
                                        raw, uid, folder, account
                                    ):
                                        recovered += 1
                            except Exception:
                                _logger.warning(
                                    "bf.email reconcile: ingest failed "
                                    "UID %s in %r", uid, folder, exc_info=True,
                                )
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

            if recovered:
                _logger.info(
                    "bf.email reconcile (%s): recovered %d missing message(s) "
                    "over last %d day(s)",
                    account.display_name, recovered, lookback,
                )
            total += recovered

        if total:
            _logger.info(
                "bf.email reconcile: recovered %d missing message(s) total", total
            )
        return total

    @api.model
    def _cron_imap_mirror(self):
        """Reconcile bf.email.imap_in_inbox against each account's live INBOX.

        Runs every 5 minutes. For each active account, fetch the live INBOX
        UID set and flip ``imap_in_inbox`` on the owner's rows whose
        ``imap_folder='INBOX'`` and ``date >= now-90d``.

        Also wakes snoozed rows whose ``snoozed_until`` has passed (global
        pass, no IMAP needed).
        """
        # ---- Snooze wake-up first (cheap, no IMAP). Sudo because the cron
        #      runs as admin but rows belong to many users. ----
        now = fields.Datetime.now()
        # active_test=False : une ligne désactivée (héritage de l'ancien
        # « Archiver après import ») reste invisible aux recherches ordinaires,
        # donc ni son réveil ni son miroir IMAP ne tournaient jamais. Son
        # état IMAP restait figé sur la valeur du jour où elle a disparu.
        Rows = self.sudo().with_context(active_test=False)
        woken = Rows.search([
            ("snoozed_until", "!=", False),
            ("snoozed_until", "<=", now),
            ("is_handled", "=", True),
        ])
        if woken:
            woken.write({"is_handled": False, "snoozed_until": False})
            _logger.info("bf.email: woke %s snoozed rows", len(woken))

        # ---- IMAP mirror pass: one connection per active account. ----
        Account = self.env["bf.email.account"].sudo()
        accounts = Account.search([("active", "=", True)])
        cutoff = fields.Datetime.now() - timedelta(days=90)
        for account in accounts:
            if not (account.host and account.login and account.password):
                continue
            try:
                conn = bf_email_imap.open_connection(
                    account.host, account.port, account.login, account.password,
                )
            except bf_email_imap.ImapConnectionError as exc:
                _logger.warning(
                    "bf.email IMAP mirror (%s): %s", account.display_name, exc,
                )
                continue
            try:
                if not bf_email_imap.select_folder(conn, "INBOX", readonly=True):
                    continue
                status, data = conn.uid("SEARCH", None, "ALL")
                if status != "OK" or not data or not data[0]:
                    live_uids = set()
                else:
                    raw = data[0]
                    if isinstance(raw, bytes):
                        raw = raw.decode("ascii", errors="ignore")
                    live_uids = {x for x in raw.split() if x.isdigit()}
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

            rows = Rows.search([
                ("account_id", "=", account.id),
                ("imap_folder", "ilike", "INBOX"),
                ("date", ">=", cutoff),
                ("imap_uid", "!=", False),
            ])
            flipped_in = flipped_out = auto_handled = 0
            now = fields.Datetime.now()
            for row in rows:
                in_inbox = str(row.imap_uid) in live_uids
                if in_inbox == row.imap_in_inbox:
                    continue
                vals = {"imap_in_inbox": in_inbox}
                if in_inbox:
                    flipped_in += 1
                else:
                    flipped_out += 1
                    if not row.is_handled:
                        vals["is_handled"] = True
                        vals["handled_at"] = now
                        auto_handled += 1
                row.write(vals)
            if flipped_in or flipped_out:
                _logger.info(
                    "bf.email IMAP mirror (%s): %s in, %s out (%s auto-Traité)",
                    account.display_name, flipped_in, flipped_out, auto_handled,
                )

    @api.model
    def _cron_imap_writeback_sweep(self, dry_run=False):
        """Sens Odoo → IMAP : ce qui est traité ici doit sortir de l'INBOX là-bas.

        ``_cron_imap_reconcile`` couvre la capture (IMAP → Odoo) et
        ``_cron_imap_mirror`` recopie l'état du serveur vers les lignes
        (IMAP → Odoo lui aussi). Personne ne surveillait la direction
        inverse : une ligne marquée traitée par un chemin qui n'appelle pas
        ``action_archive`` — ou dont la recopie IMAP a échoué sur un incident
        réseau, un quota, un dossier absent — laisse le message dans l'INBOX
        pour de bon, sans aucune trace, parce que la recopie n'est jamais
        retentée.

        Le balayage part de la vérité du serveur : on lit les Message-ID
        réellement présents dans l'INBOX, on retrouve la ligne du
        propriétaire, et on rejoue ``_imap_writeback_archive`` sur celles qui
        sont traitées. Borné par la taille de l'INBOX, donc bon marché sur une
        boîte tenue à l'Inbox Zero. Idempotent.

        ``dry_run=True`` compte sans rien déplacer — c'est le rapport d'écart.
        Retourne ``{account_id: [bf.email ids]}``.
        """
        Account = self.env["bf.email.account"].sudo()
        accounts = Account.search([
            ("active", "=", True), ("writeback_archive", "=", True),
        ])
        gaps = {}
        for account in accounts:
            if not (account.host and account.login and account.password):
                continue
            try:
                conn = bf_email_imap.open_connection(
                    account.host, account.port, account.login, account.password,
                )
            except bf_email_imap.ImapConnectionError as exc:
                _logger.warning(
                    "bf.email writeback sweep (%s): %s",
                    account.display_name, exc,
                )
                continue
            live_ids = []
            try:
                if not bf_email_imap.select_folder(conn, "INBOX", readonly=True):
                    continue
                uids = bf_email_imap.search_uids_in_range(conn)
                for i in range(0, len(uids), 200):
                    headers = bf_email_imap.fetch_headers_bulk(conn, uids[i:i + 200])
                    for _uid, (msg, _seen) in headers.items():
                        mid = str(msg.get("Message-ID", "")).strip()
                        if mid:
                            live_ids.append(mid)
            except Exception:
                _logger.warning(
                    "bf.email writeback sweep (%s): lecture INBOX échouée",
                    account.display_name, exc_info=True,
                )
                continue
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

            if not live_ids:
                continue
            # active_test=False : les lignes désactivées sont précisément
            # celles que personne ne peut plus rattraper à la main.
            stale = self.sudo().with_context(active_test=False).search([
                ("message_id_header", "in", live_ids),
                ("user_id", "=", account.user_id.id),
                ("is_handled", "=", True),
            ])
            if not stale:
                continue
            # Une ligne née du chatter n'a pas d'account_id, et
            # ``_imap_writeback_archive`` saute tout ce qui n'en a pas. Or on
            # vient de constater que sa copie physique est bien dans l'INBOX
            # de ce compte : on l'y rattache, sinon le message est
            # définitivement hors de portée de la recopie.
            orphans = stale.filtered(lambda r: not r.account_id)
            if orphans and not dry_run:
                orphans.write({"account_id": account.id})
            gaps[account.id] = stale.ids
            _logger.info(
                "bf.email writeback sweep (%s): %s courriel(s) traité(s) "
                "encore dans l'INBOX%s",
                account.display_name, len(stale),
                " (simulation)" if dry_run else "",
            )
            if dry_run:
                continue
            try:
                stale._imap_writeback_archive()
            except Exception:
                _logger.warning(
                    "bf.email writeback sweep (%s): recopie échouée",
                    account.display_name, exc_info=True,
                )
        return gaps

    @api.model
    def _sync_imap_folder(self, conn, folder, account):
        """Pull ``account.batch_size`` new UIDs from one folder, advance watermark.

        The watermark lives on the account row (``last_uid_inbox``,
        ``last_uid_sent``). Runs in the account owner's environment so
        created rows inherit user_id/company_id from env.
        """
        last_uid = account.get_watermark(folder)
        batch_size = account.batch_size or 100

        if not bf_email_imap.select_folder(conn, folder, readonly=True):
            _logger.info("bf.email IMAP: folder %r not selectable, skipping", folder)
            return

        uids = bf_email_imap.search_uids_above(conn, last_uid)
        if not uids:
            return
        uids = uids[:batch_size]

        created = 0
        skipped = 0
        latest_uid = int(last_uid) if last_uid else 0
        for uid in uids:
            raw = bf_email_imap.fetch_rfc822(conn, uid)
            if not raw:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    if self._ingest_rfc822(raw, uid, folder, account):
                        created += 1
                    else:
                        skipped += 1
            except Exception:
                _logger.warning(
                    "bf.email IMAP: failed to ingest UID %s in %r",
                    uid, folder, exc_info=True,
                )
                skipped += 1
            if uid > latest_uid:
                latest_uid = uid

        if latest_uid > (int(last_uid) if last_uid else 0):
            account.sudo().set_watermark(folder, latest_uid)
        _logger.info(
            "bf.email IMAP %s (%s): %d created, %d skipped (UIDs %d-%d)",
            folder, account.display_name, created, skipped, uids[0], uids[-1],
        )

    @api.model
    def _ingest_rfc822(self, raw_bytes, uid, folder, account):
        """Parse one RFC 2822 message and create an orphan bf.email row.

        Returns ``True`` when a row was created, ``False`` when skipped
        (dedup on Message-ID, or unparseable). ``account`` is a
        ``bf.email.account`` row whose owner becomes the new row's user_id.
        """
        msg = bf_email_imap.parse_rfc822(raw_bytes)
        message_id = str(msg.get("Message-ID", "")).strip() or False
        if not message_id:
            _logger.info(
                "bf.email IMAP: UID %s in %r has no Message-ID, skipping",
                uid, folder,
            )
            return False

        existing = self.with_context(active_test=False).search([
            ("message_id_header", "=", message_id),
            ("user_id", "=", account.user_id.id),
        ], limit=1)
        if existing:
            # Already represented (chatter, gateway, or earlier IMAP poll).
            # Backfill IMAP traceability on any source that lacks it — the
            # chatter cron may have created the row first via gateway
            # projection, and without a UID the writeback can't archive
            # the message server-side when the user clicks « Traiter ».
            #
            # ``account_id`` is part of that traceability, not an extra: both
            # ``action_archive`` and ``_imap_writeback_archive`` gate on it,
            # and so does ``_cron_imap_mirror``. A gateway row left without an
            # account is invisible to all three — the message stays in the
            # INBOX forever once the user marks the row « Traité ».
            backfill = {}
            if not existing.imap_uid:
                backfill.update({
                    "imap_uid": str(uid),
                    "imap_folder": folder,
                    "imap_in_inbox": (folder or "").upper() == "INBOX",
                })
            if not existing.account_id:
                backfill["account_id"] = account.id
            if backfill:
                existing.write(backfill)
            return False

        # Internal Odoo wins: if a mail.message with the same Message-ID
        # already exists (chatter or gateway projected previously), link
        # to it instead of creating an IMAP orphan.
        existing_msg = self.env["mail.message"].sudo().search([
            ("message_id", "=", message_id),
        ], limit=1)
        if existing_msg:
            chatter_vals = self._prepare_email_vals(existing_msg)
            if chatter_vals:
                # Augment with IMAP traceability — folder/UID kept for audit.
                chatter_vals.update({
                    "imap_uid": str(uid),
                    "imap_folder": folder,
                    "user_id": account.user_id.id,
                    "account_id": account.id,
                    "company_id": account.user_id.company_id.id,
                })
                self.with_context(
                    mail_create_nosubscribe=True,
                    tracking_disable=True,
                ).create(chatter_vals)
                self._maybe_ingest_calendar_invite(msg, account, folder)
                return True

        vals = self._prepare_imap_email_vals(msg, raw_bytes, uid, folder, account)
        if not vals:
            return False
        self.with_context(
            mail_create_nosubscribe=True,
            tracking_disable=True,
        ).create(vals)
        self._maybe_ingest_calendar_invite(msg, account, folder)
        return True

    @api.model
    def _prepare_imap_email_vals(self, msg, raw_bytes, uid, folder, account):
        """Build a bf.email vals dict from a parsed RFC 2822 message."""
        message_id = str(msg.get("Message-ID", "")).strip() or False
        subject = str(msg.get("Subject", ""))
        email_from = str(msg.get("From", ""))
        email_to = str(msg.get("To", ""))
        email_cc = str(msg.get("Cc", ""))
        date_str = bf_email_imap.parse_date(msg.get("Date"))
        in_reply_to, thread_root = bf_email_imap.parse_thread_headers(msg)

        # Direction: Sent folder = out; otherwise inbound unless From is us.
        if folder.lower() == "sent" or bf_email_imap.is_outbound_address(
            email_from, account.login
        ):
            direction = "out"
        else:
            direction = "in"

        # Resolve partner from the external party's address.
        external_addr = email_to if direction == "out" else email_from
        partner = self._resolve_partner_by_email(external_addr)
        author = self._resolve_partner_by_email(email_from)

        attachments = bf_email_imap.extract_attachments(msg)

        # Extract raw headers for heuristic signals (List-Unsubscribe etc.).
        raw_headers = ""
        try:
            raw_headers = "\n".join(
                f"{k}: {v}" for k, v in msg.items()
            )
        except Exception:
            raw_headers = ""

        return {
            "date": date_str or fields.Datetime.now(),
            "email_from": email_from,
            "email_to": email_to,
            "email_cc": email_cc,
            "subject": subject,
            "direction": direction,
            "source": "imap",
            "message_id_header": message_id,
            "in_reply_to": in_reply_to or False,
            "thread_root_id": thread_root or message_id,
            "mail_message_id": False,
            "res_model": False,
            "res_id": False,
            "record_name": "",
            "partner_id": partner.id if partner else False,
            "author_id": author.id if author else False,
            "has_attachments": bool(attachments),
            "attachment_count": len(attachments),
            "company_id": account.user_id.company_id.id,
            "user_id": account.user_id.id,
            "account_id": account.id,
            "imap_uid": str(uid),
            "imap_folder": folder,
            "imap_in_inbox": (folder or "").upper() == "INBOX",
            "raw_rfc822": bf_email_imap.attachment_to_b64(raw_bytes),
            "raw_headers": raw_headers,
        }

    @api.model
    def _resolve_partner_by_email(self, email_str):
        """Find a res.partner whose ``email`` matches the address, or False."""
        if not email_str:
            return self.env["res.partner"].browse()
        _name, bare = parseaddr(email_str)
        if not bare:
            return self.env["res.partner"].browse()
        bare = bare.strip()
        Partner = self.env["res.partner"].sudo()
        partner = Partner.search([("email", "=ilike", bare)], limit=1)
        if partner:
            return partner
        return Partner.search(
            [("email_normalized", "=", bare.lower())], limit=1
        )

    # ------------------------------------------------------------------
    # Inbound calendar invitations (iMIP) -> tentative calendar events
    # ------------------------------------------------------------------
    def _maybe_ingest_calendar_invite(self, msg, account, folder):
        """Auto-add a *tentative* calendar.event from an inbound invitation.

        Best-effort and fully guarded: any failure is logged and swallowed so
        IMAP ingestion is never interrupted. Gated by the
        ``bf_email.auto_add_calendar_invites`` config parameter (default on).

        Only inbound ``METHOD:REQUEST``/``CANCEL`` invitations addressed to the
        mailbox owner produce an event. Echoes of the owner's own events (they
        organized, then their provider mails the .ics back) are skipped, as are
        UIDs already present locally (reschedules update in place). The event
        carries ONLY the mailbox owner as attendee: the owner is also the
        organizer, so the Nextcloud CalDAV plugin has no EXTERNAL party to
        re-invite, yet the event stays visible in the owner's Odoo calendar
        (that view filters by attendee). ``show_as='free'`` keeps it
        non-blocking until the owner confirms.
        """
        try:
            ICP = self.env["ir.config_parameter"].sudo()
            if ICP.get_param(
                "bf_email.auto_add_calendar_invites", "1"
            ).strip().lower() not in ("1", "true", "yes"):
                return
            if (folder or "").strip().lower() == "sent":
                return
            owner = account.user_id
            if not owner:
                return
            events = imip.parse_imip_events(msg)
            if not events:
                return

            self_addrs = self._get_self_addresses(owner)
            CalendarEvent = self.env["calendar.event"].sudo().with_context(
                no_mail_to_attendees=True,
                mail_create_nosubscribe=True,
                mail_create_nolog=True,
                tracking_disable=True,
                dont_notify=True,
            )
            has_nc_uid = "x_nc_uid" in CalendarEvent._fields

            for ev in events:
                if ev["method"] not in imip.ACTIONABLE_METHODS:
                    continue
                # Skip echoes of our own Odoo-originated events: the owner is
                # the organizer, so it already lives in their calendar.
                if ev["organizer"] and ev["organizer"] in self_addrs:
                    continue

                uid = ev["uid"]
                domain = [("x_imip_uid", "=", uid)]
                if has_nc_uid:
                    domain = ["|", ("x_imip_uid", "=", uid),
                              ("x_nc_uid", "=", uid)]
                domain.append(("user_id", "=", owner.id))
                existing = CalendarEvent.with_context(
                    active_test=False
                ).search(domain, limit=1)

                if ev["method"] == "CANCEL":
                    if existing:
                        existing.unlink()
                    continue

                # REQUEST: only materialize when the owner is actually invited
                # (skip broadcasts / forwards where we are not an attendee).
                if ev["attendees"] and not (set(ev["attendees"]) & self_addrs):
                    continue

                if existing:
                    existing.write(self._imip_update_vals(ev))
                else:
                    CalendarEvent.create(self._imip_create_vals(ev, owner))
        except Exception:  # noqa: BLE001 - must never break ingestion
            _logger.exception(
                "bf_email: calendar invite ingestion failed (non-fatal)"
            )

    def _imip_create_vals(self, ev, owner):
        """Build calendar.event vals for a freshly received invitation."""
        return {
            "name": self._imip_event_name(ev),
            "start": ev["start"],
            "stop": ev["stop"],
            "allday": ev["allday"],
            "location": ev["location"] or "",
            "description": self._imip_description(ev),
            # Routes to the owner's default Nextcloud calendar via
            # calendar_nextcloud_sync's create() (organizer-based routing).
            "user_id": owner.id,
            # Owner-only attendee: makes the event show in the owner's Odoo
            # calendar (that view filters by attendee) while staying safe — the
            # owner is also the organizer, so there is no EXTERNAL party for
            # Nextcloud's CalDAV plugin to re-invite.
            "partner_ids": [(6, 0, [owner.partner_id.id])] if owner.partner_id else [],
            # Tentative == non-blocking until the owner confirms.
            "show_as": "free",
            "x_imip_uid": ev["uid"],
        }

    def _imip_update_vals(self, ev):
        """Vals for an updated invitation (reschedule / edited details)."""
        return {
            "name": self._imip_event_name(ev),
            "start": ev["start"],
            "stop": ev["stop"],
            "allday": ev["allday"],
            "location": ev["location"] or "",
            "active": True,
        }

    @staticmethod
    def _imip_event_name(ev):
        summary = (ev.get("summary") or "").strip() or "(sans titre)"
        return "[Tentatif] %s" % summary

    @staticmethod
    def _imip_description(ev):
        """Compose the event body: a clear tentative note + original details."""
        lines = [
            "Ajouté automatiquement depuis une invitation reçue par courriel "
            "(à confirmer).",
        ]
        if ev.get("organizer"):
            lines.append("Organisateur : %s" % ev["organizer"])
        if ev.get("description"):
            lines.append("")
            lines.append(ev["description"])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Manual sync trigger
    # ------------------------------------------------------------------
    @api.model
    def action_sync_now(self):
        """Run the sync cron immediately and show a notification with results.

        Iterates until no new messages remain (or a safety cap is reached),
        so a single click covers any backlog that exceeds the batch size.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        before = ICP.get_param("bf_email.last_sync_date", "2000-01-01 00:00:00")
        before_count = self.search_count([])

        max_iterations = 50
        for _ in range(max_iterations):
            last_before = ICP.get_param(
                "bf_email.last_sync_date", "2000-01-01 00:00:00"
            )
            self._cron_sync_emails()
            last_after = ICP.get_param(
                "bf_email.last_sync_date", "2000-01-01 00:00:00"
            )
            if last_after == last_before:
                break

        try:
            self._cron_sync_imap()
        except Exception:
            _logger.exception("bf.email action_sync_now: IMAP pull failed")

        after_count = self.search_count([])
        created = after_count - before_count
        after = ICP.get_param("bf_email.last_sync_date", before)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Synchronisation termin\u00e9e",
                "message": (
                    f"{created} nouveau(x) courriel(s) import\u00e9(s).\n"
                    f"Dernier message trait\u00e9\u00a0: {after}"
                ),
                "type": "success" if created else "info",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def get_preview_attachments(self):
        """Attachments for the list reading pane, as [{id, name, size}].

        IMAP rows carry their own ir.attachment (res_model='bf.email');
        chatter/gateway rows reuse the mail.message's attachments. Plain
        env (no sudo): whatever the user can't read is simply omitted.
        """
        self.ensure_one()
        atts = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
        ])
        if not atts and self.mail_message_id:
            try:
                atts = self.mail_message_id.attachment_ids
                atts.check_access_rule("read")
            except Exception:
                return []
        return [
            {"id": a.id, "name": a.name, "size": a.file_size}
            for a in atts
        ]

    # ------------------------------------------------------------------
    # Reminder / Activity
    # ------------------------------------------------------------------
    def action_create_reminder(self):
        """Open the activity scheduling wizard on this email record."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "mail.activity",
            "view_mode": "form",
            "views": [[False, "form"]],
            "target": "new",
            "context": {
                "default_res_model": self._name,
                "default_res_id": self.id,
                "default_summary": self.subject or "",
            },
        }

    # ------------------------------------------------------------------
    # Data repair
    # ------------------------------------------------------------------
    def action_recompute_from_source(self):
        """Re-read email_to, email_cc, partner_id from mail.message source.

        Call on existing records to fix data synced with the old logic.
        Also triggers recompute of category via partner_id change.
        """
        updated = 0
        for rec in self.filtered("mail_message_id"):
            msg = rec.mail_message_id.sudo()
            direction = rec.direction

            # email_to
            email_to_str = ""
            if hasattr(msg, "email_to") and msg.email_to:
                email_to_str = msg.email_to
            elif msg.partner_ids:
                email_to_str = ", ".join(
                    p.email for p in msg.partner_ids if p.email
                )

            # email_cc
            email_cc_str = ""
            if hasattr(msg, "email_cc") and msg.email_cc:
                email_cc_str = msg.email_cc

            # partner_id
            if direction == "in":
                partner = msg.author_id or False
            else:
                partner = False
                for p in msg.partner_ids:
                    if not p.user_ids:
                        partner = p
                        break
                if not partner and msg.partner_ids:
                    partner = msg.partner_ids[0]

            vals = {}
            if email_to_str != (rec.email_to or ""):
                vals["email_to"] = email_to_str
            if email_cc_str != (rec.email_cc or ""):
                vals["email_cc"] = email_cc_str
            new_partner_id = partner.id if partner else False
            if new_partner_id != rec.partner_id.id:
                vals["partner_id"] = new_partner_id

            if vals:
                rec.write(vals)
                updated += 1

        _logger.info(
            "bf.email recompute_from_source: %d/%d records updated",
            updated, len(self),
        )
        return updated

    # ------------------------------------------------------------------
    # OWL IMAP browser RPC surface (called from the JS client action).
    # All methods return plain dicts / lists JSON-serialisable for OWL.
    # ------------------------------------------------------------------
    @api.model
    def _imap_browser_check_folder(self, folder):
        """Reject folder names that could break out of the IMAP quoted
        string (no double quotes, no backslashes). Allows ``/`` so
        hierarchical paths like ``Archives/2026`` still pass.
        """
        if not isinstance(folder, str) or not folder:
            raise UserError(_("Nom de dossier invalide."))
        if '"' in folder or "\\" in folder or "\r" in folder or "\n" in folder:
            raise UserError(_(
                "Caractère interdit dans le nom de dossier : %s", folder,
            ))
        return folder

    @api.model
    def _imap_browser_resolve_account(self, account_id=None):
        """Return the bf.email.account to use for the OWL browser.

        Defaults to the explicit ``account_id`` if provided and owned by
        the current user; otherwise the first active account of the current
        user. Raises if nothing is available.
        """
        Account = self.env["bf.email.account"]
        if account_id:
            account = Account.search([
                ("id", "=", int(account_id)),
                ("user_id", "=", self.env.uid),
            ], limit=1)
            if account:
                return account
        account = Account.search([
            ("user_id", "=", self.env.uid),
            ("active", "=", True),
        ], limit=1, order="id")
        if not account:
            raise UserError(_(
                "Aucun compte IMAP configuré pour vous. "
                "Paramètres → Inbox unifiée → Mes comptes IMAP."
            ))
        return account

    @api.model
    def _imap_browser_open_conn(self, account_id=None):
        account = self._imap_browser_resolve_account(account_id)
        if not (account.host and account.login and account.password):
            raise UserError(_(
                "Le compte IMAP %s n'a pas d'identifiants valides.",
                account.display_name,
            ))
        return bf_email_imap.open_connection(
            account.host, account.port, account.login, account.password,
        )

    @api.model
    def imap_browser_get_folders(self):
        """Return ``[{name, has_children, total_count, unread_count}]`` from IMAP.

        Combines ``LIST`` (folder discovery) with one ``STATUS`` per folder
        to surface unread counts in the sidebar. Folders that refuse STATUS
        (e.g. ``\\Noselect`` parents like ``Archives`` on some servers)
        return ``None`` for the counts.
        """
        try:
            conn = self._imap_browser_open_conn()
        except bf_email_imap.ImapConnectionError as exc:
            raise UserError(_("Connexion IMAP impossible : %s", exc)) from exc
        try:
            status, raw = conn.list()
            folders = []
            if status == "OK" and raw:
                for line in raw:
                    if not line:
                        continue
                    decoded = (
                        line.decode("utf-8", errors="replace")
                        if isinstance(line, bytes) else line
                    )
                    tokens = decoded.rsplit(None, 1)
                    name = (
                        tokens[-1].strip().strip('"') if tokens else decoded
                    )
                    if not name:
                        continue
                    folders.append({
                        "name": name,
                        "has_children": "\\HasChildren" in decoded,
                        "noselect": "\\Noselect" in decoded,
                        "total_count": None,
                        "unread_count": None,
                    })
            # STATUS for each selectable folder. One round-trip per folder.
            for f in folders:
                if f.get("noselect"):
                    continue
                try:
                    s_status, s_data = conn.status(
                        bf_email_imap.imap_quote_mailbox(f["name"]),
                        "(MESSAGES UNSEEN)",
                    )
                    if s_status != "OK" or not s_data:
                        continue
                    raw_line = s_data[0]
                    if isinstance(raw_line, bytes):
                        raw_line = raw_line.decode("ascii", errors="ignore")
                    msgs_match = re.search(r"MESSAGES (\d+)", raw_line or "")
                    unseen_match = re.search(r"UNSEEN (\d+)", raw_line or "")
                    if msgs_match:
                        f["total_count"] = int(msgs_match.group(1))
                    if unseen_match:
                        f["unread_count"] = int(unseen_match.group(1))
                except Exception:
                    _logger.debug(
                        "imap_browser_get_folders: STATUS %s failed",
                        f["name"], exc_info=True,
                    )
            # Stable order: INBOX first, then Sent, then alphabetical.
            def sort_key(f):
                if f["name"] == "INBOX":
                    return (0, "")
                if f["name"] == "Sent":
                    return (1, "")
                return (2, f["name"])
            folders.sort(key=sort_key)
            return folders
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    @api.model
    def imap_browser_get_messages(self, folder, offset=0, limit=100):
        """Return ``{messages: [...], total: N}`` for a folder page (newest-first)."""
        self._imap_browser_check_folder(folder)
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 100), 500))
        try:
            conn = self._imap_browser_open_conn()
        except bf_email_imap.ImapConnectionError as exc:
            raise UserError(_("Connexion IMAP impossible : %s", exc)) from exc
        try:
            if not bf_email_imap.select_folder(conn, folder, readonly=True):
                raise UserError(_("Dossier introuvable : %s", folder))
            all_uids = bf_email_imap.search_uids_in_range(conn)
            total = len(all_uids)
            all_uids.reverse()  # newest first
            page_uids = all_uids[offset:offset + limit]
            headers = bf_email_imap.fetch_headers_bulk(conn, page_uids)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        # Dedup against bf.email by Message-ID in a single query.
        # User-scoped: only consider rows belonging to the current user.
        # We also collect snoozed/replied flags for the status column.
        msg_ids = [
            str(headers[u][0].get("Message-ID", "")).strip()
            for u in page_uids if u in headers
        ]
        msg_ids = [m for m in msg_ids if m]
        now = fields.Datetime.now()
        status_by_mid = {}
        if msg_ids:
            rows = self.with_context(active_test=False).sudo().search_read(
                [
                    ("message_id_header", "in", msg_ids),
                    ("user_id", "=", self.env.uid),
                ],
                ["message_id_header", "status", "snoozed_until"],
            )
            for row in rows:
                mid = row.get("message_id_header")
                if not mid:
                    continue
                status_by_mid[mid] = {
                    "already_in_bf_email": True,
                    "is_snoozed": bool(
                        row.get("snoozed_until") and row["snoozed_until"] > now
                    ),
                    "is_replied": row.get("status") == "replied",
                }

        messages = []
        for uid in page_uids:
            entry = headers.get(uid)
            if not entry:
                messages.append({
                    "uid": str(uid),
                    "date": False,
                    "from": "",
                    "sender_name": "",
                    "subject": "(impossible de lire l'en-tête)",
                    "message_id": False,
                    "already_in_bf_email": False,
                    "is_snoozed": False,
                    "is_replied": False,
                    "seen": True,
                })
                continue
            msg, seen = entry
            mid = str(msg.get("Message-ID", "")).strip() or None
            parsed_date = bf_email_imap.parse_date(msg.get("Date"))
            from_raw = str(msg.get("From", ""))
            display_name, addr = email.utils.parseaddr(from_raw)
            sender_name = display_name or addr or from_raw
            status = status_by_mid.get(mid) if mid else None
            messages.append({
                "uid": str(uid),
                "date": parsed_date or False,
                "from": from_raw[:255],
                "sender_name": sender_name[:255],
                "subject": str(msg.get("Subject", ""))[:255],
                "message_id": mid,
                "already_in_bf_email": bool(status),
                "is_snoozed": bool(status and status["is_snoozed"]),
                "is_replied": bool(status and status["is_replied"]),
                "seen": seen,
            })
        return {
            "folder": folder,
            "messages": messages,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    @api.model
    def imap_browser_get_body(self, folder, uid):
        """Return ``{subject, from, to, date, body_html, already_in_bf_email, bf_email_id}``."""
        self._imap_browser_check_folder(folder)
        if not uid:
            raise UserError(_("UID manquant."))
        try:
            conn = self._imap_browser_open_conn()
        except bf_email_imap.ImapConnectionError as exc:
            raise UserError(_("Connexion IMAP impossible : %s", exc)) from exc
        try:
            if not bf_email_imap.select_folder(conn, folder, readonly=True):
                raise UserError(_("Dossier introuvable : %s", folder))
            raw = bf_email_imap.fetch_rfc822(conn, int(uid))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        if not raw:
            raise UserError(_("Impossible de récupérer le UID %s.", uid))
        msg = bf_email_imap.parse_rfc822(raw)
        body_html, body_plain = bf_email_imap.extract_body(msg)
        body = body_html or (
            "<pre>%s</pre>" % (body_plain or "") if body_plain else ""
        )
        mid = str(msg.get("Message-ID", "")).strip() or None
        bf_email = False
        if mid:
            bf_email = self.with_context(active_test=False).sudo().search([
                ("message_id_header", "=", mid),
                ("user_id", "=", self.env.uid),
            ], limit=1)
        parsed_date = bf_email_imap.parse_date(msg.get("Date"))
        return {
            "subject": str(msg.get("Subject", ""))[:255],
            "from": str(msg.get("From", ""))[:255],
            "to": str(msg.get("To", ""))[:255],
            "date": parsed_date or False,
            "body_html": body,
            "already_in_bf_email": bool(bf_email),
            "bf_email_id": bf_email.id if bf_email else False,
            "message_id": mid,
        }

    @api.model
    def imap_browser_ingest(self, folder, uid, account_id=None):
        """Ingest a UID into bf.email via _ingest_rfc822. Returns the new id."""
        self._imap_browser_check_folder(folder)
        if not uid:
            raise UserError(_("UID manquant."))
        account = self._imap_browser_resolve_account(account_id)
        try:
            conn = bf_email_imap.open_connection(
                account.host, account.port, account.login, account.password,
            )
        except bf_email_imap.ImapConnectionError as exc:
            raise UserError(_("Connexion IMAP impossible : %s", exc)) from exc
        try:
            if not bf_email_imap.select_folder(conn, folder, readonly=True):
                raise UserError(_("Dossier introuvable : %s", folder))
            raw = bf_email_imap.fetch_rfc822(conn, int(uid))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        if not raw:
            raise UserError(_("Impossible de récupérer le UID %s.", uid))
        # Ingest in the account owner's environment so user_id/company_id
        # are derived correctly.
        owner_env = self.with_user(account.user_id)
        owner_env._ingest_rfc822(raw, int(uid), folder, account)
        # Look up the resulting row (may have existed already via dedup).
        msg = bf_email_imap.parse_rfc822(raw)
        mid = str(msg.get("Message-ID", "")).strip() or None
        bf_email_id = False
        if mid:
            row = self.with_context(active_test=False).sudo().search([
                ("message_id_header", "=", mid),
                ("user_id", "=", account.user_id.id),
            ], limit=1)
            bf_email_id = row.id if row else False
        return {"bf_email_id": bf_email_id}

    @api.model
    def imap_browser_ingest_and_reroute(self, folder, uid):
        """Ingest + return an act_window action opening the Reroute wizard."""
        result = self.imap_browser_ingest(folder, uid)
        bf_email_id = result.get("bf_email_id")
        if not bf_email_id:
            raise UserError(_(
                "Ingestion réussie mais ligne bf.email introuvable."
            ))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.email.reroute",
            "view_mode": "form",
            # Explicit `views`: this action is returned over /web/dataset/call_kw
            # (orm.call from the OWL browser), which — unlike call_button —
            # never runs it through clean_action()/generate_views(). Without
            # this key the web client's _preprocessAction does `views.map()`
            # on undefined and the reroute dialog never opens.
            "views": [[False, "form"]],
            "target": "new",
            "context": {"default_bf_email_ids": [(6, 0, [bf_email_id])]},
        }

    @api.model
    def imap_browser_quick_reroute(self, folder, uids, target_model=None):
        """Ingest one or many UIDs and open the Reroute wizard.

        ``uids`` accepts a single string/int or a list. ``target_model``
        is an optional hint (``project.task``, ``helpdesk.ticket``,
        ``res.partner``) propagated to the wizard via context so it can
        pre-fill ``target_reference`` intelligently.
        """
        if uids is None:
            raise UserError(_("UID(s) manquant(s)."))
        if not isinstance(uids, (list, tuple)):
            uids = [uids]
        bf_ids = []
        for uid in uids:
            res = self.imap_browser_ingest(folder, uid)
            bid = res.get("bf_email_id")
            if bid and bid not in bf_ids:
                bf_ids.append(bid)
        if not bf_ids:
            raise UserError(_(
                "Ingestion réussie mais aucune ligne bf.email retrouvée."
            ))
        ctx = {"default_bf_email_ids": [(6, 0, bf_ids)]}
        if target_model:
            ctx["default_target_model_hint"] = target_model
            # For res.partner, pre-set target if all rows share one partner.
            if target_model == "res.partner":
                rows = self.browse(bf_ids)
                partners = rows.mapped("partner_id")
                if len(partners) == 1 and partners:
                    ctx["default_target_reference"] = (
                        f"res.partner,{partners.id}"
                    )
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.email.reroute",
            "view_mode": "form",
            # Explicit `views` — see imap_browser_ingest_and_reroute: returned
            # over call_kw, so clean_action()/generate_views() never runs.
            "views": [[False, "form"]],
            "target": "new",
            "context": ctx,
        }

    @api.model
    def imap_browser_snooze(self, folder, uid):
        """Ingest if needed, then open the snooze wizard on the bf.email row."""
        result = self.imap_browser_ingest(folder, uid)
        bf_email_id = result.get("bf_email_id")
        if not bf_email_id:
            raise UserError(_("Ingestion requise avant de reporter."))
        return self.browse(bf_email_id).action_snooze()

    @api.model
    def imap_browser_create_activity(self, folder, uid):
        """Ingest if needed, then open mail.activity form on the bf.email row.

        Pre-fills the activity summary with the email subject and the note
        with a one-liner referencing sender + subject. The user picks the
        activity type and date in the form.
        """
        result = self.imap_browser_ingest(folder, uid)
        bf_email_id = result.get("bf_email_id")
        if not bf_email_id:
            raise UserError(_("Ingestion requise avant de créer une activité."))
        bf = self.browse(bf_email_id)
        subject = (bf.subject or "")[:80]
        from_safe = (bf.email_from or "").replace("<", "&lt;").replace(">", "&gt;")
        subject_safe = (bf.subject or "").replace("<", "&lt;").replace(">", "&gt;")
        note = (
            f"<p><b>De :</b> {from_safe}<br/>"
            f"<b>Sujet :</b> {subject_safe}</p>"
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "mail.activity",
            "view_mode": "form",
            # Explicit `views`: reached via orm.call (imap_browser_create_activity
            # in the OWL browser), which bypasses clean_action()/generate_views().
            "views": [[False, "form"]],
            "target": "new",
            "context": {
                "default_res_model": "bf.email",
                "default_res_id": bf_email_id,
                "default_summary": subject,
                "default_note": note,
                "default_user_id": self.env.uid,
            },
        }

    @api.model
    def imap_browser_reply(self, folder, uid):
        """Ingest if needed, then open the mail composer in reply mode."""
        result = self.imap_browser_ingest(folder, uid)
        bf_email_id = result.get("bf_email_id")
        if not bf_email_id:
            raise UserError(_("Ingestion requise avant de répondre."))
        return self.browse(bf_email_id).action_reply()

    @api.model
    def imap_browser_forward(self, folder, uid):
        """Ingest if needed, then open the mail composer in forward mode."""
        result = self.imap_browser_ingest(folder, uid)
        bf_email_id = result.get("bf_email_id")
        if not bf_email_id:
            raise UserError(_("Ingestion requise avant de transférer."))
        return self.browse(bf_email_id).action_forward()

    @api.model
    def imap_browser_mark_handled(self, folder, uid):
        """Ingest if needed, then run action_archive on the bf.email row.

        ``action_archive`` sets ``is_handled=True`` and (since 18.0.2.3.0
        with the writeback ICP on) COPY+EXPUNGE the message from INBOX to
        ``Archives/{YYYY}`` server-side.
        """
        result = self.imap_browser_ingest(folder, uid)
        bf_email_id = result.get("bf_email_id")
        if not bf_email_id:
            raise UserError(_("Ingestion requise avant de traiter."))
        self.browse(bf_email_id).action_archive()
        return {"bf_email_id": bf_email_id, "is_handled": True}

    @api.model
    def imap_browser_reply_all(self, folder, uid):
        """Ingest if needed, then open the mail composer in reply-all mode."""
        result = self.imap_browser_ingest(folder, uid)
        bf_email_id = result.get("bf_email_id")
        if not bf_email_id:
            raise UserError(_("Ingestion requise avant de répondre."))
        return self.browse(bf_email_id).action_reply_all()

    @api.model
    def imap_browser_move(self, folder, uid, dst_folder):
        """COPY a UID to ``dst_folder``, EXPUNGE in ``folder``.

        Used by drag-and-drop in the OWL browser. Refuses when destination
        is empty or identical to the source. Does NOT touch ``bf.email``.
        """
        self._imap_browser_check_folder(folder)
        self._imap_browser_check_folder(dst_folder)
        if not uid:
            raise UserError(_("UID manquant."))
        if dst_folder == folder:
            raise UserError(_(
                "Source et destination identiques (%s).", folder,
            ))
        try:
            conn = self._imap_browser_open_conn()
        except bf_email_imap.ImapConnectionError as exc:
            raise UserError(_("Connexion IMAP impossible : %s", exc)) from exc
        try:
            if not bf_email_imap.select_folder(conn, folder, readonly=False):
                raise UserError(_("Dossier source introuvable : %s", folder))
            try:
                uid_str = bf_email_imap.imap_uid_token(uid)
            except ValueError as exc:
                raise UserError(_("UID invalide : %s", uid)) from exc
            try:
                copy_status, _data = conn.uid(
                    "COPY", uid_str, bf_email_imap.imap_quote_mailbox(dst_folder),
                )
                if copy_status != "OK":
                    raise UserError(_(
                        "COPY refusé par le serveur (cible %s).", dst_folder,
                    ))
                conn.uid("STORE", uid_str, "+FLAGS", "(\\Deleted)")
                conn.expunge()
            except UserError:
                raise
            except Exception as exc:
                raise UserError(_(
                    "Échec du déplacement vers %s : %s", dst_folder, exc,
                )) from exc
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return {
            "moved_uid": str(uid),
            "src_folder": folder,
            "dst_folder": dst_folder,
        }

    @api.model
    def imap_browser_move_to_trash(self, folder, uid):
        """COPY a UID to Trash, then EXPUNGE in the current folder.

        Does NOT touch bf.email — the user can re-ingest from Trash if
        needed. Refuses if the current folder already starts with
        ``Trash`` (the user asked to delete a trashed message — that's
        a different operation; we don't support permanent delete here).
        """
        self._imap_browser_check_folder(folder)
        if not uid:
            raise UserError(_("UID manquant."))
        if folder.lower().startswith("trash"):
            raise UserError(_(
                "Ce message est déjà dans Trash. La suppression définitive "
                "n'est pas exposée par ce navigateur — passez par votre webmail IMAP."
            ))
        try:
            conn = self._imap_browser_open_conn()
        except bf_email_imap.ImapConnectionError as exc:
            raise UserError(_("Connexion IMAP impossible : %s", exc)) from exc
        try:
            if not bf_email_imap.select_folder(conn, folder, readonly=False):
                raise UserError(_("Dossier introuvable : %s", folder))
            try:
                uid_str = bf_email_imap.imap_uid_token(uid)
            except ValueError as exc:
                raise UserError(_("UID invalide : %s", uid)) from exc
            try:
                conn.uid("COPY", uid_str, '"Trash"')
                conn.uid("STORE", uid_str, "+FLAGS", "(\\Deleted)")
                conn.expunge()
            except Exception as exc:
                raise UserError(_(
                    "Échec du déplacement vers Trash : %s", exc,
                )) from exc
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return {"deleted_uid": str(uid), "folder": folder}
