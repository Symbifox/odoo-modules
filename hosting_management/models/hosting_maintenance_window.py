# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Fenêtres de maintenance annoncées aux clients.

Une fenêtre est une interruption PONCTUELLE qu'on annonce : un serveur, un début, une fin,
un objet et un impact rédigés pour le client. Ce n'est pas une routine
(`hosting.maintenance.schedule`) : celles-là sont internes et n'ont pas d'heure.

L'outillage qui annonce une maintenance crée la fenêtre AVANT d'envoyer l'avis et en tire le
contexte du gabarit (`_hosting_notice_context`) : la page d'état et le courriel lisent
alors la même vérité. La sourdine quotidienne des alertes (`_is_in_maintenance_window`
du service) est un autre mécanisme et ne lit pas ces fenêtres.

🔴 L'objet et l'impact sont lus par TOUS les clients dont un service est touché (page d'état,
avis). Ils ne nomment jamais un client ; les services, eux, sont filtrés par client au moment
de l'affichage.
"""
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.misc import format_datetime, format_time


GABARIT_AVIS = "hosting_management.mail_template_client_maintenance_notice"


def recharger_traduction_avis(env):
    """Recharge depuis les catalogues la SEULE traduction du gabarit d'avis de maintenance.

    Le corps d'un gabarit se traduit d'un bloc : quand son texte anglais change, la
    traduction déjà en base reste celle d'avant (une mise à jour n'écrase pas une
    traduction existante), et l'avis partait en français SANS le paragraphe neuf. Ne
    recharger que ce gabarit, et pas tout le module, préserve les retouches faites en
    base sur les autres.
    """
    from odoo.tools.translate import TranslationImporter

    langues = [code for code, _nom in env["res.lang"].get_installed() if code.split("_")[0] == "fr"]
    if not langues or not env.ref(GABARIT_AVIS, raise_if_not_found=False):
        return
    importer = TranslationImporter(env.cr, verbose=False)
    for langue in langues:
        # Comme Odoo : le catalogue de la langue de base, puis celui de la variante.
        importer.load_file("hosting_management/i18n/fr.po", langue, xmlids={GABARIT_AVIS})
        if langue != "fr":
            importer.load_file("hosting_management/i18n/%s.po" % langue, langue, xmlids={GABARIT_AVIS})
    importer.save(overwrite=True)
    env["mail.template"].invalidate_model()


def client_de(partenaire):
    """Le client commercial d'une fiche, par la règle d'Odoo, SANS lire le champ stocké
    `commercial_partner_id` : il est vide en base sur des fiches anciennes, et « vide ==
    vide » faisait citer dans l'avis d'un client les services d'un autre."""
    while partenaire and partenaire.parent_id and not partenaire.is_company:
        partenaire = partenaire.parent_id
    return partenaire


class HostingMaintenanceWindow(models.Model):
    _name = "hosting.maintenance.window"
    _description = "Maintenance window"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    name = fields.Char(
        string="Subject",
        required=True,
        tracking=True,
        help="Shown to every client concerned, on the status page and in the notice: "
        "never name a client here.",
    )
    date_start = fields.Datetime(string="Start", required=True, tracking=True)
    date_end = fields.Datetime(string="End", required=True, tracking=True)
    server_id = fields.Many2one(
        comodel_name="hosting.server",
        string="Server",
        tracking=True,
        help="Fills in the services affected: every active service on this server. "
        "Changing it again replaces the list.",
    )
    service_ids = fields.Many2many(
        comodel_name="hosting.service",
        relation="hosting_maintenance_window_service_rel",
        column1="window_id",
        column2="service_id",
        string="Services affected",
        compute="_compute_service_ids",
        store=True,
        readonly=False,
    )
    impact = fields.Text(
        string="Expected impact",
        help="Shown to every client concerned. Example: about 45 minutes of interruption "
        "within the window.",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("announced", "Announced"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="draft",
        required=True,
        tracking=True,
    )
    phase = fields.Selection(
        selection=[
            ("upcoming", "Upcoming"),
            ("in_progress", "In progress"),
            ("done", "Done"),
        ],
        string="Phase",
        compute="_compute_phase",
    )
    partner_ids = fields.Many2many(
        comodel_name="res.partner",
        string="Clients affected",
        compute="_compute_partner_ids",
        groups="hosting_management.group_hosting_manager",
    )
    note = fields.Text(string="Internal notes", help="Never shown to clients.")
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    @api.depends("server_id")
    def _compute_service_ids(self):
        for fenetre in self:
            if fenetre.server_id:
                fenetre.service_ids = self.env["hosting.service"].search([
                    ("server_id", "=", fenetre.server_id.id),
                    ("state", "=", "active"),
                ])
            elif not fenetre.service_ids:
                fenetre.service_ids = False

    @api.depends("date_start", "date_end")
    def _compute_phase(self):
        maintenant = fields.Datetime.now()
        for fenetre in self:
            if not fenetre.date_start or not fenetre.date_end or maintenant < fenetre.date_start:
                fenetre.phase = "upcoming"
            elif maintenant <= fenetre.date_end:
                fenetre.phase = "in_progress"
            else:
                fenetre.phase = "done"

    @api.depends("service_ids.partner_id")
    def _compute_partner_ids(self):
        for fenetre in self:
            clients = self.env["res.partner"]
            for partenaire in fenetre.service_ids.partner_id:
                clients |= client_de(partenaire)
            fenetre.partner_ids = clients

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for fenetre in self:
            if fenetre.date_start and fenetre.date_end and fenetre.date_end <= fenetre.date_start:
                raise ValidationError(_("A maintenance window must end after it starts."))

    def action_announce(self):
        for fenetre in self:
            if not fenetre.service_ids:
                raise ValidationError(_("A maintenance window needs at least one service affected."))
        self.write({"state": "announced"})
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})
        return True

    def action_reset_to_draft(self):
        self.write({"state": "draft"})
        return True

    # ------------------------------------------------------------------
    # Lectures pour l'avis et pour la page d'état (privées : un portail parle à call_kw)
    # ------------------------------------------------------------------

    @api.model
    def _hosting_windows_for_services(self, services, horizon_days=30):
        """Les fenêtres annoncées qui touchent ces services : toutes celles à venir, et
        celles finies depuis moins de `horizon_days` jours. Lues en sudo par l'appelant."""
        limite = fields.Datetime.now() - timedelta(days=horizon_days)
        # Les services d'une fenêtre se calculent depuis le serveur : une fenêtre neuve,
        # pas encore écrite, échappait à la recherche.
        self.sudo().flush_model(["service_ids", "state", "date_end"])
        return self.sudo().search([
            ("state", "=", "announced"),
            ("service_ids", "in", services.ids),
            ("date_end", ">=", limite),
        ], order="date_start asc, id asc")

    def _hosting_notice_context(self, partner):
        """Le contexte du gabarit d'avis pour UN destinataire : la date dans sa langue et le
        fuseau de son client, la durée de la fenêtre, et seulement les services touchés de
        son client. Une clé sans valeur n'est pas posée : le gabarit garde alors son texte
        par défaut.

        La langue est celle du DESTINATAIRE, comme le gabarit (`{{ object.lang }}`, sinon la
        langue de l'environnement) : un contact anglophone d'un client francophone recevait
        un corps anglais et une date française."""
        self.ensure_one()
        client = client_de(partner)
        tz = client.tz or partner.tz or self.env.user.tz or "America/Toronto"
        langue = partner.lang or self.env.lang
        env = self.with_context(lang=langue).env
        zone = pytz.timezone(tz)
        debut_local = pytz.utc.localize(self.date_start).astimezone(zone)
        fin_local = pytz.utc.localize(self.date_end).astimezone(zone)
        debut = format_datetime(env, self.date_start, tz=tz, dt_format="short", lang_code=langue)
        if debut_local.date() == fin_local.date():
            fin = format_time(env, self.date_end, tz=tz, time_format="short", lang_code=langue)
        else:
            fin = format_datetime(env, self.date_end, tz=tz, dt_format="short", lang_code=langue)
        minutes = int((self.date_end - self.date_start).total_seconds() // 60)
        heures, reste = divmod(minutes, 60)
        duree = ("%d h %02d" % (heures, reste)) if heures else ("%d min" % reste)
        services = self.service_ids.filtered(lambda s: client_de(s.partner_id) == client)
        ctx = {
            "maintenance_date": "%s → %s" % (debut, fin),
            "maintenance_duration": duree,
        }
        if services:
            ctx["affected_services"] = ", ".join(services.sorted("name").mapped("name"))
        if self.impact:
            ctx["expected_impact"] = self.impact
        return ctx
