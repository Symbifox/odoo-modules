{
    'name': "Symbifox — Signature des consentements (Loi 25)",
    'version': '18.0.1.0.2',
    'category': 'Privacy/Compliance',
    'summary': "Signer les consentements Loi 25 avec le moteur natif bf_sign (au lieu de DocuSeal / LibreSign externes).",
    'description': """
Module-pont entre bf_sign et privacy_consent.

Ajoute « Envoyer pour signature » sur les consentements (privacy.consent) via le
mixin bf.sign.mixin : l'avis de consentement est rendu en PDF, une demande de
signature bf_sign liée est créée, et le document signé est reversé dans le fil
du consentement une fois signé.

Permet de faire signer les artefacts de consentement avec le moteur de signature
électronique simple (SES) natif bf_sign, sans dépendre d'un signataire externe
(DocuSeal ou LibreSign).

S'installe automatiquement lorsque bf_sign ET privacy_consent sont tous deux
présents.
""",
    'author': "Les services de consultation Blue Fox, Inc.",
    'website': "https://symbifox.com",
    'license': 'Other proprietary',
    'depends': ['bf_sign', 'privacy_consent'],
    'data': [
        'report/privacy_consent_form.xml',
        'views/privacy_consent_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
