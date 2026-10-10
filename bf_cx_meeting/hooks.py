"""Install hook, kept as a no-op since 18.0.1.3.0.

The rating email is written in English in data/mail_template_data.xml and its
French lives in i18n/fr_CA.po, which Odoo loads at install: nothing is left to
write per language. The manifest and the 18.0.1.1.0 migration still call it.
"""


def post_init_hook(env):
    """Nothing to do: the data file and the catalogue carry both languages."""
