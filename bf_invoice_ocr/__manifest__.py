{
    "name": "Symbifox — Invoice OCR Scanner",
    "summary": "Extract vendor bill data from PDF attachments via the bf_llm gateway",
    # 18.0.1.5.0: les trois messages écrits dans ocr_error_message étaient
    #   français dans une source anglaise, et hors de tout _() ; le bouton
    #   « Scanner OCR » se lisait mal dans les deux langues. Les trois passent
    #   par _() en anglais et le bouton devient « Scan with OCR ».
    # 18.0.1.5.0: le module était déjà écrit en anglais, mais n'avait aucun
    #   catalogue français : un usager réglé en fr_CA le lisait en anglais.
    #   i18n/fr_CA.po et le .pot du module portent maintenant le français.
    "version": "18.0.1.5.0",
    'auto_install': True,
    "category": "Accounting",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["account", "bf_llm"],
    "data": [
        "views/account_move_views.xml",
        "data/ocr_cron.xml",
    ],
    "installable": True,
    "application": False,
}
