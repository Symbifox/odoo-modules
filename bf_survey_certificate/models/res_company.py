from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # The person who signs the certificates this company issues. The three
    # fields travel together: a certificate shows the image above the printed
    # name and title, and falls back to name and title alone when no image is
    # set. All three empty means an unsigned certificate.
    bf_certificate_signature = fields.Image(
        string="Certificate signature",
        max_width=512, max_height=256,
        # A handwritten signature is not for the portal or RPC to read:
        # res.company is readable by everyone logged in, so the field is
        # fenced to internal users. The certificate report reads it in sudo.
        groups="base.group_user",
        help="Handwritten signature printed on course certificates. "
             "Ideally dark ink on a transparent background.",
    )
    bf_certificate_signatory_name = fields.Char(
        string="Certificate signatory",
        help="Name printed under the signature on course certificates.",
    )
    bf_certificate_signatory_title = fields.Char(
        # "Certificate signatory title", not "Signatory title": the shorter
        # label collides with bf_letter_writer's field of the same name in
        # the translation catalogues.
        string="Certificate signatory title",
        translate=True,
        help="Title printed under the signatory's name, in the participant's "
             "language.",
    )
