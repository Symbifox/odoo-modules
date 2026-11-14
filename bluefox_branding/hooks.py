import hashlib
import logging
import os

from lxml import etree

_logger = logging.getLogger(__name__)

# ─── « Avis de retard sur facture » à la marque (gabarit 141) ─────────────
# Le gabarit 141 n'a pas d'identifiant XML (créé à la main dans l'interface) :
# le crochet le retrouve par son nom et son modèle.
#
# 🔴 Deux défauts corrigés par la 18.0.3.29.0.
#
# 1. Ce corps n'existait qu'en français et le crochet l'écrivait dans TOUTES les
#    langues actives : un client anglais recevait son avis de retard en français.
#    Les seize autres surcharges avaient déjà leur jumeau anglais
#    (`mail_template_overrides_en.xml`), pas celle-ci, qui vit en Python.
#    Une seule structure HTML, deux jeux de phrases. On remplace par
#    `.replace()` et jamais par `.format()` : le corps porte des accolades
#    QWeb ({{ … }}).
#
# 2. 🔴 Le littéral du module portait encore sa COQUILLE (fond, carte, en-tête,
#    logo, pied de marque, liens légaux) alors que la production l'a dé-coquillé
#    le 2026-10-28 et rattaché à la mise en page partagée
#    (`email_layout_xmlid`). Le réécrire tel quel aurait rendu une carte dans la
#    carte et DEUX pieds, donc deux lignes « Confidentialité | Conditions » :
#    le doublon même que cette version ferme. Le squelette ci-dessous EST le corps
#    que la production sert, au caractère près, et le crochet pose désormais la
#    mise en page et la langue avec lui.
_LATE_INVOICE_SQUELETTE = """\
<t t-set="company" t-value="object.company_id or user.company_id"/>
<t t-set="brand_primary" t-value="(company and company.report_brand_primary) or '#714B67'"/>
<t t-set="brand_dark" t-value="(company and company.report_brand_dark) or '#212529'"/>
<p style="margin:0 0 16px 0;">@@salut@@<t t-out="object.partner_id.name"/>@@salut_fin@@</p>

<p style="margin:0 0 16px 0;">
@@avis@@
<strong t-attf-style="color:{{ brand_dark }};"><t t-out="object.name"/></strong>
@@avis_montant@@
<strong t-attf-style="color:{{ brand_dark }};"><t t-out="format_amount(object.amount_total, object.currency_id)"/></strong>
@@avis_fin@@
</p>

<p style="margin:0 0 16px 0;">
@@echeance@@
<strong t-attf-style="color:{{ brand_dark }};"><t t-out="format_date(object.invoice_date_due)"/></strong>.
</p>

<p style="margin:0 0 16px 0;">
@@reglement@@
</p>

<p style="margin:0 0 8px 0;">
@@question@@
</p>

<p style="margin:24px 0 0 0;font-size:14px;">
@@salutations@@<br/>
<strong t-attf-style="color:{{ brand_dark }};">@@equipe@@<t t-out="company.name"/>@@equipe_fin@@</strong>
</p>
"""

_LATE_INVOICE_TEXTES = {
    'fr': {
        'salut': 'Bonjour ',
        'salut_fin': ',',
        'avis': 'Nous souhaitons porter à votre attention que la facture',
        'avis_montant': 'd\'un montant de',
        'avis_fin': 'est actuellement en retard de paiement.',
        'echeance': 'La date d\'échéance était le',
        'reglement': 'Nous vous prions de bien vouloir procéder au règlement dans les meilleurs délais.\nSi le paiement a déjà été effectué, veuillez ne pas tenir compte de cet avis.',
        'question': 'Pour toute question, n\'hésitez pas à nous contacter.',
        'salutations': 'Cordialement,',
        'equipe': 'L\'équipe ',
        'equipe_fin': '',
    },
    'en': {
        'salut': 'Hello ',
        'salut_fin': ',',
        'avis': 'We would like to bring to your attention that invoice',
        'avis_montant': 'for an amount of',
        'avis_fin': 'is currently overdue.',
        'echeance': 'The due date was',
        'reglement': 'Please arrange payment at your earliest convenience.\nIf payment has already been made, please disregard this notice.',
        'question': 'If you have any questions, please do not hesitate to contact us.',
        'salutations': 'Kind regards,',
        'equipe': 'The ',
        'equipe_fin': ' team',
    },
}

# La mise en page et la langue vont avec ce corps : sans elles, il sort nu (plus
# de coquille) et toujours dans la langue de la session qui envoie.
_LATE_INVOICE_LAYOUT = 'bf_onboarding_base.bf_mail_layout'
_LATE_INVOICE_LANG = '{{ object.partner_id.lang }}'

# Les corps que ce module a PUBLIÉS. Tout autre corps qui porte `brand_primary`
# a été réécrit à la main sur la base et n'est pas touché (`_apply_late_invoice`).
#
# ⚠️ Entretien : chaque version qui change une phrase AJOUTE ici les empreintes
# qu'elle publie, sans retirer les anciennes. Sans cela, la version SUIVANTE
# trouvera un corps qu'elle ne reconnaît pas, gardera le gabarit et journalisera
# « réécrit à la main » sur son propre travail. L'essai
# `test_les_corps_publies_sont_reconnus` refuse un oubli.
_LATE_INVOICE_SHA_CONNUS = frozenset({
    # Le corps COQUILLÉ que le module écrivait jusqu'à la 18.0.3.28.0, dans
    # toutes les langues : une base restée dessus doit être reprise, pas gardée.
    '4b39c649a87d51c484e1bb2148c4c1f96a409748e9d812441965026a0fbe68e2',
    # Les deux corps de la 18.0.3.29.0. Le français est aussi, au caractère, le
    # corps dé-coquillé que la production sert depuis le 2026-10-28.
    'd47adea8072fc37bea7ecc05a73963be26318f793f43af10385070edf1f3063b',
    '0821e5022da66020f37a9de7c6c501180eb400e63372b6561cac168398eedf8a',
})


def _late_invoice_body(lang):
    """Le corps de l'avis de retard dans la langue demandée.

    Le français pour une langue française, l'anglais pour les autres : la même
    règle que la catégorie 1 du crochet, qui choisit entre ses deux fichiers XML.
    """
    textes = _LATE_INVOICE_TEXTES['fr' if (lang or '').startswith('fr') else 'en']
    corps = _LATE_INVOICE_SQUELETTE
    for cle, valeur in textes.items():
        corps = corps.replace('@@%s@@' % cle, valeur)
    return corps


def _late_invoice_retouche_a_la_main(gabarit, langs):
    """True quand le corps stocké est le nôtre, mais réécrit depuis.

    Un corps sans `brand_primary` n'est pas le nôtre (gabarit d'origine d'Odoo,
    ou texte propre au locataire) : le crochet l'habille, c'est son travail. Un
    corps qui porte `brand_primary` sans être l'un des deux que nous avons
    publiés a été retouché sur la base : on le garde. Même esprit que
    `_retouched_by_tenant` pour les gabarits de la catégorie 1.
    """
    for lang in langs:
        corps = gabarit.with_context(lang=lang).body_html or ''
        if 'brand_primary' not in corps:
            # Pas le nôtre dans cette langue : le crochet l'habille, c'est son
            # travail. `continue` et non `return False` : une langue non marquée
            # n'absout pas une retouche faite dans une autre.
            continue
        empreinte = hashlib.sha256(corps.encode()).hexdigest()
        if empreinte in _LATE_INVOICE_SHA_CONNUS:
            continue
        if corps == _late_invoice_body(lang):
            continue
        return True
    return False


def _apply_late_invoice(env, langs, gabarits=None):
    """Écrire l'avis de retard dans chaque langue active, avec sa mise en page.

    Appelé par `post_init_hook` et par la migration 18.0.3.29.0. Cherche aussi
    les gabarits archivés (`active_test=False`), comme la 18.0.3.25.1.
    `gabarits` borne le travail à des enregistrements donnés, pour un essai qui
    ne veut pas toucher au gabarit réel de la base où il tourne.

    🔴 La clé `en_US` est écrite EXPRÈS, à la fin. Quand `en_US` n'est pas une
    langue active, Odoo réécrit cette clé à chaque écriture par
    langue (`fields.py`, branche « model translation ») mais saute une écriture
    qui ne change rien : la langue que `en_US` finissait par porter dépendait
    donc de la base, et de l'ordre alphabétique de `res.lang`. Ici elle porte
    l'anglais, toujours.
    """
    if gabarits is None:
        gabarits = env['mail.template'].with_context(active_test=False).search([
            ('model', '=', 'account.move'),
            ('name', 'ilike', 'Avis de retard'),
        ])
    if not gabarits:
        _logger.warning("bluefox_branding: aucun « Avis de retard » sur cette base")
        return gabarits.browse()

    ecrits = gabarits.browse()
    for gabarit in gabarits:
        if _late_invoice_retouche_a_la_main(gabarit, langs):
            _logger.warning("bluefox_branding: « %s » (ID %s) a été réécrit à la main sur cette "
                            "base (%s) : gardé tel quel", gabarit.name, gabarit.id,
                            gabarit.write_date)
            continue
        # `en_US` en dernier, et même s'il figure déjà dans `langs` : c'est la
        # dernière écriture qui décide du contenu de cette clé.
        for lang in list(langs) + ['en_US']:
            gabarit.with_context(lang=lang).body_html = _late_invoice_body(lang)
        valeurs = {}
        if gabarit.email_layout_xmlid != _LATE_INVOICE_LAYOUT:
            valeurs['email_layout_xmlid'] = _LATE_INVOICE_LAYOUT
        if not gabarit.lang:
            valeurs['lang'] = _LATE_INVOICE_LANG
        if valeurs:
            gabarit.write(valeurs)
        ecrits |= gabarit
        _logger.info("bluefox_branding: « %s » (ID %s) écrit en %d langue(s)%s",
                     gabarit.name, gabarit.id, len(langs),
                     valeurs and " (%s)" % ", ".join(valeurs) or "")
    return ecrits


def _extract_templates_from_xml(filename='mail_template_overrides.xml'):
    """Parse an overrides file and extract field values per record XML ID.

    ``mail_template_overrides.xml`` is the French text, ``mail_template_overrides_en.xml``
    the English one (same records, same structure).

    Returns dict: {xml_id: {field_name: value, ...}}
    """
    xml_path = os.path.join(os.path.dirname(__file__), 'data', filename)
    if not os.path.exists(xml_path):
        _logger.warning("bluefox_branding: %s not found", xml_path)
        return {}

    tree = etree.parse(xml_path)
    result = {}

    for record in tree.xpath('//record[@model="mail.template"]'):
        xml_id = record.get('id')
        if not xml_id:
            continue

        fields = {}
        for field in record.findall('field'):
            fname = field.get('name')
            ftype = field.get('type')
            if ftype == 'html':
                # Serialize child elements as HTML string
                inner = ''.join(
                    etree.tostring(child, encoding='unicode', method='html')
                    for child in field
                )
                if not inner:
                    inner = field.text or ''
                fields[fname] = inner
            else:
                fields[fname] = field.text or ''

        result[xml_id] = fields

    return result


def _guarded_xml_ids(filename='mail_template_overrides.xml'):
    """XML IDs flagged ``bf_garde_retouche="1"`` in an overrides file.

    These are Odoo's own templates that a tenant may have rewritten by hand
    (one tenant reworded its sign-up welcome in the editor): such a template
    is kept, see ``_retouched_by_tenant``.
    """
    xml_path = os.path.join(os.path.dirname(__file__), 'data', filename)
    if not os.path.exists(xml_path):
        return set()
    tree = etree.parse(xml_path)
    return {r.get('id') for r in tree.xpath('//record[@model="mail.template"][@bf_garde_retouche="1"]')}


def _retouched_by_tenant(tmpl, langs):
    """True when someone edited this template by hand since its module created it.

    Untouched: written in the same minute it was created (module install).
    Ours: carries ``brand_primary``, the colour variable every override sets,
    so a replay of the hook keeps rewriting what the hook wrote.
    """
    if not tmpl.create_date or not tmpl.write_date:
        return False
    if (tmpl.write_date - tmpl.create_date).total_seconds() < 60:
        return False
    return not any('brand_primary' in (tmpl.with_context(lang=lang).body_html or '') for lang in langs)


def _get_active_langs(env):
    """Return list of active language codes."""
    langs = env['res.lang'].search([]).mapped('code')
    if not langs:
        langs = ['en_US']
    return langs


def post_init_hook(env, only=None):
    """Apply Blue Fox branding to all mail templates.

    ``only``: XML IDs to rewrite, and nothing else (no late invoice notice). A
    migration passes the templates its release changes: replaying every
    override would wipe a tenant's later edit of the others (18.0.3.26.0).

    Handles two categories:
    1. Templates with XML IDs (noupdate=True) — read from mail_template_overrides.xml
    2. Template 141 (no XML ID) — hardcoded body

    Writes in ALL active languages so JSONB translated fields (body_html, name,
    subject) are updated for every language key, not just en_US.
    """
    _logger.info("bluefox_branding: post_init_hook — applying Blue Fox email branding")

    active_langs = _get_active_langs(env)
    _logger.info("bluefox_branding: Active languages: %s", active_langs)

    # ── Category 1: Templates with XML IDs (noupdate=True in their origin module) ──
    # 🔴 Until 18.0.3.23.0 the French text was written in EVERY language: an English-speaking
    # family got its portal invitation, calendar and survey emails in French (found by the
    # QA with real emails, 2026-09-27). French languages get the French file, the others the
    # English one.
    templates_data = _extract_templates_from_xml()
    templates_en = _extract_templates_from_xml('mail_template_overrides_en.xml')
    guarded = _guarded_xml_ids()
    updated = 0

    for xml_id, fields in templates_data.items():
        if only is not None and xml_id not in only:
            continue
        tmpl = env.ref(xml_id, raise_if_not_found=False)
        if not tmpl:
            _logger.warning("bluefox_branding: Template %s not found — skipping", xml_id)
            continue
        if xml_id in guarded and _retouched_by_tenant(tmpl, active_langs):
            _logger.warning("bluefox_branding: Template %s (ID %s) was edited by hand on this "
                            "database (%s) — kept as is", xml_id, tmpl.id, tmpl.write_date)
            continue

        for lang in active_langs:
            values = fields if lang.startswith('fr') else templates_en.get(xml_id, fields)
            tmpl.with_context(lang=lang).write(values)
        updated += 1
        _logger.info("bluefox_branding: Updated %s (ID %s) in %d languages — %s",
                      xml_id, tmpl.id, len(active_langs),
                      fields.get('name', '(no name change)'))

    _logger.info("bluefox_branding: Updated %d templates from XML overrides", updated)

    if only is not None:
        _logger.info("bluefox_branding: post_init_hook complete (%d named templates)", len(only))
        return

    # ── Category 2: Template 141 — no XML ID, find by name + model ──
    _apply_late_invoice(env, active_langs)

    _logger.info("bluefox_branding: post_init_hook complete")
