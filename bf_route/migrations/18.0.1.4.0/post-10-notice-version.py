"""The standard position notice changed: everyone reads it again.

⚠️ Why a migration at all. The version that forces a re-read moves only when a field of
``res.company`` is written (see ``ResCompany.write``). The standard notice lives in Python,
in ``_default_notice``, so rewriting it changes what NEW readers see and leaves everyone who
already acknowledged the old text attesting to a text that no longer describes the
processing. The acknowledgements themselves keep the text as it was read, which is right:
they are evidence of what the person was told, not of what the module does today.

What the new text adds, and why it is not cosmetic:

* the kilometres of the day are measured from the positions read, which the old text did
  not mention at all;
* a reading accepted as a customer's place is NOT erased with the position, while the old
  text ended on "then it is erased" without reserve.

Both are uses the person must be informed of under s. 8.1 of Quebec's Law 25, and the
second one contradicts what they were told.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE res_company
           SET bf_route_notice_version = bf_route_notice_version + 1
         WHERE bf_route_position_mode = 'stops'
     RETURNING id, bf_route_notice IS NOT NULL AND bf_route_notice <> ''
    """)
    rows = cr.fetchall()
    if not rows:
        _logger.info("bf_route: no company reads positions, no notice version to bump")
        return
    custom = [company_id for company_id, has_own in rows if has_own]
    _logger.info("bf_route: notice version bumped for %s company(ies); everyone reads the "
                 "notice again before their next position is kept", len(rows))
    if custom:
        # ⚠️ A migration cannot rewrite someone else's words. A company that replaced the
        # standard notice now makes its people re-read ITS text, which may still say nothing
        # about the kilometres or about the place that outlives the erasure.
        _logger.warning(
            "bf_route: company(ies) %s use their own position notice. The version was bumped, "
            "so their people will read it again, but the text itself was NOT touched: review "
            "it against the kilometre measurement and the place taken from a reading.",
            ", ".join(str(i) for i in custom))
