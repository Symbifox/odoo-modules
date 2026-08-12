"""Carry the download watermark from a private add-on layer into the base.

The watermark first shipped as a ``cq_watermark`` column on
``secure.transfer.brand``, added by a private downstream add-on. It is generic
plumbing, not part of that add-on's business process, so it moves into
bf_securetransfer as ``watermark_downloads`` — and the setting has to survive
the move, otherwise a brand that stamps its PDFs today would silently stop the
moment the add-on is uninstalled.

A database that never carried that add-on has no such column: the copy is
skipped and the new field keeps its default (off).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'secure_transfer_brand'
           AND column_name = 'cq_watermark'
    """)
    if not cr.fetchone():
        return
    cr.execute("""
        UPDATE secure_transfer_brand
           SET watermark_downloads = TRUE
         WHERE cq_watermark IS TRUE
           AND watermark_downloads IS NOT TRUE
    """)
    _logger.info(
        "bf_securetransfer: watermark carried over from cq_watermark on %s brand(s).",
        cr.rowcount,
    )
