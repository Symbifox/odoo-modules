"""Auto-provisioned personal drop pages, one per employee.

When ``bf_securetransfer.autoprovision_user_pages`` is on, creating an internal
user provisions a personal secure-transfer drop page (/to/<slug>) sending to
that employee's e-mail, and later renames / re-points / archives it as the user
changes. It is OFF by default so a shared install (e.g. Blue Fox) keeps manual
brands; a single-person install turns it on.

Service / API / bot accounts never get a page (login heuristics + the explicit
``st_no_drop_page`` opt-out + the superuser). Provisioning is wrapped so a
failure can NEVER block user create/write — the drop page is a convenience, not
a core flow. Branding stays per-person: the brand NAME is the person's, and
the public skin is resolved from the company's appointment_brand_* fields, never
the legal company name.
"""
import logging

from odoo import SUPERUSER_ID, api, fields, models
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)

# Logins that look like service / robot / API accounts — never auto-paged.
_BOT_LOGIN_HINTS = (
    "odoobot", "__system__", "api@", "-api", "api-", "n8n", "-service",
    "service@", "noreply", "no-reply", "mailer", "-bot", "bot@", "public",
)


class ResUsers(models.Model):
    _inherit = "res.users"

    st_no_drop_page = fields.Boolean(
        string="Aucune page de dépôt sécurisée",
        help="Empêche la création automatique d'une page de dépôt /to/<slug> "
             "pour cet utilisateur (comptes de service, robots, API, ou toute "
             "personne qui n'en veut pas).",
    )
    st_drop_page_brand_id = fields.Many2one(
        "secure.transfer.brand",
        string="Page de dépôt",
        compute="_compute_st_drop_page",
        help="Marque-dépôt personnelle liée à cet employé.",
    )
    st_drop_page_url = fields.Char(
        string="URL de la page de dépôt",
        compute="_compute_st_drop_page",
        help="Adresse publique native /to/<slug> de la page de dépôt de "
             "l'employé (aucun raccourcisseur).",
    )

    def _compute_st_drop_page(self):
        Brand = self.env["secure.transfer.brand"].sudo() \
            .with_context(active_test=False)
        for user in self:
            brand = Brand.search([("owner_user_id", "=", user.id)], limit=1)
            user.st_drop_page_brand_id = brand.id or False
            user.st_drop_page_url = brand.page_url if brand else False

    # ------------------------------------------------------------------ gating
    @api.model
    def _st_autoprovision_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_securetransfer.autoprovision_user_pages"
        ) in ("1", "True", "true")

    def _st_is_serviceish(self):
        """A login that reads like a bot / API / service account."""
        self.ensure_one()
        if self.id == SUPERUSER_ID:
            return True
        login = (self.login or "").lower()
        return any(hint in login for hint in _BOT_LOGIN_HINTS)

    def _st_should_have_page(self):
        """True when this user should own an active personal drop page:
        an active, internal (non-portal) human with a valid e-mail that has
        not opted out and is not a service account."""
        self.ensure_one()
        return bool(
            self.active
            and not self.share
            and not self.st_no_drop_page
            and email_normalize(self.email or "")
            and not self._st_is_serviceish()
        )

    # ------------------------------------------------------------------ sync
    def _st_sync_drop_page(self):
        """Create / update / archive each user's personal drop page so it
        matches _st_should_have_page. Idempotent; swallows errors per user so a
        provisioning glitch never blocks the surrounding create/write."""
        Brand = self.env["secure.transfer.brand"].sudo() \
            .with_context(active_test=False)
        for user in self:
            try:
                brand = Brand.search(
                    [("owner_user_id", "=", user.id)], limit=1)
                if not user._st_should_have_page():
                    if brand and brand.active:
                        brand.active = False  # archive, keep history/journal
                    continue
                email = email_normalize(user.email)
                label = "Envoi de fichiers | %s" % (user.name or email)
                if brand:
                    wanted = {
                        "active": True,
                        "fixed_recipient": email,
                        "fixed_recipient_name": user.name or "",
                        "name": label,
                    }
                    diff = {k: v for k, v in wanted.items() if brand[k] != v}
                    if diff:
                        brand.write(diff)
                else:
                    Brand.create({
                        "name": label,
                        # Person-based slug (e.g. « jane-doe »), not the
                        # « envoi-de-fichiers-… » brand label.
                        "slug": Brand._st_unique_slug(
                            user.name or email.split("@")[0]),
                        "is_default": False,
                        "active": True,
                        "tier": "paid",
                        "fixed_recipient": email,
                        "fixed_recipient_name": user.name or "",
                        "owner_user_id": user.id,
                        "company_id": user.company_id.id or self.env.company.id,
                    })
            except Exception:  # noqa: BLE001 — never block user create/write
                _logger.exception(
                    "bf_securetransfer: échec de synchro de la page de dépôt "
                    "de l'utilisateur id=%s", user.id)

    # ------------------------------------------------------------------ overrides
    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        if self._st_autoprovision_enabled():
            users._st_sync_drop_page()
        return users

    def write(self, vals):
        res = super().write(vals)
        watched = {"name", "email", "login", "active", "share",
                   "st_no_drop_page"}
        if self._st_autoprovision_enabled() and watched.intersection(vals):
            self._st_sync_drop_page()
        return res
