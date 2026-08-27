{
    "name": "Symbifox — Signature des consentements (Loi 25)",
    "summary": "Faire signer un consentement Loi 25 avec la signature électronique "
               "maison, plutôt que par DocuSeal ou LibreSign",
    "version": "18.0.1.1.0",
    "category": "Services/Privacy",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Vie privée — Signature native (bf_sign)
=======================================

`privacy_consent` sait faire signer un consentement par DocuSeal et par
LibreSign, deux services externes. `bf_sign` fait la même chose à l'interne,
avec certificat de complétion et piste de vérification scellée — et le webhook
LibreSign du module dit lui-même que « l'intégration native bf_sign reste le
chemin recommandé ».

Ce pont ouvre ce troisième chemin. Il branche `privacy.consent` sur
`bf.sign.mixin`, si bien qu'un consentement s'envoie en signature comme un
devis ou une résolution corporative, et que la preuve reste dans la même piste
de vérification que le reste.

⚠️ **Les deux autres chemins restent en place.** Des consentements déjà signés
portent un identifiant DocuSeal ou LibreSign, et la preuve historique doit
rester lisible. Ce pont ajoute une voie, il n'en retire aucune.

⚠️ **La signature accorde le consentement, comme les deux autres voies.**
`_process_bf_sign_completion` fait exactement ce que fait
`_process_libresign_completion` : preuve `pdf_signed`, méthode de collecte
« signature », puis `action_grant()`. Diverger ici produirait deux définitions
de « consentement signé ».

⚠️ **Un consentement retiré ou refusé ne se fait pas signer.** La garde est au
point d'envoi, pas dans le traitement de la réponse : une fois la demande
partie, le signataire a le lien.
""",
    "depends": [
        "privacy_consent",
        "bf_sign",
    ],
    "data": [
        "report/privacy_consent_form.xml",
        "views/privacy_consent_views.xml",
    ],
}
