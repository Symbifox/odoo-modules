# Privacy bridge of document requests: it connects the retention that
# bf_document_request already applies to the privacy register of privacy_consent.
#
{
    "name": "Document Requests: Privacy Bridge (Quebec Law 25)",
    "summary": "Attach the expiry policy to the retention schedule, and record "
               "in the destruction register what a request's expiry deletes",
    # 18.0.1.1.1: on a French-speaking company, the delivered register texts
    #   take their French version, on install and on upgrade, unless someone
    #   has adapted them (mechanism in bf_securetransfer_privacy/hooks.py);
    #   French labels without dashes ("échéance").
    # 18.0.1.1.0: the source is written in English, and fr_CA.po carries the
    #   French. The delivered data points to the generic catalogue of
    #   bf_document_request 18.0.1.2.0 (its new identifiers). The register
    #   entry follows the language the base bridge writes it in.
    # 18.0.1.0.1: the request also goes out by email.
    "version": "18.0.1.1.1",
    "category": "Productivity/Documents",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # A bridge, on the pattern of bf_securetransfer_privacy: it installs itself
    # when both sides are present, and document requests work perfectly well
    # without it.
    "auto_install": True,
    "post_init_hook": "post_init_hook",
    "depends": [
        "bf_document_request",
        "bf_securetransfer_privacy",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
        "views/bf_document_expiry_policy_views.xml",
    ],
}
