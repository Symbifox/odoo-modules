{
    "name": "Symbifox — LLM Provider",
    "summary": "Provider-agnostic LLM access (chat + document/vision extraction) for Symbifox modules",
    # 18.0.1.1.0: le modèle par défaut suit la famille courante de Claude.
    # 18.0.1.2.0: neuf messages d'erreur étaient écrits en français sans accents
    #   dans la source anglaise du module : ils passent à l'anglais. Les
    #   adaptateurs ne portent pas d'environnement : ils traduisent par
    #   `self.r.env._()`, jamais par `_()`, qui devinerait la langue.
    # 18.0.1.2.0: le module était déjà écrit en anglais, mais n'avait aucun
    #   catalogue français : un usager réglé en fr_CA le lisait en anglais.
    #   i18n/fr_CA.po et le .pot du module portent maintenant le français.
    "version": "18.0.1.2.0",
    "category": "Technical",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["base"],
    # cryptography is hard-required (API key encryption). pdf2image/poppler and
    # pytesseract/tesseract-ocr are SOFT/optional (import-guarded) — see README.
    "external_dependencies": {"python": ["cryptography"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/bf_llm_provider_views.xml",
        "views/res_config_settings_views.xml",
        "data/bf_llm_data.xml",
    ],
    "installable": True,
    "application": False,
}
