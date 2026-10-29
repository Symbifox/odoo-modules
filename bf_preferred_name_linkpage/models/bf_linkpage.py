"""Les pronoms de la personne sur sa page de liens, et les traces de son nom.

La page est rendue en sudo pour le visiteur public (`_resolve_slug`) : tout
ce qu'elle montre de la fiche passe par `_bf_public_pronouns`, qui ne rend
rien pour une page d'organisation, une page ponctuelle ou une société, ni pour
la page qu'un autre usager a faite au nom de la fiche.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import is_html_empty

# Les champs suivis au fil de la page qui portent le nom de la personne : son
# titre et son adresse, mais aussi la fiche et l'usager de la page, dont la
# trace garde le nom affiché en texte.
HISTORY_FIELDS = ("name", "slug", "partner_id", "user_id")


class BfLinkpage(models.Model):
    _inherit = "bf.linkpage"

    bf_show_pronouns = fields.Boolean(
        string="Show my pronouns",
        default=True,
        help="Show, under the name on the public page, the pronouns the "
             "person gave on their contact form. Unchecked, they stay on the "
             "contact form and in the email signature, but not on this page.",
    )

    # -- Les adresses retirées -------------------------------------------------

    def write(self, vals):
        # Toute adresse quittée est réservée, par le geste comme à la main :
        # un lien imprimé ne doit jamais mener chez quelqu'un d'autre.
        if vals.get("slug"):
            Retired = self.env["bf.linkpage.retired.slug"]
            for page in self:
                if page.slug and page.slug != vals["slug"]:
                    Retired._retire(page.slug, page)
        return super().write(vals)

    @api.constrains("slug", "partner_id", "user_id", "kind")
    def _check_slug_not_retired(self):
        Retired = self.env["bf.linkpage.retired.slug"]
        for page in self:
            if Retired._is_retired(page.slug, page):
                # Rien sur qui l'a quittée, ni qu'elle a été quittée. Le libellé diffère de celui d'une
                # adresse prise, qui nomme la page : risque résiduel assumé.
                raise ValidationError(_(
                    "The URL slug \"%s\" is not available. Choose another one.",
                    page.slug))

    def _generate_slug(self, value, exclude_id=None):
        slug = base = super()._generate_slug(value, exclude_id=exclude_id)
        page = self.browse(exclude_id) if exclude_id else self.browse()
        Retired = self.env["bf.linkpage.retired.slug"]
        suffix = 2
        while Retired._is_retired(slug, page):
            # Suffixe sur l'adresse de base, pas sur la valeur : une valeur
            # vide donnait « 2 ».
            slug = super()._generate_slug("%s %d" % (base, suffix), exclude_id=exclude_id)
            suffix += 1
        return slug

    def unlink(self):
        # Une page supprimée libérait son adresse : un départ suivi d'un
        # homonyme suffisait pour qu'un code QR imprimé mène chez lui.
        Retired = self.env["bf.linkpage.retired.slug"]
        for page in self.with_context(active_test=False):
            Retired._retire(page.slug, page)
        return super().unlink()

    # -- Les pronoms ------------------------------------------------------------

    def _bf_public_pronouns(self):
        """Les pronoms à montrer sous le nom, ou une chaîne vide.

        Seulement sur la page d'une personne qui en est elle-même l'usager
        (la seule qui puisse décocher la case), quand elle les a donnés et que
        la case est cochée. Ni sur la page qu'un AUTRE usager a faite au nom
        de la fiche, ni sur la page d'un contact ou d'un employé sans compte :
        le portail n'a pas accès aux pages, la case n'y serait jamais entre
        ses mains. Sur une ligne : ils ont été
        saisis par la personne, au portail aussi.
        """
        self.ensure_one()
        page = self.sudo()
        partner = page.partner_id
        if (not page.bf_show_pronouns or page.kind != "owner" or not partner
                or partner.is_company or page.user_id.partner_id != partner):
            return ""
        return " ".join((partner.bf_pronouns or "").split())

    @api.model
    def _bf_owner_pages(self, partners):
        """Les pages de ces personnes, archivées comprises, en sudo.

        Une page archivée peut revenir : son titre doit avoir suivi le nom
        entre-temps.
        """
        if not partners:
            return self.sudo().browse()
        return self.sudo().with_context(active_test=False).search([
            ("kind", "=", "owner"),
            ("partner_id", "in", partners.ids),
        ])

    def _bf_forget_tracking(self, *fnames):
        """Retire ces champs du suivi en attente de la transaction.

        Comme `forget_name_tracking` du socle, pour la page : le suivi relève
        la valeur à la PREMIÈRE écriture suivie de la transaction, si bien
        qu'un `mail_notrack` sur la seule écriture du titre ou de l'adresse
        laisserait passer l'ancienne valeur relevée plus tôt.
        """
        initial = self.env.cr.precommit.data.get(f"mail.tracking.{self._name}") or {}
        for record_id in self.ids:
            values = initial.get(record_id)
            if values:
                for fname in fnames:
                    values.pop(fname, None)

    def _bf_set_untracked(self, vals):
        """Écrit ces valeurs sans trace au fil de la page."""
        self._bf_forget_tracking(*vals)
        self.with_context(mail_notrack=True).write(vals)

    def _bf_follow_partner_name(self):
        """Pose, sans trace, le nom de la fiche en titre de ces pages."""
        for page in self.exists():
            new_name = page.partner_id.name
            if new_name and page.name != new_name:
                page._bf_set_untracked({"name": new_name})

    def _bf_erase_name_history(self):
        """Efface du fil de ces pages ce qui y garde le nom de la personne.

        Les traces du titre, de l'adresse, de la fiche et de l'usager, et les
        avis d'assignation (« Vous avez été assigné à <titre> »). Rend le
        nombre de traces effacées.
        """
        if not self:
            return 0
        Tracking = self.env["mail.tracking.value"].sudo()
        fields_ = self.env["ir.model.fields"].sudo().search([
            ("model", "=", self._name), ("name", "in", HISTORY_FIELDS),
        ])
        traces = Tracking.search([
            ("field_id", "in", fields_.ids),
            ("mail_message_id.model", "=", self._name),
            ("mail_message_id.res_id", "in", self.ids),
        ])
        # Comme le socle : un message de suivi qui n'a plus rien à montrer
        # part avec ses traces.
        messages = traces.mail_message_id
        count = len(traces)
        traces.unlink()
        messages.filtered(
            lambda m: not m.tracking_value_ids and not m.attachment_ids
            and is_html_empty(m.body)
        ).unlink()
        self.env["mail.message"].sudo().search([
            ("model", "=", self._name),
            ("res_id", "in", self.ids),
            ("message_type", "=", "user_notification"),
        ]).unlink()
        # Le nom de la page est recopié dans chaque message et chaque activité
        # à leur création (`record_name`, `res_name`) : hors du fil, mais
        # lisible par l'API.
        for page in self:
            self.env["mail.message"].sudo().search([
                ("model", "=", self._name), ("res_id", "=", page.id),
                ("record_name", "!=", page.name),
            ]).write({"record_name": page.name})
            self.env["mail.activity"].sudo().search([
                ("res_model", "=", self._name), ("res_id", "=", page.id),
                ("res_name", "!=", page.name),
            ]).write({"res_name": page.name})
        return count
