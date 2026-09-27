"""Ce que les trois régimes de documents partagent quand ils sortent.

Les art. 1068.1, 1069 al. 2 et 1068.2 n'ont ni le même demandeur, ni le même
délai, ni le même avis. Mais au moment de sortir, ils font tous la même chose :
une pièce part vers un tiers, et il faut pouvoir dire quand, quoi, et si c'est
arrivé. C'est ce que ce mixin porte, une fois pour les trois.

⚠️ **Une remise n'est pas une lecture.** Un lien sécurisé expire. S'il expire
sans qu'une seule personne l'ait ouvert, le syndicat a envoyé et l'acquéreur n'a
rien reçu. Le module le dit (`secure_unread`), plutôt que de compter une remise
qui n'a pas eu lieu. C'est la seule chose que le pont ajoute au régime, et elle
compte : sur l'art. 1069 al. 2, le délai joue contre le syndicat.

⚠️ **La copie remise reste gelée sur la fiche — quand la pièce est produite.**
L'assistant d'envoi efface la sienne après le transfert, et il a raison : il ne
doit pas laisser les octets d'un tiers dans son dépôt, ni dans les sauvegardes
nocturnes. Mais une attestation régénérée six mois plus tard ne dirait pas la
même chose : elle est datée de sa remise. Les régimes qui remettent des pièces
JOINTES par le syndicat ne gèlent rien : la pièce jointe est déjà la copie de la
fiche, et la dupliquer ne ferait que doubler la liste des pièces.
"""
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Une remise se consulte peu de temps : ce n'est pas un partage de travail,
# c'est une pièce qu'on lit une fois. Le syndicat peut allonger au transfert.
# ⚠️ C'est une PRÉFÉRENCE, pas une exigence : la marque décide des durées
# qu'elle offre (voir `_secure_retention_days`).
DEFAULT_RETENTION_DAYS = 30


class BfPropertySecureDelivery(models.AbstractModel):
    _name = "bf.property.secure.delivery"
    _description = "Remise d'une pièce par transfert sécurisé"

    # ⚠️ Vrai pour les régimes dont le module PRODUIT la pièce, faux pour ceux
    # qui remettent ce que le syndicat a joint. Voir l'entête du fichier.
    _secure_freezes_copy = True

    secure_transfer_id = fields.Many2one(
        "secure.transfer",
        string="Transfert sécurisé",
        readonly=True,
        copy=False,
        help="Le transfert par lequel la pièce est partie.",
    )
    secure_sent_date = fields.Datetime(
        string="Remis par transfert le", readonly=True, copy=False
    )
    secure_download_count = fields.Integer(
        related="secure_transfer_id.download_count", string="Téléchargements"
    )
    secure_unread = fields.Boolean(
        string="Expiré sans avoir été ouvert",
        compute="_compute_secure_unread",
        help="⚠️ Le lien a expiré et personne ne l'a ouvert. Le syndicat a "
             "envoyé ; l'acquéreur n'a rien reçu. Sur l'art. 1069 al. 2, dont "
             "le délai joue contre le syndicat, la différence n'est pas "
             "théorique.",
    )
    secure_frozen_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Copie remise",
        readonly=True,
        copy=False,
        help="Ce qui est parti, tel quel. Le transfert efface la sienne ; "
             "celle-ci reste, parce qu'une pièce régénérée plus tard ne dirait "
             "pas la même chose.",
    )

    @api.depends("secure_transfer_id.state", "secure_transfer_id.download_count")
    def _compute_secure_unread(self):
        """⚠️ Calculé NON stocké : il dépend de l'état du transfert, qui bouge
        de son côté. Le stocker demanderait de suivre un modèle d'un autre
        module par une chaîne de dépendances qui n'existe pas."""
        for record in self:
            transfer = record.secure_transfer_id
            record.secure_unread = bool(
                transfer
                and transfer.state in ("expired", "deleted")
                and not transfer.download_count
            )

    # ── Ce que chaque régime doit dire de lui-même ──

    def _secure_recipient(self):
        """Le partenaire à qui la pièce est due. À définir par chaque régime."""
        raise NotImplementedError

    def _secure_documents(self):
        """[(nom_de_fichier, octets)] des pièces. À définir par chaque régime.

        Une liste et non une pièce : l'art. 1068.2 en transmet plusieurs, et
        les régimes qui n'en ont qu'une rendent une liste d'un élément.
        """
        raise NotImplementedError

    def _secure_joined_documents(self):
        """Les pièces que le syndicat a jointes à la fiche.

        Le cas ordinaire pour les régimes qui n'ont pas de rapport imprimable.

        🔴 Jamais celles du FIL. Un courriel versé
        à la fiche, l'original non caviardé qu'un copropriétaire a envoyé, la
        pièce d'un échange interne : tout cela porte le même `res_model` et le
        même `res_id` qu'une pièce jointe exprès. Les prendre toutes remettait
        au promettant acheteur ce que la revue de vie privée de l'art. 1068.2
        venait d'écarter. Seules partent les pièces jointes à la fiche
        elle-même, hors de tout message.
        """
        self.ensure_one()
        attachments = self.env["ir.attachment"].search(
            [("res_model", "=", self._name), ("res_id", "=", self.id)]
        )
        in_thread = self.env["mail.message"].sudo().search(
            [("model", "=", self._name), ("res_id", "=", self.id),
             ("attachment_ids", "!=", False)]
        ).attachment_ids
        attachments -= in_thread
        if not attachments:
            raise UserError(
                _("Joignez d'abord les pièces à remettre : le module n'en "
                  "fabrique pas à votre place.")
            )
        return [(a.name, base64.b64decode(a.datas or b"")) for a in attachments]

    def _secure_retention_days(self):
        """30 jours si la marque les offre, sinon la plus longue qu'elle offre.

        🔴 Les 30 jours étaient autrefois imposés, et le
        palier gratuit de `bf_securetransfer` plafonne à 7. Toute remise sur
        une marque gratuite mourait sur « Durée de mise à disposition non
        offerte », après que le gestionnaire eut tout préparé.
        """
        brand = self._secure_brand()
        limits = getattr(brand, "_effective_limits", None)
        choices = (limits() or {}).get("expiry_choices") if limits else None
        if not choices or DEFAULT_RETENTION_DAYS in choices:
            return DEFAULT_RETENTION_DAYS
        return max(choices)

    def _secure_subject(self):
        raise NotImplementedError

    def _secure_check_ready(self):
        """Refuse la remise quand le régime ne le permet pas encore."""
        return True

    # ── Ce que le pont doit trouver pour partir ──

    def _secure_brand(self):
        """La marque qui habille le lien.

        ⚠️ La marque par défaut, faute de mieux : un syndicat qui voudra son
        propre domaine sur les liens le dira, et ce choix ira alors sur la
        fiche du syndicat. Choisir en silence la première venue serait pire
        que de le dire ici.
        """
        Brand = self.env["secure.transfer.brand"].sudo()
        brand = Brand.search(
            [("active", "=", True), ("fixed_recipient", "=", False),
             ("is_default", "=", True)],
            limit=1,
        ) or Brand.search(
            [("active", "=", True), ("fixed_recipient", "=", False)],
            order="sequence, id",
            limit=1,
        )
        if not brand:
            raise UserError(
                _("Aucune marque de transfert sécurisé n'est configurée : le "
                  "lien n'aurait pas de domaine où vivre.")
            )
        return brand

    # ── La remise ──

    def action_secure_send(self):
        """Fait partir la pièce, puis rattache le transfert.

        ⚠️ L'état de la fiche n'est PAS écrit ici. Chaque régime a son propre
        passage à « remis », avec ses conditions et ses avis, et le dupliquer
        ferait deux vérités. Le pont transporte ; la machine à états reste chez
        elle.
        """
        # 🔴 D'abord le droit, ensuite l'état. Une fois le fichier téléversé
        # chez le tiers et le lien envoyé, aucun `rollback` ne le rappelle.
        #
        # ⚠️ `_ensure_organisation_decides` vient de `bf.property.organisation.authority`,
        # que les trois régimes portent DÉJÀ depuis leur module d'origine.
        # Le réclamer ici aussi par `_inherit` casse l'ordre de résolution :
        # le socle se retrouverait à la fois avant et après ce mixin.
        self._ensure_organisation_decides(_("Remettre une pièce à un tiers"))
        self.ensure_one()
        self._secure_check_ready()
        if self.secure_transfer_id:
            raise UserError(
                _("Cette pièce est déjà partie par transfert sécurisé le %s.")
                % fields.Datetime.to_string(self.secure_sent_date)
            )
        recipient = self._secure_recipient()
        if not recipient or not recipient.email:
            raise UserError(
                _("Le destinataire n'a pas d'adresse courriel : le lien "
                  "sécurisé n'aurait nulle part où aller.")
            )
        sender_email = self.env.user.email
        if not sender_email:
            raise UserError(
                _("Votre compte n'a pas d'adresse courriel : l'accusé d'envoi "
                  "n'aurait nulle part où aller.")
            )
        documents = self._secure_documents()
        Attachment = self.env["ir.attachment"]
        # La copie de travail, que l'assistant efface après l'envoi.
        working = Attachment
        for filename, content in documents:
            working |= Attachment.create(
                {
                    "name": filename,
                    "datas": base64.b64encode(content),
                    "res_model": False,
                    "res_id": 0,
                }
            )
        wizard = self.env["secure.transfer.send.wizard"].create(
            {
                "brand_id": self._secure_brand().id,
                "partner_ids": [(6, 0, [recipient.id])],
                "subject": self._secure_subject(),
                "attachment_ids": [(6, 0, working.ids)],
                "retention_days": self._secure_retention_days(),
                "sender_email": sender_email,
                # ⚠️ Le canal du code se règle par copropriété. Hérité en
                # silence, il valait « courriel » : le lien et le code
                # arrivaient dans la même boîte, donc un seul facteur.
                "otp_channel": self.organisation_id.sudo().secure_otp_channel,
            }
        )
        # ⚠️ L'assistant REND l'identifiant du transfert qu'il vient de créer.
        # Le retrouver par « le dernier créé par moi » céderait dès que deux
        # envois se croisent, et ce genre de défaut ne se voit qu'en production.
        try:
            result = wizard.action_send()
            transfer_id = (result or {}).get("res_id")
            if not transfer_id:
                raise UserError(
                    _("Le transfert sécurisé n'a pas rendu d'identifiant : "
                      "rien n'a été rattaché à la fiche.")
                )
        except Exception:
            # 🔴 Un envoi qui échoue ne laisse pas ses octets derrière lui.
            # Ces pièces n'ont ni modèle ni fiche : personne ne les reverrait,
            # et elles resteraient dans les sauvegardes nocturnes.
            working.exists().unlink()
            raise
        transfer = self.env["secure.transfer"].browse(transfer_id)
        frozen = Attachment
        if self._secure_freezes_copy:
            for filename, content in documents:
                frozen |= Attachment.create(
                    {
                        "name": filename,
                        "datas": base64.b64encode(content),
                        "res_model": self._name,
                        "res_id": self.id,
                    }
                )
        self.write(
            {
                "secure_transfer_id": transfer.id,
                "secure_sent_date": fields.Datetime.now(),
                "secure_frozen_attachment_id": frozen[:1].id,
            }
        )
        self.message_post(
            body=_(
                "Pièce remise par transfert sécurisé à %(who)s."
            )
            % {"who": recipient.display_name}
        )
        return transfer
