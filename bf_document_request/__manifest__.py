# Document requests: an employee requests documents from a client, who
# photographs or attaches them from a mobile page.
#
# It extends bf_securetransfer, the generic secure transfer base (watermark,
# access log, one-time codes, allow-lists). Nothing in this module belongs in
# that base: keep it here.
#
{
    "name": "Secure Transfer: Document Requests",
    # 18.0.1.2.2: consistent French vocabulary ("pièces", "échéance"), no
    #   dashes in the delivered policies' names; the client receives no
    #   receipt (it carried the download link of their own documents); a
    #   received document cannot be removed from its request; the counters
    #   are labelled and the deadline shows without seconds.
    # 18.0.1.2.1: the upload page is centred on a wide screen (it stayed
    #   stuck to the left) and shows the deadline in the requester's time
    #   zone, spelled out like in the emails (it showed UTC).
    # 18.0.1.2.0: the source is written in English, and fr_CA.po carries the
    #   French. Generic starter catalogue (identity document, proof of
    #   address, of insurance, of income; short and long policies), whose
    #   former identifiers a pre-migration renames, so that existing records
    #   stay the same. The public page follows the visitor's language, emails
    #   and text messages the client's.
    # 18.0.1.1.1: the two delivered policies take durations the base offers
    #   (24 h and 30 days; 30 days requires a brand whose ceiling reaches 30).
    "version": "18.0.1.2.2",
    "summary": "Request documents from a client by email or text message, "
               "which they photograph or attach from their phone or "
               "computer: catalogue, deadline, per-document retention, "
               "hash-chained log",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "category": "Productivity/Documents",
    "license": "Other proprietary",
    "depends": ["bf_securetransfer"],
    "data": [
        "security/ir.model.access.csv",
        "views/bf_document_expiry_policy_views.xml",
        "views/bf_document_request_views.xml",
        "views/bf_document_request_templates.xml",
        "views/secure_transfer_views.xml",
        "data/bf_document_expiry_policy_data.xml",
        "data/bf_document_type_data.xml",
        "data/bf_document_request_data.xml",
    ],
    "installable": True,
    "application": False,
}
