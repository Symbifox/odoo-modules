"""Régénère les signatures : un `-u` réécrit les gabarits et ne rend rien.

Sans ce script, la 18.0.5.1.0 serait une mise à niveau sans effet visible. La
chaîne est la suivante : `_load_records` pose `install_mode=True` dans le
contexte (`odoo/models.py:5452`), `mail_template.write` ne régénère que
`if "body_html" in vals and not self.env.context.get("install_mode")`, donc
l'écriture des gabarits par la mise à niveau **saute**
`_bf_refresh_signatures`. Les corps changent en base ; les signatures déjà
stockées gardent la rangée de liens de 10 px que cette version remplace par des
boutons.

L'exclusion elle-même est voulue — pendant une mise à niveau, le gabarit se
réécrit AVANT les éléments de marque de la société, et un rendu déclenché là
lirait des champs à moitié à jour. Le prix de ce choix est une passe finale par
version : c'est ce fichier.

POURQUOI `end-` ET NON `post-`
------------------------------
Les `post-` tournent dans la boucle par paquet de `load_module_graph`
(`odoo/modules/loading.py:156` puis `:233`), donc **avant** le chargement des
modules qui dépendent de celui-ci. Or `bf_preferred_name_signature`
(`auto_install`) remplace `bf_signature_pronouns` par un `related` vers
`partner_id.bf_pronouns` : un rendu déclenché depuis un `post-` lit encore la
vieille colonne stockée, nulle pour qui n'a jamais rempli le champ de la
signature, et la régénération EFFACE les pronoms de sa signature. Mesuré au
banc : une personne perdait `(il/lui (they/them))` et sa signature tombait de
1097 à 1008 caractères.

Les `end-` tournent à l'étape 3.5, après `registry.loaded = True` et
`registry.setup_models(cr)` (`loading.py:521-523`, `migration.py:66`) : le
registre est complet, les champs sont ceux que l'exécution normale verra, et le
rendu lit la même chose qu'un rendu déclenché depuis l'interface.

CE SCRIPT NE PEUT PAS LEVER, ET C'EST VOULU
-------------------------------------------
Une étape `end` qui lève ne se rejoue **jamais** : à cet instant le schéma, les
données et la version du module sont déjà commis, et la reprise trouve
`latest_version` égale au manifeste, donc ne migre rien — en silence,
mesuré en production sur un autre module de la suite. Le corps est donc
enveloppé : à la moindre exception, un `warning` nomme exactement la
passe à rejouer à la main, et la montée continue.

Portée : les **cinq** gabarits que cette version change — la carte, le bandeau
et les deux colonnes pour leur rangée de liens, `plain` et `compact` pour leurs
pronoms. `minimal` ne rend ni liens ni pronoms : son corps ne change pas, et le
régénérer reviendrait à faire courir un risque de rendu à des gens que la
version ne concerne pas. Dans cette portée-là, la règle de `_bf_refresh_signatures` est
rejouée telle quelle : les sociétés qui portent un de ces gabarits avec la
propagation active, plus les personnes qui en ont un en surcharge, ces
dernières filtrées une seconde fois par la propagation de LEUR société. Pas
« tous les usagers » : une société qui a coupé la propagation l'a coupée, et
une mise à niveau n'est pas un bon moment pour la lui rallumer.

`force=True` est obligatoire. `_bf_apply_company_signature` compare le rendu à
`bf_signature_rendered`, et l'ancien rendu y est identique au nouveau pour
toute personne dont le gabarit n'a pas changé : sans `force`, la passe sauterait
tout le monde.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Les cinq gabarits dont cette version change le corps : les trois qui rendent
# la rangée de liens, plus `plain` et `compact`, dont seuls les pronoms
# changent (ils passaient en #999 sur 10 à 11 px, soit 2,85:1 de contraste).
# `minimal` ne rend ni liens ni pronoms : son corps ne bouge pas.
GABARITS_TOUCHES = (
    "bf_signature_card", "bf_signature_banner", "bf_signature_two_columns",
    "bf_signature_plain", "bf_signature_compact",
)

# Cette route sert le logo du site TEL QUEL, et celui-ci est un SVG : ni Gmail
# ni Outlook ne dessinent un SVG dans un `<img>`, le destinataire voit une case
# vide. Corrigé une première fois dans les signatures posées, pas à la source —
# dans le champ qui les engendre, où il attendait le rendu suivant. On ne le
# réécrit pas ici : le champ porte peut-être une adresse choisie à la main, et
# l'écraser à l'aveugle dans une migration est le geste qui a créé le problème.
# On le NOMME, pour que la pose le corrige sciemment.
ROUTE_SVG = "/web/image/website/"

# Ce que le journal doit dire si le corps lève : une étape `end` ne se rejoue
# pas toute seule, donc le message est la consigne de rattrapage, pas un
# constat. En shell Odoo sur la base concernée.
RATTRAPAGE = """bf_multi_company_email: la passe de régénération de la \
18.0.5.1.0 a LEVÉ, et elle ne se rejouera pas d'elle-même : la version du \
module est déjà consignée, donc un `-u` de reprise ne jouera aucune \
migration. Effet si rien n'est fait : les corps des gabarits portent les \
boutons, mais les signatures déjà stockées gardent la rangée de liens de \
10 px. La passe peut aussi être PARTIELLE (ce qui a été écrit avant \
l'exception reste écrit) ; la rejouer est sans danger, elle réécrit tout. \
À rejouer à la main, en shell Odoo (`odoo shell -d <base>`) :

    gabarits = env["mail.template"].sudo().browse([
        env.ref("bf_multi_company_email." + nom).id
        for nom in ("bf_signature_card", "bf_signature_banner",
                    "bf_signature_two_columns", "bf_signature_plain",
                    "bf_signature_compact")
    ])
    societes = env["res.company"].sudo().search([
        ("bf_signature_template_id", "in", gabarits.ids),
        ("bf_signature_auto_apply", "=", True),
    ])
    gens = env["res.users"].sudo().search([
        ("share", "=", False),
        "|", ("company_id", "in", societes.ids),
        ("bf_signature_template_id", "in", gabarits.ids),
    ]).filtered(lambda u: u.company_id.bf_signature_auto_apply)
    gens._bf_apply_company_signature(force=True)
    env.cr.commit()

Puis relire la COLONNE, pas le cache : `select id, length(signature) from \
res_users where id in (...)`, et y chercher un fond de bouton."""


def _empreintes(cr, ids):
    """Les signatures stockées, relues dans les COLONNES, par identifiant.

    La règle de la maison : on mesure ce que l'outil lit. Le compte annoncé au
    journal doit être celui des signatures qui ont réellement changé, pas celui
    des candidats — `_bf_apply_company_signature` saute les rendus vides, et
    `len(users)` les comptait quand même.
    """
    if not ids:
        return {}
    cr.execute(
        """
        SELECT id,
               md5(coalesce(signature, '')),
               md5(coalesce(bf_signature_rendered::text, ''))
          FROM res_users
         WHERE id IN %s
        """,
        (tuple(ids),),
    )
    return {uid: (sig, rendu) for uid, sig, rendu in cr.fetchall()}


def _regenerer(cr):
    env = api.Environment(cr, SUPERUSER_ID, {})

    templates = env["mail.template"].sudo().browse()
    for xmlid in GABARITS_TOUCHES:
        template = env.ref(
            f"bf_multi_company_email.{xmlid}", raise_if_not_found=False)
        if template:
            templates |= template
    if not templates:
        _logger.warning(
            "bf_multi_company_email: aucun des gabarits touchés par la "
            "18.0.5.1.0 n'a été trouvé, aucune signature régénérée.")
        return

    companies = env["res.company"].sudo().search([
        ("bf_signature_template_id", "in", templates.ids),
        ("bf_signature_auto_apply", "=", True),
    ])
    users = env["res.users"].sudo().search([
        ("share", "=", False),
        "|",
        ("company_id", "in", companies.ids),
        ("bf_signature_template_id", "in", templates.ids),
    ])
    users = users.filtered(lambda u: u.company_id.bf_signature_auto_apply)

    if not users:
        _logger.info(
            "bf_multi_company_email: aucune signature à régénérer "
            "(propagation coupée, ou aucun gabarit touché en service).")
    else:
        env.flush_all()
        avant = _empreintes(cr, users.ids)
        users._bf_apply_company_signature(force=True)
        # Vider vers la base avant de relire : sans ça on comparerait deux
        # états du cache de l'ORM, qui ne dit pas ce que la colonne porte.
        env.flush_all()
        apres = _empreintes(cr, users.ids)
        ecrites = [uid for uid in users.ids if avant.get(uid) != apres.get(uid)]
        _logger.info(
            "bf_multi_company_email: %s signature(s) réécrite(s) en "
            "18.0.5.1.0 sur %s candidate(s) — les liens rapides sont des "
            "boutons. Réécrites : %s.",
            len(ecrites), len(users), ecrites or "aucune",
        )

    # Le logo SVG, nommé société par société. Le rendu ne lève pas là-dessus :
    # il sort une image que le destinataire ne voit pas, ce qui ne se remarque
    # qu'à l'ouverture d'un vrai courriel. Donc on le dit au journal.
    douteuses = env["res.company"].sudo().search([
        ("bf_signature_logo_url", "like", ROUTE_SVG),
    ])
    for company in douteuses:
        _logger.warning(
            "bf_multi_company_email: la société %s (%s) porte un logo de "
            "signature servi par %s — cette route rend un SVG, que ni Gmail "
            "ni Outlook ne dessinent dans un <img> : %s. À remplacer par un "
            "PNG avant de régénérer les signatures.",
            company.id, company.name, ROUTE_SVG, company.bf_signature_logo_url,
        )
    if not douteuses:
        _logger.info(
            "bf_multi_company_email: aucune société ne porte un logo de "
            "signature en %s.", ROUTE_SVG)


def migrate(cr, version):
    # Enveloppe volontaire : voir la docstring. Une étape `end` qui lève ne se
    # rejoue jamais, donc elle ne doit pas lever — elle doit DIRE ce qu'il
    # reste à faire.
    try:
        _regenerer(cr)
    except Exception:
        _logger.warning(RATTRAPAGE, exc_info=True)
