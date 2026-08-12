"""Clean up the BF invoice PDF formatting.

Three data fixes on res.company for the Blue Fox tenant (company id=1):

1. company_details: drop the duplicated company name. The folder layout already
   prints the legal name above this block (via partner_legal_name's
   external_layout_folder_legal_name inherit), so repeating it as the first
   line of company_details creates a visible duplicate on every invoice.

2. report_footer: replace the editor-generated o_file_box file pill that
   wkhtmltopdf couldn't render. Now that report_embedded_files.scss is bundled
   into web.report_assets_common (this same version), the pill *would* render —
   but the existing footer also hardcodes the wrong TPS/TVQ numbers from the
   pre-incorporation enr., plus the wrong contact email. We rewrite the whole
   footer with the correct Blue Fox Inc. fiscal identifiers and a clean link
   to the T&C page (no editor file-box HTML).

3. vat / l10n_ca_pst: align with the correct Blue Fox Inc. identifiers.
   The previous values were from Blue Fox enr. (sole proprietorship, radié
   septembre 2025) and are now invalid. See
   reference_bf_corporate_identifiers.md.

Only touches the BF main company (id=1). Other tenants installing the same
module get the manifest + SCSS + view changes but no data write.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


# Correct Blue Fox Inc. identifiers (post-incorporation 2025-06-27).
BF_VAT = "000000000 RT0001"           # NE / BN — TPS
BF_QST = "0000000000 TQ0001"          # TVQ — RQ
BF_EMAIL = "service@example.com"
BF_PHONE = "555 555-5555"

BF_COMPANY_DETAILS = (
    "<p>123, rue Exemple<br>"
    "Ville QC A1A 1A1<br>"
    "Canada</p>"
)

# Plain HTML the wkhtmltopdf can actually render.
# Avoid html_editor classes (o_file_box, alert, d-flex, …) — the matching SCSS
# is bundled by this same module in 18.0.2.3.0 but using vanilla HTML in a
# stable field like report_footer makes the footer future-proof.
BF_REPORT_FOOTER = (
    f'<p>{BF_PHONE}<br/>'
    f'{BF_EMAIL}<br/>'
    f'TPS: {BF_VAT}<br/>'
    f'TVQ: {BF_QST}</p>'
    '<p>Sujet aux '
    '<a href="https://example.com/terms">termes et conditions</a> '
    'de Blue Fox.</p>'
)


def _is_bf_tenant(env):
    """Only touch the canonical BF tenant — match by VAT (current or stale).

    Defensive: this migration also runs on staging / other tenants where
    bluefox_branding gets installed; never touch their res.company data.
    """
    co = env["res.company"].browse(1).exists()
    if not co:
        return False
    if co.name not in ("Blue Fox", "Les services de consultation Blue Fox, Inc."):
        return False
    # Match either the correct new BN or the stale old enr. BN
    stale_vat = "00000 0000 RT0001"
    return (co.vat or "").strip() in (BF_VAT, stale_vat)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    if not _is_bf_tenant(env):
        _logger.info(
            "bluefox_branding 18.0.2.3.0: not the BF tenant — skipping data fixes."
        )
        return

    co = env["res.company"].browse(1)
    updates = {}

    if "Les services de consultation Blue Fox, Inc." in (co.company_details or ""):
        updates["company_details"] = BF_COMPANY_DETAILS

    # Detect either of the two broken footers (with or without the o_file_box).
    needs_footer_rewrite = (
        "0000000000 IC0001" in (co.report_footer or "")
        or "o_file_box" in (co.report_footer or "")
        or "info@example.com" in (co.report_footer or "")
    )
    if needs_footer_rewrite:
        updates["report_footer"] = BF_REPORT_FOOTER

    if (co.vat or "").strip() != BF_VAT:
        updates["vat"] = BF_VAT

    if (co.l10n_ca_pst or "").strip() != BF_QST:
        updates["l10n_ca_pst"] = BF_QST

    if updates:
        co.write(updates)
        _logger.info(
            "bluefox_branding 18.0.2.3.0: updated res.company id=1 fields: %s",
            sorted(updates),
        )
    else:
        _logger.info(
            "bluefox_branding 18.0.2.3.0: BF res.company already clean — nothing to do."
        )
