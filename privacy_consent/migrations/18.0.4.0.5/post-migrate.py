"""Post-migration — les boutons des rappels cessent d'être morts.

Rétroportage du volet « CTA morts » du lot `cq_consent` 18.0.4.10.0 vers les
locataires qui portent encore `privacy_consent`.

Trois gabarits envoyaient le destinataire vers une route ``auth="user"`` :

* ``mail_template_consent_reminder_1``  → ``/my/privacy/consent/<id>``
* ``mail_template_consent_reminder_2``  → ``/my/privacy/consent/<id>``
* ``mail_template_consent_expiring``    → ``/my/privacy/consent/<id>/renew``

Or la personne qu'on relance n'a pas nécessairement de compte portail : le
courriel de demande, lui, a toujours utilisé le lien public à jeton. Un rappel
dont le bouton mène à un écran de connexion est un rappel qui ne sert à rien —
et c'est précisément la population qu'on relance qui n'a pas de compte.

⚠ DIVERGENCE ASSUMÉE AVEC LA COPIE CQ, ET ELLE EST DÉLIBÉRÉE. Sur CQ, le bouton
de ``consent_expiring`` pointe vers ``…/renew`` parce que la 18.0.4.9.0 y a
ajouté un GET qui rend un écran de confirmation. Ici, ``public_consent_renew``
est déclarée ``methods=["POST"]`` : un GET depuis un bouton de courriel
répondrait **405**, soit un lien mort remplacé par un autre lien mort. Les trois
boutons pointent donc vers la page publique du consentement
``/privacy/consent/<id>/<token>``, qui est en GET, publique, et qui porte déjà le
formulaire de renouvellement (``renew_url``, views/portal_templates.xml) ainsi
que le texte « Votre consentement expirera le … ». Le parcours est complet.

``data/mail_template.xml`` et ``data/mail_template_sequence.xml`` sont en
``noupdate="1"`` : un ``-u`` ne réécrit PAS les enregistrements déjà en base.
D'où cette reprise, qui applique aux corps VIVANTS exactement la substitution
faite dans le XML.

Quatre points tenus :

1. **Toutes les clés jsonb réellement présentes** sont patchées, et AUCUNE n'est
   créée. ⚠ La version CQ boucle sur un tuple ``("en_US", "fr_CA")`` en dur ; sur
   un locataire dont le corps n'a que ``en_US``,
   écrire sous ``lang="fr_CA"`` **fabriquerait** une clé qui n'existait pas, ou
   lèverait « Invalid language code » si la langue est inactive. On lit donc les
   clés du jsonb et on n'écrit que celles-là.
2. **Substitution en SQL**, pas par l'ORM : elle contourne la validation de
   langue d'Odoo, qui refuserait une clé présente dans le jsonb mais dont la
   langue est désactivée sur le locataire.
3. **Idempotence** : la substitution ne s'applique que si l'ancienne forme est
   présente. Rejouer la migration ne fait rien.
4. **Abstention journalisée** : un corps que le client a personnalisé au point de
   ne plus contenir l'ancre est laissé tranquille, pas réécrit à l'aveugle.

⚠ Volontairement HORS périmètre : les boutons qui mènent au CENTRE DE
PRÉFÉRENCES (``mail_template_consent_renewal_confirmation``,
``mail_template_consent_granted_confirmation``, et le second bouton de
``consent_expiring``). Il n'existe aucune route publique de préférences : les
rediriger vers la fiche d'un consentement unique changerait ce que la personne
voit. C'est un arbitrage produit, pas une substitution mécanique.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# (xmlid, ancienne forme, nouvelle forme)
# ⚠ Copie conforme de ce que portent désormais les fichiers XML. Toute retouche
# ici doit être réalignée sur data/mail_template*.xml, sinon une base neuve et une
# base migrée divergent — mode de panne déjà rencontré sur ce module.
PUBLIC_LINK = '/privacy/consent/{{ object.id }}/{{ object.access_token }}"'
SUBSTITUTIONS = (
    (
        "privacy_consent.mail_template_consent_reminder_1",
        '/my/privacy/consent/{{ object.id }}"',
        PUBLIC_LINK,
    ),
    (
        "privacy_consent.mail_template_consent_reminder_2",
        '/my/privacy/consent/{{ object.id }}"',
        PUBLIC_LINK,
    ),
    (
        "privacy_consent.mail_template_consent_expiring",
        '/my/privacy/consent/{{ object.id }}/renew"',
        PUBLIC_LINK,
    ),
)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    patched = 0
    skipped = 0

    for xmlid, old, new in SUBSTITUTIONS:
        template = env.ref(xmlid, raise_if_not_found=False)
        if not template:
            _logger.warning(
                "privacy_consent CTA : gabarit %s introuvable, abstention.", xmlid
            )
            skipped += 1
            continue

        cr.execute(
            "SELECT jsonb_object_keys(body_html) FROM mail_template WHERE id = %s",
            (template.id,),
        )
        langs = [row[0] for row in cr.fetchall()]
        if not langs:
            _logger.warning(
                "privacy_consent CTA : corps vide sur %s, abstention.", xmlid
            )
            skipped += 1
            continue

        for lang in langs:
            cr.execute(
                "SELECT body_html ->> %s FROM mail_template WHERE id = %s",
                (lang, template.id),
            )
            row = cr.fetchone()
            body = row[0] if row else None
            if not body:
                continue

            if new in body and old not in body:
                # Déjà repris — rien à faire pour cette langue.
                continue
            if old not in body:
                _logger.warning(
                    "privacy_consent CTA : ancre absente du corps %s de %s "
                    "(corps personnalisé ?), abstention.", lang, xmlid,
                )
                skipped += 1
                continue

            cr.execute(
                "UPDATE mail_template SET body_html = jsonb_set(body_html, %s, to_jsonb(%s::text)) "
                "WHERE id = %s",
                ("{%s}" % lang, body.replace(old, new), template.id),
            )
            patched += 1

    _logger.info(
        "privacy_consent CTA : %s corps de gabarit repris, %s abstentions.",
        patched, skipped,
    )
