"""Plus de colspan dans les signatures.

Le cœur Odoo 18.0.20261112 retire tout `colspan` d'un tableau ouvert dans le
composeur (`populateTableSpans`, voir le commentaire du record de
`bf_signature_card`). Cette version refait les gabarits livrés ; ce script
remet en état ce qu'un `-u` ne touche pas.

DEUX CHOSES, DANS CET ORDRE
---------------------------
1. **Les traductions du corps des gabarits.** `mail.template.body_html` est
   traduisible et chaque langue porte une COPIE ENTIÈRE du corps. Un `-u`
   n'écrit que la clé `en_US` ; la clé `fr_CA` garde l'ancien corps, avec ses
   colspan, et c'est elle qui rend dès qu'on régénère depuis une interface en
   français (préférences, lien rapide, galerie, société). On retire donc, sur
   les gabarits livrés, toute clé autre que `en_US` qui DIFFÈRE de la source :
   ces corps n'ont aucun texte à traduire, une copie différente est donc une
   copie périmée (celle de `plain` pouvait dater d'avant la 18.0.5.1.0). La
   langue retombe sur la source. Le catalogue livré a un `msgstr` vide pour
   les six corps, donc rien ne la recrée.

2. **Les signatures stockées**, visées par leur CONTENU et non par leur
   gabarit : toute personne interne dont `signature` ou une valeur de
   `bf_signature_rendered` (une par société) porte un colspan. Le rendu se
   fait par couple (personne, société), et seulement pour une société qui a
   gardé la propagation (`bf_signature_auto_apply`) : une société qui l'a
   coupée l'a coupée. Pas de `force` : on n'écrit que ce qui change, et un
   rendu qui porte ENCORE un colspan (gabarit maison créé chez le locataire)
   n'est pas écrit — il est nommé au journal.

Le composeur lit, dans l'ordre : la signature de l'identité d'envoi
(`bf.email.identity.signature_html`, une copie que la personne a choisie),
puis `bf_signature_rendered` de la société du compte, puis `signature`. Les
deux dernières sont réécrites ici ; la première est une copie d'usager et
n'est que NOMMÉE au journal si elle porte un colspan.

Mêmes contraintes que `18.0.5.1.0/end-migration.py`, dont la docstring fait
foi : `end-` et non `post-` (le registre doit être complet, sans quoi les
pronoms s'effacent) et un corps qui ne lève JAMAIS (une étape `end` qui lève
ne se rejoue pas : le journal nomme la passe à refaire).
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

VERSION = "18.0.5.2.0"
# L'ATTRIBUT, pas le mot : une adresse ou un nom peut contenir « colspan ».
MOTIF = "%colspan=%"

GABARITS_LIVRES = (
    "bf_signature_card", "bf_signature_banner", "bf_signature_two_columns",
    "bf_signature_compact", "bf_signature_minimal", "bf_signature_plain",
)

RATTRAPAGE = """bf_multi_company_email: la passe de la 18.0.5.2.0 a LEVÉ, et \
elle ne se rejouera pas d'elle-même : la version du module est déjà \
consignée. Effet si rien n'est fait : des signatures stockées gardent un \
colspan, que l'éditeur du cœur 18.0.20261112 casse à chaque courriel \
(boutons coincés sous le logo). La passe peut être PARTIELLE ; la rejouer \
est sans danger. En shell Odoo (`odoo shell -d <base>`) :

    import importlib.util, odoo.addons.bf_multi_company_email as m, os
    chemin = os.path.join(os.path.dirname(m.__file__),
                          "migrations/18.0.5.2.0/end-migration.py")
    spec = importlib.util.spec_from_file_location("m28088", chemin)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    mod._passe(env.cr); env.cr.commit()

Puis relire les COLONNES : `select id from res_users where signature like \
'%colspan=%' or bf_signature_rendered::text like '%colspan=%'`."""


def _traductions(cr, env):
    ids = []
    for xmlid in GABARITS_LIVRES:
        template = env.ref(
            f"bf_multi_company_email.{xmlid}", raise_if_not_found=False)
        if template:
            ids.append(template.id)
    if not ids:
        return []
    cr.execute(
        """
        UPDATE mail_template t
           SET body_html = (
                 SELECT jsonb_object_agg(e.key, e.value)
                   FROM jsonb_each(t.body_html) e
                  WHERE e.key = 'en_US' OR e.value = t.body_html->'en_US')
         WHERE t.id IN %s
           AND t.body_html ? 'en_US'
           AND EXISTS (SELECT 1 FROM jsonb_each(t.body_html) e
                        WHERE e.key <> 'en_US' AND e.value <> t.body_html->'en_US')
     RETURNING t.id
        """,
        (tuple(ids),),
    )
    nettoyes = [row[0] for row in cr.fetchall()]
    env["mail.template"].invalidate_model(["body_html"])
    return nettoyes


def _porteurs(cr):
    """Personnes internes dont une signature stockée porte un colspan."""
    cr.execute(
        """
        SELECT u.id
          FROM res_users u
         WHERE NOT u.share
           AND u.active
           AND (u.signature::text LIKE %s
                OR u.bf_signature_rendered::text LIKE %s)
         ORDER BY u.id
        """,
        (MOTIF, MOTIF),
    )
    return [row[0] for row in cr.fetchall()]


def _regenerer(env, users):
    """Le corps de `_bf_apply_company_signature`, par société qui propage."""
    ecrites, refusees, retouchees = set(), [], []
    for user in users:
        for company in (user.company_ids or user.company_id):
            if not company.bf_signature_auto_apply:
                continue
            target = user.sudo().with_context(
                allowed_company_ids=[company.id],
                bf_signature_regen=True,
            )
            stockee = target.bf_signature_rendered or ""
            maison = company == user.company_id and "colspan=" in (user.signature or "")
            if "colspan=" not in stockee and not maison:
                continue
            # Le refus se décide sur le GABARIT, pas sur le rendu : un
            # avertissement de société (champ Html) peut porter son propre
            # tableau à colspan, et refuser sur le rendu laisserait toute la
            # société sur l'ANCIENNE carte, boutons sous le logo.
            template = user._bf_signature_template(company)
            if template and "colspan=" in (template.body_html or ""):
                refusees.append((user.id, company.id))
                continue
            rendered = user._bf_render_signature(company)
            if not rendered:
                continue
            if rendered != stockee:
                target.bf_signature_rendered = rendered
                ecrites.add(user.id)
            if company == user.company_id:
                if "colspan=" not in (user.signature or ""):
                    # Signature saisie à la main sans colspan, réécrite parce
                    # que la copie par société en portait un : même règle qu'un
                    # changement de gabarit, mais on le DIT.
                    retouchees.append(user.id)
                target.write({"signature": rendered})
                ecrites.add(user.id)
    return sorted(ecrites), refusees, retouchees


def _passe(cr):
    env = api.Environment(cr, SUPERUSER_ID, {})

    nettoyes = _traductions(cr, env)
    _logger.info(
        "bf_multi_company_email: %s gabarit(s) livré(s) dont une traduction "
        "du corps était périmée, ramenés à la source : %s.",
        len(nettoyes), nettoyes or "aucun")

    porteurs = _porteurs(cr)
    if porteurs:
        env.flush_all()
        ecrites, refusees, retouchees = _regenerer(env, env["res.users"].browse(porteurs))
        env.flush_all()
        _logger.info(
            "bf_multi_company_email: %s personne(s) portaient un colspan ; "
            "signatures réécrites en %s pour : %s.",
            len(porteurs), VERSION, ecrites or "aucune")
        if retouchees:
            _logger.warning(
                "bf_multi_company_email: signature principale réécrite sans "
                "qu'elle porte de colspan (copie par société abîmée) pour : %s. "
                "Si l'une était saisie à la main, elle est remplacée par le "
                "gabarit.", retouchees)
        for uid, cid in refusees:
            _logger.warning(
                "bf_multi_company_email: le gabarit de la personne %s pour la "
                "société %s porte un colspan (gabarit maison, hors du "
                "module) : signature laissée telle quelle. À refaire sans "
                "colspan, sinon le composeur la cassera.", uid, cid)
    else:
        _logger.info(
            "bf_multi_company_email: aucune signature stockée ne porte de "
            "colspan, rien à régénérer en %s.", VERSION)

    # Le verdict se lit dans les COLONNES, après la passe.
    restantes = _porteurs(cr)
    if restantes:
        _logger.warning(
            "bf_multi_company_email: %s personne(s) portent ENCORE un colspan "
            "après la passe %s : %s (propagation coupée, gabarit maison, ou "
            "rendu vide).", len(restantes), VERSION, restantes)

    cr.execute(
        "SELECT id FROM res_company WHERE bf_signature_disclaimer::text LIKE %s",
        (MOTIF,))
    societes = [row[0] for row in cr.fetchall()]
    if societes:
        _logger.warning(
            "bf_multi_company_email: l'avertissement de signature de %s "
            "société(s) porte son propre tableau à colspan : %s. Le composeur "
            "l'abîmera ; à ressaisir sans tableau fusionné.",
            len(societes), societes)

    cr.execute("SELECT to_regclass('bf_email_identity') IS NOT NULL")
    if cr.fetchone()[0]:
        cr.execute(
            "SELECT id FROM bf_email_identity WHERE signature_html::text LIKE %s",
            (MOTIF,))
        identites = [row[0] for row in cr.fetchall()]
        if identites:
            _logger.warning(
                "bf_multi_company_email: %s identité(s) d'envoi portent leur "
                "propre signature avec un colspan (copie d'usager, non "
                "réécrite) : %s. Le composeur la lit EN PREMIER.",
                len(identites), identites)


def migrate(cr, version):
    # Enveloppe volontaire : une étape `end` qui lève ne se rejoue jamais.
    # Le point de sauvegarde est indispensable : `migrate()` est appelé sans,
    # et une erreur SQL attrapée ici laisserait la transaction AVORTÉE — la
    # montée échouerait plus loin, sur les modules suivants, sans se rejouer.
    try:
        with cr.savepoint():
            _passe(cr)
    except Exception:
        api.Environment(cr, SUPERUSER_ID, {}).invalidate_all()
        _logger.warning(RATTRAPAGE, exc_info=True)
