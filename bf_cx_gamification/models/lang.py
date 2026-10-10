"""Language of whoever reads a text this bridge stores."""


def reader_lang(user):
    """The user's language if it is installed, else the company's, else None.

    None leaves the context without a language: the text is then written in
    the source language (English).
    """
    installed = {code for code, _name in user.env["res.lang"].get_installed()}
    for lang in (user.lang, user.company_id.partner_id.lang):
        if lang in installed:
            return lang
    return None
