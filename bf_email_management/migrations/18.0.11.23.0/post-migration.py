"""Device tokens become hashes (audit 2026-09-08, S-M2).

Every row still carrying a clear token gets its SHA-256 in ``token_hash`` and
the clear value is dropped, except on a row awaiting its exchange, where the
controller still has to hand the clear token to the app. Phones notice
nothing: ``_resolve`` recognises the hash of the token they already present.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE bf_email_mobile_device
           SET token_hash = encode(sha256(convert_to(device_token, 'UTF8')), 'hex'),
               device_token = CASE WHEN pending_code IS NULL THEN NULL
                                   ELSE device_token END
         WHERE device_token IS NOT NULL AND token_hash IS NULL
    """)
