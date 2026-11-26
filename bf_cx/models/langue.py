# -*- coding: utf-8 -*-
"""Language of a text written for someone who is not the one behind the screen.

Since the source moved to English (18.0.1.13.2), a text rendered without a language
in the context reads the `en_US` slot, i.e. English. Before, that slot held the
French and everyone read French. A closed-loop activity built while a client
submits a survey, or a note written by a scheduled job, is read by a staff
member: it is rendered in THEIR language when it is known, else in the
company's, else in the caller's.
"""


def langue_de(env, *porteurs, societe=None):
    """Installed language code: the first holder (user, contact) that has one,
    else the company (`societe`, or the environment's), else `env.lang`, else
    en_US."""
    installees = {code for code, _nom in env["res.lang"].get_installed()}
    for porteur in porteurs:
        lang = porteur and porteur.lang
        if lang in installees:
            return lang
    for soc in (societe, env.company):
        lang = soc and soc.partner_id.lang
        if lang in installees:
            return lang
    return env.lang if env.lang in installees else "en_US"
