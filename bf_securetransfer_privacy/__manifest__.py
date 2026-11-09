{
    "name": "Secure Transfer: Privacy Bridge (Quebec Law 25)",
    "summary": "Record in the destruction register what the secure transfer "
               "purge already deletes, and declare the purpose in the register "
               "of processing activities",
    # 18.0.1.2.2: the retention code is `ST-CONTENU` again, the one the whole
    #   fleet carries — a code is an identifier, and an identifier is not
    #   translated. The migration brings back a database installed in
    #   18.0.1.2.0 or 18.0.1.2.1, refreshes the French wording of the register
    #   texts that `noupdate` had frozen in their pre-18.0.1.2.0 version, and
    #   gives the purpose summary back its English source term, the fr_CA
    #   catalogue carrying its French — reloaded only where it can add that
    #   French without replacing a text of their own.
    # 18.0.1.2.1: on a French-speaking company, the delivered register texts
    #   (purpose summary, retention rule, retention policy) take their French
    #   version, on install and on upgrade, unless someone has adapted them;
    #   French labels without dashes.
    # 18.0.1.2.0: the source is written in English, and fr_CA.po carries the
    #   French. The register entry is written in the company's language. The
    #   purge entry declares a partial scope when the register offers one, and
    #   `_privacy_destruction_vals` lets another bridge enrich the entry
    #   before it is created.
    "version": "18.0.1.2.2",
    "category": "Services/Privacy",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # A bridge: it installs itself when both sides are present, and the secure
    # transfer works perfectly well without it.
    "auto_install": True,
    "post_init_hook": "post_init_hook",
    "depends": [
        "bf_securetransfer",
        "privacy_consent",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
    ],
}
