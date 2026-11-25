# -*- coding: utf-8 -*-
"""La langue d'un envoi qui part sans personne derrière l'écran.

Depuis la bascule de la source en anglais, un travail planifié qui rend un gabarit ou
un libellé sans langue au contexte lit la fente `en_US`, donc l'anglais. Avant, cette fente
portait le français et tout le monde lisait le français. On rend donc dans la langue du
destinataire quand on la connaît, sinon dans celle de la société, sinon dans celle de l'appel.
"""


def langue_de(env, *porteurs, societe=None):
    """Code de langue installé : le premier porteur (usager, contact) qui en a une, sinon la
    société (`societe` ou celle de l'environnement), sinon `env.lang`, sinon en_US."""
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
