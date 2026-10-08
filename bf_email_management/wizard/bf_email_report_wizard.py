"""La fenêtre « Signaler » de la boîte.

Ouverte par le bouton « Pourriel », la touche ``!`` ou l'action de liste. Elle
range toujours ; le reste (règle, hameçonnage, plaintes) se coche.
"""
import html

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfEmailReportWizard(models.TransientModel):
    _name = "bf.email.report.wizard"
    _description = "Signaler un pourriel ou un hameçonnage"

    email_ids = fields.Many2many(
        "bf.email", "bf_email_report_wizard_email_rel", "wizard_id", "email_id",
        string="Courriels", required=True)
    email_count = fields.Integer(compute="_compute_resume")
    sender_summary = fields.Char(string="Expéditeur(s)", compute="_compute_resume")
    junk_summary = fields.Char(string="Rangé dans", compute="_compute_resume")
    kind = fields.Selection(
        [("spam", "Pourriel"), ("phish", "Hameçonnage")],
        string="Nature", required=True, default="spam")
    phish_available = fields.Boolean(compute="_compute_offres")
    block_sender = fields.Boolean(
        string="Toujours ranger ses courriels dans Indésirables",
        help="Crée une règle personnelle sur l'adresse de l'expéditeur : ses "
             "prochains courriels vont droit dans Indésirables, sans avis.")
    complaints_enabled = fields.Boolean(compute="_compute_offres")
    available_authority_ids = fields.Many2many(
        "bf.email.report.authority", compute="_compute_offres")
    authority_ids = fields.Many2many(
        "bf.email.report.authority", "bf_email_report_wizard_authority_rel",
        "wizard_id", "authority_id", string="Porter plainte auprès de")
    form_help = fields.Html(
        string="Formulaires à remplir", compute="_compute_form_help",
        sanitize=False)

    @api.depends("email_ids")
    def _compute_resume(self):
        for wiz in self:
            emails = wiz.email_ids
            wiz.email_count = len(emails)
            adresses = []
            for rec in emails:
                adresse = rec._external_address()
                if adresse and adresse not in adresses:
                    adresses.append(adresse)
            wiz.sender_summary = ", ".join(adresses[:3]) + (
                _(" et %s autre(s)", len(adresses) - 3) if len(adresses) > 3 else "")
            dossiers = []
            for compte in emails.mapped("account_id"):
                junk = compte._junk_folder_name()
                if junk and junk not in dossiers:
                    dossiers.append(junk)
            wiz.junk_summary = ", ".join(dossiers) or _(
                "aucun dossier Indésirables connu : le courriel sort de la "
                "boîte sans bouger sur le serveur")

    @api.depends("email_ids", "kind")
    def _compute_offres(self):
        Authority = self.env["bf.email.report.authority"]
        for wiz in self:
            wiz.phish_available = "bf.reported.phish" in self.env
            company = (wiz.email_ids[:1].company_id or self.env.company)
            # Une plainte vise UN courriel : sur un lot, on range et on bloque,
            # on ne fabrique pas quinze plaintes d'un clic.
            wiz.complaints_enabled = bool(
                company.bf_email_complaints_enabled and len(wiz.email_ids) == 1)
            if not wiz.complaints_enabled:
                wiz.available_authority_ids = Authority
                continue
            champ = "for_phish" if wiz.kind == "phish" else "for_spam"
            wiz.available_authority_ids = Authority.search([
                (champ, "=", True),
                "|", ("company_id", "=", False), ("company_id", "=", company.id),
            ])

    @api.onchange("kind")
    def _onchange_kind(self):
        self.authority_ids = self.authority_ids & self.available_authority_ids

    @api.depends("authority_ids", "kind", "email_ids")
    def _compute_form_help(self):
        for wiz in self:
            formulaires = wiz.authority_ids.filtered(lambda a: a.channel == "form")
            if not formulaires or len(wiz.email_ids) != 1:
                wiz.form_help = False
                continue
            blocs = []
            for autorite in formulaires:
                texte = wiz.email_ids._report_complaint_text(autorite, wiz.kind)
                blocs.append(Markup(
                    "<p><strong>%s</strong> : <a href=\"%s\" target=\"_blank\" "
                    "rel=\"noopener noreferrer\">%s</a></p>"
                    "<pre class=\"o_bf_email_report_text\" style=\"white-space:pre-wrap;"
                    "max-height:16rem;overflow:auto;\">%s</pre>"
                ) % (autorite.name, autorite.url, _("ouvrir le formulaire"),
                     Markup(html.escape(texte, quote=False))))
            wiz.form_help = Markup("").join(blocs)

    def action_confirm(self):
        self.ensure_one()
        emails = self.email_ids.with_context(active_test=False).exists()
        if not emails:
            raise UserError(_("Aucun courriel à signaler."))
        emails.check_access("write")
        autorites = self.authority_ids & self.available_authority_ids
        if autorites and not self.complaints_enabled:
            autorites = autorites.browse()
        # Les pièces d'abord, le rangement ensuite : tout ce qui lit le
        # courriel (plainte, .eml, signalement) le lit avant qu'il ne parte.
        brouillons = self.env["mail.scheduled.message"]
        for autorite in autorites.filtered(lambda a: a.channel == "email"):
            brouillons |= emails._report_stage_complaint(autorite, self.kind)
        signalements = 0
        if self.kind == "phish":
            signalements = emails._report_to_awareness()
        regles = sans_dossier = 0
        # Pas de blocage sur un hameçonnage : un exercice signalé enverrait
        # les exercices suivants dans Indésirables, et la sécurité, elle,
        # retire les copies.
        if self.block_sender and self.kind == "spam":
            regles, sans_dossier = emails._report_block_senders()
        emails._report_mark(self.kind)
        morceaux = [_("%s courriel(s) rangé(s) dans Indésirables.", len(emails))]
        if regles:
            morceaux.append(_("%s règle(s) créée(s).", regles))
        if sans_dossier:
            morceaux.append(_(
                "%s adresse(s) sans règle : leur compte n'a pas de dossier "
                "Indésirables connu.", sans_dossier))
        if signalements:
            morceaux.append(_("%s signalement(s) d'hameçonnage.", signalements))
        if brouillons:
            morceaux.append(_(
                "%s plainte(s) en brouillon : dossier Brouillons, « Envoyer "
                "maintenant » après relecture.", len(brouillons)))
        return {
            "type": "ir.actions.act_window_close",
            "infos": {
                "bf_email_reported": emails.ids,
                "message": " ".join(str(m) for m in morceaux),
            },
        }
