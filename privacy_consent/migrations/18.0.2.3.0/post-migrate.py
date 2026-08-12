"""Post-migration: Populate descriptions for purposes and update names.

Since noupdate="1" data files don't update existing records even when
ir_model_data.noupdate=False in Odoo 18, we apply changes directly via SQL.
"""
import logging

_logger = logging.getLogger(__name__)

# Purpose updates: (code, name_fr, name_en, summary_fr, summary_en)
PURPOSE_UPDATES = [
    (
        "marketing",
        "Communications marketing et infolettres",
        "Marketing Communications and Newsletters",
        """<p>Nous souhaitons recueillir votre <strong>consentement exprès</strong> à recevoir
certaines communications de Blue Fox à des fins d'information et de marketing.
Ces communications sont <strong>optionnelles</strong> et ne sont pas nécessaires
à l'exécution de nos services de consultation ou d'hébergement.</p>
<p>Les types de communications proposés incluent :</p>
<ul>
    <li><strong>Infolettres et contenus éducatifs</strong></li>
    <li><strong>Invitations à des événements, webinaires et ateliers</strong></li>
    <li><strong>Offres, promotions et propositions de services</strong></li>
</ul>
<p>Chaque catégorie est indépendante : vous pouvez accepter certaines fins et en refuser d'autres.
Vous pouvez refuser ou retirer votre consentement sans que cela n'affecte vos services existants.</p>""",
        """<p>We wish to collect your <strong>express consent</strong> to receive certain communications
from Blue Fox for information and marketing purposes. These communications are
<strong>optional</strong> and are not necessary for the delivery of our consulting or
hosting services.</p>
<p>The types of communications offered include:</p>
<ul>
    <li><strong>Newsletters and educational content</strong></li>
    <li><strong>Invitations to events, webinars and workshops</strong></li>
    <li><strong>Offers, promotions and service proposals</strong></li>
</ul>
<p>Each category is independent: you can accept some purposes and refuse others.
You may refuse or withdraw your consent without affecting your existing services.</p>""",
    ),
    (
        "recording",
        "Enregistrement ponctuel de rencontres",
        "Occasional Meeting Recording",
        """<p>Dans le cadre de ses mandats de consultation, d'implantation et de formation,
Blue Fox peut souhaiter, à l'occasion, <strong>enregistrer certaines rencontres</strong>
(visioconférences ou ateliers) afin de faciliter :</p>
<ul>
    <li>la prise de notes et le suivi du projet;</li>
    <li>la formation interne de l'équipe;</li>
    <li>ou, le cas échéant, la préparation de contenus de formation ou de communication.</li>
</ul>
<p>Ce consentement général est soumis à un <strong>avis clair</strong> au début de chaque
rencontre enregistrée et à votre <strong>droit de refuser</strong> ponctuellement.</p>""",
        """<p>As part of its consulting, implementation and training mandates, Blue Fox may wish,
on occasion, to <strong>record certain meetings</strong> (video conferences or workshops)
in order to facilitate:</p>
<ul>
    <li>note taking and project follow-up;</li>
    <li>internal team training;</li>
    <li>or, where applicable, preparation of training or communication content.</li>
</ul>
<p>This general consent is subject to a <strong>clear notice</strong> at the beginning of
each recorded meeting and your <strong>right to refuse</strong> on a case-by-case basis.</p>""",
    ),
    (
        "agent",
        "Installation d'agent Blue Fox",
        "Blue Fox Agent Installation",
        """<p>Blue Fox souhaite installer un ou des programmes dans le cadre d'un mandat professionnel,
sur un ordinateur, un serveur ou un autre dispositif dont vous avez la gestion ou la responsabilité.</p>
<p>Ce consentement est requis par la <strong>Loi canadienne anti-pourriel (LCAP)</strong> pour
l'installation de programmes d'ordinateur dans le cadre d'activités commerciales.</p>
<p>Les fins possibles incluent :</p>
<ul>
    <li>Sauvegarde et restauration de données</li>
    <li>Supervision / monitoring de l'état du système</li>
    <li>Télémaintenance / assistance à distance</li>
    <li>Sécurité (ex. agent antivirus, agent EDR)</li>
</ul>
<p>Blue Fox ne configure ce programme que pour les fins strictement nécessaires au mandat,
et prend des mesures pour limiter les données collectées au minimum requis.</p>""",
        """<p>Blue Fox wishes to install one or more programs as part of a professional mandate,
on a computer, server or other device that you manage or are responsible for.</p>
<p>This consent is required by <strong>Canada's Anti-Spam Legislation (CASL)</strong> for
the installation of computer programs in the course of commercial activities.</p>
<p>Possible purposes include:</p>
<ul>
    <li>Data backup and restore</li>
    <li>Supervision / monitoring of system status</li>
    <li>Remote maintenance / remote assistance</li>
    <li>Security (e.g., antivirus agent, EDR agent)</li>
</ul>
<p>Blue Fox configures this program only for strictly necessary purposes for the mandate,
and takes measures to limit the data collected to the minimum required.</p>""",
    ),
    (
        "sensibles",
        "Traitements à risque élevé & données sensibles",
        "High-Risk Processing & Sensitive Data",
        """<p>Ce consentement vise le traitement de renseignements personnels présentant un
<strong>risque élevé</strong> pour votre vie privée, en raison de la nature sensible
des données ou du contexte.</p>
<p>Les catégories de renseignements concernés peuvent inclure :</p>
<ul>
    <li>Données de santé ou d'évaluation psychosociale</li>
    <li>Données financières détaillées (revenus personnels, dettes, etc.)</li>
    <li>Renseignements biométriques</li>
    <li>Données sur des enfants / mineurs</li>
    <li>Autres données particulièrement sensibles</li>
</ul>
<p>Blue Fox s'engage à n'entreprendre ce traitement que si cela est
<strong>strictement nécessaire</strong> et adéquatement protégé.</p>""",
        """<p>This consent applies to the processing of personal information presenting a
<strong>high risk</strong> to your privacy, due to the sensitive nature of the data
or the context.</p>
<p>The categories of information involved may include:</p>
<ul>
    <li>Health data or psychosocial assessment data</li>
    <li>Detailed financial data (personal income, debts, etc.)</li>
    <li>Biometric information</li>
    <li>Data about children / minors</li>
    <li>Other particularly sensitive data</li>
</ul>
<p>Blue Fox undertakes to carry out this processing only if it is
<strong>strictly necessary</strong> and adequately protected.</p>""",
    ),
    (
        "training",
        "Formation interne Blue Fox",
        "Blue Fox Internal Training",
        """<p>Blue Fox peut souhaiter utiliser des extraits de rencontres, ateliers ou sessions de travail
à des <strong>fins de formation interne</strong> pour son équipe. Cela peut inclure :</p>
<ul>
    <li>Des enregistrements vidéo ou audio de rencontres/ateliers;</li>
    <li>Des transcriptions ou résumés de sessions;</li>
    <li>Des captures d'écran ou démonstrations techniques.</li>
</ul>
<p>Ce contenu est utilisé <strong>exclusivement à l'interne</strong> et n'est pas partagé
publiquement. Votre consentement est requis avant toute utilisation.</p>""",
        """<p>Blue Fox may wish to use excerpts from meetings, workshops or work sessions
for <strong>internal training</strong> purposes for its team. This may include:</p>
<ul>
    <li>Video or audio recordings of meetings/workshops;</li>
    <li>Transcriptions or session summaries;</li>
    <li>Screenshots or technical demonstrations.</li>
</ul>
<p>This content is used <strong>exclusively internally</strong> and is not shared
publicly. Your consent is required before any use.</p>""",
    ),
]

# Notice name updates: (xml_id, new_name_fr, new_name_en)
NOTICE_NAME_UPDATES = [
    ("notice_marketing", "F-01 — Consentement aux communications marketing et infolettres",
     "F-01 — Consent to Marketing Communications and Newsletters"),
    ("notice_recording", "F-02 — Consentement à l'enregistrement ponctuel de rencontres",
     "F-02 — Consent to Occasional Meeting Recording"),
    ("notice_recording_audio", "Avis — Enregistrement audio", "Notice — Audio Recording"),
    ("notice_transcription", "Avis — Transcription des communications",
     "Notice — Communication Transcription"),
    ("notice_reference", "Avis — Utilisation comme référence client",
     "Notice — Client Reference"),
    ("notice_logo", "Avis — Utilisation du logo", "Notice — Logo Usage"),
    ("notice_case_study", "Avis — Étude de cas", "Notice — Case Study"),
    ("notice_third_party", "Avis — Partage avec des tiers",
     "Notice — Third-Party Sharing"),
    ("notice_service", "Avis — Communications de service (informatif)",
     "Notice — Service Communications (informational)"),
    ("notice_software_install", "F-03 — Consentement à l'installation d'un agent Blue Fox",
     "F-03 — Consent to Blue Fox Agent Installation"),
    ("notice_high_risk",
     "F-07 — Consentement pour traitements à risque élevé & données sensibles",
     "F-07 — Consent for High-Risk Processing & Sensitive Data"),
    ("notice_training", "Avis — Formation interne Blue Fox",
     "Notice — Blue Fox Internal Training"),
]


def migrate(cr, version):
    if not version:
        return

    _logger.info("privacy_consent post-migrate: updating purpose descriptions and names")

    import json

    for code, name_fr, name_en, summary_fr, summary_en in PURPOSE_UPDATES:
        name_json = json.dumps({"en_US": name_en, "fr_CA": name_fr})
        summary_json = json.dumps({"en_US": summary_en, "fr_CA": summary_fr})
        cr.execute(
            """
            UPDATE privacy_purpose
            SET name = %s::jsonb,
                plain_language_summary = %s::jsonb
            WHERE code = %s
            """,
            (name_json, summary_json, code),
        )
        _logger.info("  Updated purpose code=%s (%d rows)", code, cr.rowcount)

    _logger.info("privacy_consent post-migrate: updating notice names")

    for xmlid, name_fr, name_en in NOTICE_NAME_UPDATES:
        name_json = json.dumps({"en_US": name_en, "fr_CA": name_fr})
        cr.execute(
            """
            UPDATE privacy_notice SET name = %s::jsonb
            WHERE id = (
                SELECT res_id FROM ir_model_data
                WHERE module = 'privacy_consent' AND name = %s
            )
            """,
            (name_json, xmlid),
        )
        _logger.info("  Updated notice %s (%d rows)", xmlid, cr.rowcount)

    # Fix email template: remove {% if %} Jinja blocks that Odoo 18
    # HTML sanitizer doesn't process, for ALL locale keys
    _logger.info("privacy_consent post-migrate: fixing email template Jinja blocks")
    cr.execute(
        """
        SELECT res_id FROM ir_model_data
        WHERE module = 'privacy_consent' AND name = 'mail_template_consent_request'
        """
    )
    row = cr.fetchone()
    if row:
        template_id = row[0]
        for lang in ("en_US", "fr_CA"):
            cr.execute(
                """
                UPDATE mail_template SET body_html = jsonb_set(
                    body_html, %s,
                    to_jsonb(
                        replace(replace(
                            body_html->>%s,
                            '{%% if object.notice_version_id and object.notice_version_id.body %%}', ''
                        ), '{%% endif %%}', '')
                    )
                ) WHERE id = %s AND body_html->>%s LIKE '%%{%% if%%'
                """,
                ([lang], lang, template_id, lang),
            )
            cr.execute(
                """
                UPDATE mail_template SET body_html = jsonb_set(
                    body_html, %s,
                    to_jsonb(
                        replace(
                            body_html->>%s,
                            '{{ object.notice_version_id.body }}',
                            '{{ object.notice_version_id.body if object.notice_version_id else object.purpose_id.plain_language_summary or '''' }}'
                        )
                    )
                ) WHERE id = %s AND body_html->>%s LIKE '%%notice_version_id.body %%'
                AND body_html->>%s NOT LIKE '%%notice_version_id.body if%%'
                """,
                ([lang], lang, template_id, lang, lang),
            )
        _logger.info("  Fixed email template Jinja blocks")

    # Reset noupdate=True for all managed purposes and notices
    cr.execute(
        """
        UPDATE ir_model_data SET noupdate = TRUE
        WHERE module = 'privacy_consent'
        AND model IN ('privacy.purpose', 'privacy.notice', 'mail.template')
        """
    )
    _logger.info("  Reset noupdate=True for managed records")
