{
    "name": "Symbifox — LLM Provider",
    "summary": "Provider-agnostic LLM access (chat + document/vision extraction) for Symbifox modules",
    "version": "18.0.1.0.1",
    "category": "Technical",
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
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
