"""L'étiquette QR : une pastille sans puce, imprimée d'avance.

Une étiquette tirée d'un lot porte le geste « vierge » jusqu'à son association.
Elle garde son code (ce que le QR imprime) et son numéro (ce qu'on lit) toute
sa vie : réinitialiser la rend vierge, ne la réimprime pas.

🔴 **Qui associe :** la gestion des pastilles, plus les groupes que la société a
choisis. Ces groupes n'ont pas le droit d'écrire sur les pastilles dans le socle :
l'association passe donc par une méthode qui contrôle d'abord, puis écrit en
sudo les seuls champs d'une association. Jamais un droit d'écriture général.
"""
import json
from urllib.parse import urlparse

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class BfNfcTag(models.Model):
    _inherit = "bf.nfc.tag"

    # 🔴 Le socle (bf_nfc, jusqu'à sa 2.6.x au moins) suit ``res_id`` dans l'historique. Odoo ne sait
    # pas suivre un Many2oneReference : toute écriture de la fiche visée lève
    # « Unsupported tracking on field res_id » au moment où la transaction se
    # termine, pas au moment de l'écriture. Les essais qui ne vont jamais jusque-là
    # (tous ceux d'une TransactionCase, et un parcours annulé en shell) passent au
    # vert ; le premier clic sur « Associer » dans un navigateur plante. La fiche
    # visée reste tracée : ``res_model`` est suivi, et chaque association ou
    # réinitialisation laisse une note qui nomme la fiche.
    res_id = fields.Many2oneReference(tracking=False)
    qr_batch_id = fields.Many2one("bf.qr.batch", string="Lot", index=True,
                                  ondelete="restrict", readonly=True, copy=False)
    qr_prefixe = fields.Char(string="Préfixe", readonly=True, copy=False, index=True)
    qr_numero = fields.Integer(string="Numéro", readonly=True, copy=False, index=True)
    qr_reference = fields.Char(
        string="N° d'étiquette", readonly=True, copy=False, index=True,
        help="Imprimé sous le code et lu à voix haute : « QR-0042 ». Ne change jamais.")
    qr_vierge = fields.Boolean(string="À associer", compute="_compute_qr_vierge", store=True)
    qr_public = fields.Boolean(
        string="S'ouvre sans compte", tracking=True,
        help="Un visiteur sans compte qui scanne l'étiquette arrive directement à "
             "l'adresse, ou à la page publique de la fiche (page de liens publiée, "
             "événement publié). Jamais pour un geste qui écrit.")

    _sql_constraints = [
        ("qr_reference_unique", "unique(company_id, qr_reference)",
         "Ce numéro d'étiquette existe déjà dans cette société."),
    ]

    @api.model
    def _search(self, domain, *args, **kwargs):
        # Posé par les routes de l'application mobile : voir controllers/mobile.py.
        if self.env.context.get("bf_qr_sans_vierges"):
            domain = [("qr_vierge", "=", False)] + list(domain or [])
        return super()._search(domain, *args, **kwargs)

    @api.depends("gesture_id.kind")
    def _compute_qr_vierge(self):
        for tag in self:
            tag.qr_vierge = tag.gesture_id.kind == "vierge"

    @api.depends("qr_batch_id")
    def _compute_url(self):
        super()._compute_url()
        # ⚠️ Une étiquette de lot imprime la porte /q/, qui sait accueillir un
        # visiteur sans compte ; la porte /nfc/ l'enverrait à l'écran de connexion.
        for tag in self:
            if tag.qr_batch_id and tag.code and not tag.sdm_enabled:
                tag.url = "%s/q/%s" % (tag.get_base_url().rstrip("/"), tag.code)

    # ------------------------------------------------------------------
    # Qui peut associer et réinitialiser
    # ------------------------------------------------------------------
    def _peut_associer(self, user=None):
        """Vrai si ``user`` peut associer ou réinitialiser TOUTES ces étiquettes."""
        user = user or self.env.user
        if user._is_public():
            return False
        # 🔴 La société AVANT le rôle. Les écritures qui suivent se font en sudo :
        # un gestionnaire d'une société qui passait ici sans ce contrôle vidait ou
        # redirigeait par RPC les étiquettes imprimées d'une autre société, que la
        # règle de cloison des pastilles lui interdit pourtant d'écrire.
        etiquettes = self.sudo()
        if any(tag.company_id not in user.company_ids for tag in etiquettes):
            return False
        if user.has_group("bf_nfc.group_nfc_manager"):
            return True
        groupes_perso = user.groups_id
        for tag in etiquettes:
            if not (tag.company_id.bf_qr_groupe_ids & groupes_perso):
                return False
        return bool(self)

    def _exiger_association(self):
        if not self._peut_associer():
            raise AccessError(_("Vous ne pouvez pas associer ni réinitialiser ces étiquettes. "
                                "Demandez à la gestion des pastilles."))
        if any(not tag.qr_batch_id for tag in self.sudo()):
            raise UserError(_("Seules les étiquettes QR tirées d'un lot s'associent ici."))

    # ------------------------------------------------------------------
    # Association
    # ------------------------------------------------------------------
    def action_associer_qr(self):
        self.ensure_one()
        self._exiger_association()
        if not self.sudo().qr_vierge:
            raise UserError(_("Cette étiquette est déjà associée. Réinitialisez-la d'abord."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Associer « %s »", self.sudo().qr_reference or self.sudo().name),
            "res_model": "bf.qr.associer",
            "view_mode": "form",
            "target": "new",
            "context": {"default_tag_id": self.id},
        }

    def _associer(self, geste, cible=None, url=None, libelle=None, place=None, public=False):
        """Écrit une association vérifiée. Le seul chemin, pour l'écran comme pour le tableur.

        🔴 Les contrôles sont ICI et pas dans les assistants : le tableur et
        l'écran doivent refuser exactement la même chose.
        """
        self._exiger_association()
        gestion = self.env.user.has_group("bf_nfc.group_nfc_manager")
        geste = geste.sudo()
        if not geste or geste.kind == "vierge":
            raise UserError(_("Choisissez ce que l'étiquette doit faire."))
        if geste.reserve_gestion and not gestion:
            raise AccessError(_("Le geste « %s » est réservé à la gestion des pastilles.", geste.name))
        valeurs = {"gesture_id": geste.id, "res_model": False, "res_id": False, "params": False}
        if geste.kind == "url":
            adresse = (url or "").strip()
            morceaux = urlparse(adresse)
            if morceaux.scheme.lower() not in ("https", "http") or not morceaux.netloc:
                raise UserError(_("« %s » n'est pas une adresse web (https ou http).", adresse))
            valeurs["params"] = json.dumps({"url": adresse})
        if cible:
            if cible._name not in self._modeles_cibles(gestion=gestion):
                raise UserError(_("Une étiquette ne peut pas viser une fiche de type « %s ».",
                                  cible._description))
            # La personne qui associe doit pouvoir lire la fiche : on n'attache pas
            # une étiquette à ce qu'on ne voit pas.
            # 🔴 ``with_user`` et pas ``sudo(False)`` : la fiche arrive avec l'env de
            # qui l'a lue (un assistant, un tableur, un appel en superutilisateur).
            # ``sudo(False)`` garde CET utilisateur ; l'essai l'a vu contrôler les
            # droits de l'administrateur à la place de ceux du concierge.
            cible.with_user(self.env.user).check_access("read")
            valeurs.update({"res_model": cible._name, "res_id": cible.id})
        if geste.needs_target and not cible:
            raise UserError(_("Le geste « %s » a besoin d'une fiche.", geste.name))
        if geste.target_model and (not cible or cible._name != geste.target_model):
            raise UserError(_("Le geste « %s » vise un autre type de fiche.", geste.name))
        # Une porte publique ne sert jamais un geste qui écrit.
        valeurs["qr_public"] = bool(public) and not geste.writes
        for tag in self.sudo():
            if not tag.qr_vierge:
                raise UserError(_("L'étiquette %s est déjà associée. Réinitialisez-la d'abord.",
                                  tag.qr_reference or tag.name))
            propres = dict(valeurs,
                           name=(libelle or "").strip() or (cible.display_name if cible else False)
                           or tag.qr_reference or tag.name,
                           place=(place or "").strip() or False)
            tag.write(propres)
            quoi = cible.display_name if cible else valeurs.get("params") and url
            tag.message_post(
                body=Markup("<p>%s</p>") % escape(_(
                    "Étiquette associée par %(qui)s : %(geste)s, %(quoi)s.",
                    qui=self.env.user.name, geste=geste.name, quoi=quoi or _("sans fiche"))),
                message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    # ------------------------------------------------------------------
    # Réinitialisation
    # ------------------------------------------------------------------
    def action_reinitialiser_qr(self):
        """Le bouton de la fiche. Sa confirmation est posée dans la vue."""
        self._exiger_association()
        self._reinitialiser()
        return True

    def action_reinitialiser_qr_lot(self):
        """Depuis la liste : UNE confirmation pour tout le lot sélectionné."""
        self._exiger_association()
        return {
            "type": "ir.actions.act_window",
            "name": _("Réinitialiser des étiquettes"),
            "res_model": "bf.qr.reinitialiser",
            "view_mode": "form",
            "target": "new",
            "context": {"default_tag_ids": [(6, 0, self.ids)]},
        }

    def _reinitialiser(self):
        """Rend les étiquettes vierges. Le code, le numéro et le journal restent.

        ⚠️ Le journal n'est pas touché : on veut encore savoir ce qu'une étiquette
        a fait avant d'être recollée ailleurs.
        """
        self._exiger_association()
        vierge = self.env.ref("bf_qr_manager.gesture_vierge")
        for tag in self.sudo():
            if tag.qr_vierge:
                continue
            avant = tag.cible.display_name if tag.cible else (tag._params().get("url") or tag.gesture_id.name)
            tag.choice_ids.unlink()
            tag.write({
                "gesture_id": vierge.id, "res_model": False, "res_id": False,
                "params": False, "place": False, "qr_public": False,
                "name": tag.qr_reference or tag.name,
            })
            tag.message_post(
                body=Markup("<p>%s</p>") % escape(_(
                    "Étiquette réinitialisée par %(qui)s. Elle menait à : %(avant)s.",
                    qui=self.env.user.name, avant=avant)),
                message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    # ------------------------------------------------------------------
    # Porte publique
    # ------------------------------------------------------------------
    def _qr_adresse_publique(self):
        """L'adresse où envoyer un visiteur SANS compte, ou None.

        🔴 Seulement une étiquette de lot marquée « s'ouvre sans compte », et
        seulement pour un geste qui n'écrit rien. Une fiche ne s'ouvre en public
        que si elle a elle-même une page publique ET qu'elle est publiée.
        """
        self.ensure_one()
        tag = self.sudo()
        if not (tag.qr_batch_id and tag.qr_public) or tag.gesture_id.writes:
            return None
        if tag._refus_eventuel("session"):
            return None
        if tag.gesture_kind == "url":
            adresse = tag._params().get("url") or ""
            if urlparse(adresse).scheme.lower() in ("https", "http") and urlparse(adresse).netloc:
                return adresse
            return None
        if tag.gesture_kind == "open" and tag.res_model and tag.res_id \
                and tag.res_model in self.env:
            fiche = self.env[tag.res_model].sudo().browse(tag.res_id).exists()
            if not fiche:
                return None
            if tag.res_model == "bf.linkpage":
                return fiche.public_url if fiche.state == "published" and fiche.public_url else None
            if "website_url" in fiche._fields and "is_published" in fiche._fields \
                    and fiche.is_published and fiche.website_url:
                return "%s%s" % (fiche.get_base_url().rstrip("/"), fiche.website_url)
        return None

    # ------------------------------------------------------------------
    # Impression
    # ------------------------------------------------------------------
    def action_imprimer_qr(self):
        if not self:
            raise UserError(_("Aucune étiquette à imprimer."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Imprimer des étiquettes QR"),
            "res_model": "bf.qr.imprimer",
            "view_mode": "form",
            "target": "new",
            "context": {"default_tag_ids": [(6, 0, self.ids)]},
        }
