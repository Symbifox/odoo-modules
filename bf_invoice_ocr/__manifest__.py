{
    "name": "Blue Fox — Invoice OCR Scanner",
    "summary": "Extract vendor bill data from PDF attachments via the bf_llm gateway",
    "version": "18.0.1.0.0",
    "category": "Accounting",
    "author": "Blue Fox Inc.",
    "website": "https://bluefoxconsultant.com",
    "license": "LGPL-3",
    "depends": ["account", "bf_llm"],
    "data": [
        "views/account_move_views.xml",
        "data/ocr_cron.xml",
    ],
    "installable": True,
    "application": False,
}
