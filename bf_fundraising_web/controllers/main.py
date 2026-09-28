# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging
import math
import re
import threading
import time
from collections import defaultdict

from odoo import http
from odoo.http import request
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)

# Validation stricte du courriel (même motif que le formulaire public du
# soutien) : « a%@% » et autres jokers SQL sont refusés d'emblée.
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Limiteur par adresse IP des envois publics (même forme que le formulaire
# public du soutien) : l'IP est celle du pair déjà corrigée par ProxyFix.
_submit_lock = threading.Lock()
_submit_data = defaultdict(list)  # IP -> [horodatages des envois acceptés]
_SUBMIT_MAX = 5  # envois
_SUBMIT_WINDOW = 600  # par 10 minutes et par IP
_MAX_TRACKED_IPS = 10000


def _client_ip():
    try:
        return request.httprequest.remote_addr or "unknown"
    except Exception:
        return "unknown"


def _check_submit_rate_limit():
    """Vrai (et l'envoi est compté) si cette IP peut encore soumettre un don."""
    ip = _client_ip()
    now = time.monotonic()
    with _submit_lock:
        if len(_submit_data) > _MAX_TRACKED_IPS:
            _submit_data.clear()
        cutoff = now - _SUBMIT_WINDOW
        _submit_data[ip] = [t for t in _submit_data[ip] if t > cutoff]
        if len(_submit_data[ip]) >= _SUBMIT_MAX:
            return False
        _submit_data[ip].append(now)
        return True


class FundraisingWebController(http.Controller):
    def _form_render_values(self, post=None, error=None):
        return {
            "funds": request.env["bf.fund"].sudo().search([("active", "=", True)]),
            "campaigns": request.env["donation.campaign"]
            .sudo()
            .search([("active", "=", True)]),
            "values": post or {},
            "error": error or {},
        }

    @http.route(["/don"], type="http", auth="public", website=True, sitemap=True)
    def donation_form(self, **kw):
        return request.render(
            "bf_fundraising_web.donation_form", self._form_render_values(post=kw)
        )

    @http.route(
        ["/don/submit"],
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def donation_submit(self, **post):
        error = {}
        name = (post.get("name") or "").strip()
        email = (post.get("email") or "").strip()
        try:
            amount = float((post.get("amount") or "0").replace(",", "."))
        except ValueError:
            amount = 0.0
        normalized = email_normalize(email) if EMAIL_RE.match(email) else False
        if not name:
            error["name"] = True
        if not normalized:
            error["email"] = True
        # NaN et l'infini passent « amount <= 0 » : on exige un nombre fini.
        if not math.isfinite(amount) or amount <= 0:
            error["amount"] = True
        if not error and not _check_submit_rate_limit():
            _logger.info("bf_fundraising_web: limite d'envois atteinte pour %s", _client_ip())
            error["rate"] = True
        if error:
            return request.render(
                "bf_fundraising_web.donation_form",
                self._form_render_values(post=post, error=error),
            )

        Partner = request.env["res.partner"].sudo()
        # Correspondance exacte sur le courriel normalisé : aucun joker.
        partner = Partner.search([("email_normalized", "=", normalized)], limit=1)
        if not partner:
            partner = Partner.create(
                {
                    "name": name,
                    "email": email,
                    "is_constituent": True,
                    "company_type": "person",
                    "street": post.get("street") or False,
                    "city": post.get("city") or False,
                    "zip": post.get("zip") or False,
                }
            )

        def _valid_id(model, val):
            """Return the id only if it refers to an existing record, so a
            tampered/stale fund/campaign id is silently ignored rather than
            raising a foreign-key error."""
            try:
                rid = int(val) if val else 0
            except (TypeError, ValueError):
                return False
            if rid and request.env[model].sudo().browse(rid).exists():
                return rid
            return False

        fund_id = _valid_id("bf.fund", post.get("fund_id"))
        campaign_id = _valid_id("donation.campaign", post.get("campaign_id"))
        donation = (
            request.env["donation.donation"]
            .sudo()
            .create_web_donation(partner.id, amount, fund_id, campaign_id)
        )
        return request.render(
            "bf_fundraising_web.donation_thanks",
            # Seulement ce que le visiteur a saisi : jamais les données d'une
            # fiche existante appariée par courriel.
            {"donation": donation, "email": email},
        )
