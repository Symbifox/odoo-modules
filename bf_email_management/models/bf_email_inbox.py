"""Surface RPC de la boîte de réception OWL (action cliente ``bf_email_inbox``).

Le navigateur IMAP (``imap_browser_*``) parle au serveur de courriel ; ces
méthodes-ci parlent à ``bf.email``. Même contrat de sortie — des dictionnaires
JSON simples — pour que les deux actions clientes partagent la même mise en
page, les mêmes préférences et les mêmes raccourcis clavier.

Rien ici n'est en ``sudo`` : la portée est toujours l'usager courant, et les
règles d'enregistrement restent l'autorité.
"""

import email.utils
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Nombre de lignes maximum qu'une page peut demander. Le composant OWL propose
# 50/100/200/500 dans ses préférences ; on borne quand même côté serveur.
MAX_PAGE = 500


class BfEmail(models.Model):
    _inherit = "bf.email"

    # ------------------------------------------------------------------
    # Dossiers virtuels
    # ------------------------------------------------------------------
    @api.model
    def _inbox_folder_defs(self):
        """Définition des « dossiers » de gauche, à plat, parents d'abord.

        Le vocabulaire suit délibérément ``_mobile_filter_sql`` : téléphone,
        liste Odoo et cette action doivent s'entendre sur ce que veut dire
        « boîte de réception », sinon les trois comptent trois choses.
        """
        now = fields.Datetime.now()
        inbox_domain = [
            ("is_handled", "=", False),
            "|", ("imap_in_inbox", "=", True),
            ("source", "in", ("chatter", "gateway")),
        ]
        defs = [
            {
                "key": "inbox", "label": _("Boîte de réception"),
                "icon": "fa-inbox", "parent": False,
                "domain": inbox_domain,
                "unread_domain": inbox_domain + [("status", "=", "new")],
            },
            {
                "key": "unread", "label": _("Non lus"),
                "icon": "fa-envelope", "parent": False,
                "domain": [("status", "=", "new"), ("is_handled", "=", False)],
            },
            {
                "key": "to_reply", "label": _("À répondre"),
                "icon": "fa-reply", "parent": False,
                "domain": [
                    ("direction", "=", "in"),
                    ("is_handled", "=", False),
                    ("status", "in", ("new", "read")),
                ],
            },
            {
                "key": "unrouted", "label": _("Sans dossier"),
                "icon": "fa-unlink", "parent": False,
                "domain": [
                    ("source", "=", "imap"),
                    ("res_model", "=", False),
                    ("is_handled", "=", False),
                ],
            },
            {
                "key": "snoozed", "label": _("Reportés"),
                "icon": "fa-moon-o", "parent": False,
                "domain": [
                    ("is_handled", "=", True),
                    ("snoozed_until", "!=", False),
                    ("snoozed_until", ">", now),
                ],
            },
            {
                # Un « non lu » sortant n'existe pas : c'est nous qui l'avons
                # écrit. Pas de pastille sur ce dossier.
                "key": "sent", "label": _("Envoyés"),
                "icon": "fa-paper-plane-o", "parent": False,
                "domain": [("direction", "=", "out")], "unread": False,
            },
            {
                "key": "handled", "label": _("Traités"),
                "icon": "fa-check", "parent": False,
                "domain": [
                    ("is_handled", "=", True),
                    "|", ("snoozed_until", "=", False),
                    ("snoozed_until", "<=", now),
                ], "unread": False,
            },
            {
                "key": "categories", "label": _("Par catégorie"),
                "icon": "fa-tags", "parent": False, "domain": None,
            },
        ]
        # Les catégories portent TOUT le courrier, traité compris. Les borner
        # au non-traité paraissait logique — une catégorie sert à trier ce qui
        # reste à faire — mais sur une boîte tenue à l'Inbox Zero elles sont
        # alors vides en permanence, donc inutiles. Ce qu'on veut ici est un axe
        # de navigation dans l'archive : « tous mes courriels fournisseurs ».
        # Le compteur de non-lus reste, lui, ce qui mérite attention.
        # fields_get plutôt que _fields[...].selection : c'est la voie qui rend
        # les libellés dans la langue de l'usager.
        selection = self.fields_get(["category"])["category"]["selection"]
        for value, label in selection:
            defs.append({
                "key": f"category:{value}",
                "label": label,
                "icon": "fa-tag",
                "parent": "categories",
                "domain": [("category", "=", value)],
            })
        # Sans elle, une bonne part du courrier n'apparaît sous aucune
        # catégorie et le groupe ne totalise pas la boîte : l'auto-classement
        # ne s'est jamais appliqué aux lignes les plus anciennes.
        defs.append({
            "key": "category:none",
            "label": _("Sans catégorie"),
            "icon": "fa-tag",
            "parent": "categories",
            "domain": [("category", "in", (False, ""))],
        })
        defs.append({
            "key": "all", "label": _("Tous les courriels"),
            "icon": "fa-archive", "parent": False, "domain": [],
            "unread": False,
        })
        return defs

    @api.model
    def _inbox_folder_domain(self, folder):
        """Domaine complet (portée usager incluse) pour une clé de dossier."""
        for d in self._inbox_folder_defs():
            if d["key"] == folder:
                if d["domain"] is None:
                    # Un parent d'arborescence n'est pas sélectionnable.
                    raise UserError(
                        _("« %s » regroupe des dossiers, il ne se lit pas "
                          "directement.", d["label"])
                    )
                return [("user_id", "=", self.env.uid)] + d["domain"]
        raise UserError(_("Dossier inconnu : %s", folder))

    @api.model
    def inbox_get_folders(self):
        """Arborescence de gauche avec ses compteurs.

        ``count`` = total du dossier, ``unread_count`` = ce qui mérite le gras.
        Un parent (``domain`` nul) additionne ses enfants.
        """
        base = [("user_id", "=", self.env.uid)]
        defs = self._inbox_folder_defs()
        out = []
        for d in defs:
            if d["domain"] is None:
                out.append({
                    "key": d["key"], "label": d["label"], "icon": d["icon"],
                    "parent": d["parent"], "selectable": False,
                    "count": 0, "unread_count": 0,
                })
                continue
            count = self.search_count(base + d["domain"])
            if d.get("unread") is False:
                unread = 0
            else:
                unread_domain = d.get("unread_domain")
                if unread_domain is None:
                    unread_domain = d["domain"] + [("status", "=", "new")]
                unread = self.search_count(base + unread_domain)
            out.append({
                "key": d["key"], "label": d["label"], "icon": d["icon"],
                "parent": d["parent"], "selectable": True,
                "count": count, "unread_count": unread,
            })
        by_key = {f["key"]: f for f in out}
        for f in out:
            parent = by_key.get(f["parent"]) if f["parent"] else None
            if parent and not parent["selectable"]:
                parent["count"] += f["count"]
                parent["unread_count"] += f["unread_count"]
        return out

    # ------------------------------------------------------------------
    # Liste des messages
    # ------------------------------------------------------------------
    @staticmethod
    def _inbox_display_name(raw):
        """Nom affichable tiré d'un en-tête d'adresse, adresse en repli."""
        if not raw:
            return ""
        name, addr = email.utils.parseaddr(raw)
        return (name or addr or raw)[:255]

    def _inbox_row(self, with_thread=False):
        """Une ligne de la liste, telle que le composant OWL l'attend.

        ``thread_count`` est un calcul NON stocké qui fait un ``search_count``
        par enregistrement : le joindre à une page de cent lignes coûterait
        cent COUNT. Seul l'aperçu, qui porte sur une ligne, le demande.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        if self.direction == "out":
            correspondent = self._inbox_display_name(self.email_to)
        else:
            correspondent = (
                self.partner_id.display_name
                or self._inbox_display_name(self.email_from)
            )
        row = {
            "id": self.id,
            "date": self.date and fields.Datetime.to_string(self.date) or False,
            "subject": self.subject or "",
            "preview": self.body_preview or "",
            "from": self.email_from or "",
            "to": self.email_to or "",
            "correspondent": correspondent,
            "direction": self.direction or "",
            "source": self.source or "",
            "category": self.category or "",
            "status": self.status or "",
            "is_handled": self.is_handled,
            "seen": self.status != "new",
            "is_replied": self.status == "replied",
            "is_snoozed": bool(
                self.snoozed_until and self.snoozed_until > now
            ),
            "has_attachments": self.has_attachments,
            "attachment_count": self.attachment_count,
            "res_model": self.res_model or False,
            "res_id": self.res_id or False,
            "record_name": self.record_name or "",
            "is_to_me": self.is_to_me,
            "is_question": self.is_question,
        }
        if with_thread:
            row["thread_count"] = self.thread_count
        return row

    @api.model
    def inbox_get_messages(self, folder="inbox", offset=0, limit=100,
                           search=None):
        """Une page de la liste, du plus récent au plus ancien.

        La recherche est côté serveur (et non un filtre de la page chargée
        comme dans le navigateur IMAP) : ici la source est une table indexée,
        donc autant chercher dans tout le dossier plutôt que dans les cent
        premières lignes.
        """
        domain = self._inbox_folder_domain(folder)
        term = (search or "").strip()
        if term:
            domain += [
                "|", "|", "|",
                ("subject", "ilike", term),
                ("email_from", "ilike", term),
                ("email_to", "ilike", term),
                ("body_preview", "ilike", term),
            ]
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 100), MAX_PAGE))
        total = self.search_count(domain)
        records = self.search(domain, offset=offset, limit=limit,
                              order="date desc, id desc")
        return {
            "folder": folder,
            "messages": [r._inbox_row() for r in records],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    # ------------------------------------------------------------------
    # Aperçu
    # ------------------------------------------------------------------
    @api.model
    def inbox_get_body(self, email_id):
        """Corps assaini + pièces jointes, et bascule « lu » au passage."""
        rec = self.browse(int(email_id)).exists()
        if not rec:
            raise UserError(_("Courriel introuvable (#%s).", email_id))
        rec.check_access("read")
        data = rec._inbox_row(with_thread=True)
        data.update({
            "body_html": rec.body_html_display or "",
            "cc": rec.email_cc or "",
            "message_id_header": rec.message_id_header or "",
            "imap_folder": rec.imap_folder or "",
            "snoozed_until": (
                rec.snoozed_until
                and fields.Datetime.to_string(rec.snoozed_until) or False
            ),
            "attachments": rec.get_preview_attachments(),
        })
        # Même règle que ``web_read`` : on ne marque lu que ce que l'usager
        # peut écrire, sinon ouvrir la boîte d'un collègue la marquerait lue
        # à sa place.
        if rec.status == "new":
            writable = rec._filtered_access("write")
            if writable:
                writable.write({"status": "read"})
                data["status"] = "read"
                data["seen"] = True
        return data

    # ------------------------------------------------------------------
    # Composer un courriel neuf
    # ------------------------------------------------------------------
    @api.model
    def inbox_compose(self):
        """Ouvrir le composeur sur un courriel neuf, rattaché à rien.

        Un courriel doit bien partir de quelque part : Odoo poste toujours sur
        une fiche. Plutôt que d'imposer un dossier au départ — c'est justement
        ce qu'on ne sait pas encore — on crée une ligne ``bf.email`` vide qui
        sert de fil à elle-même, exactement comme ``_composer_target`` le fait
        déjà pour un orphelin IMAP. La ligne apparaît ensuite dans « Sans
        dossier », d'où « Router… » l'attache à la fiche voulue, et les
        réponses du correspondant reviennent dessus par les en-têtes.

        Le brouillon naît ``is_handled=True`` : abandonner le composeur ne doit
        pas laisser une coquille vide en haut de la boîte de réception. Il
        rejoint la boîte au moment de l'envoi, quand il y a quelque chose à
        suivre (voir ``inbox_close_compose``).
        """
        shell = self.create({
            "subject": "",
            "direction": "out",
            "source": "chatter",
            "status": "read",
            "is_handled": True,
            "handled_at": fields.Datetime.now(),
            "user_id": self.env.uid,
            "date": fields.Datetime.now(),
            "email_from": self.env.user.email or "",
        })
        action = self.env["ir.actions.actions"]._for_xml_id(
            "mail.action_email_compose_message_wizard"
        )
        action["context"] = {
            "default_model": self._name,
            "default_res_ids": [shell.id],
            "default_composition_mode": "comment",
            "default_partner_ids": [(6, 0, [])],
            "default_partner_cc_ids": [(6, 0, [])],
            "default_partner_bcc_ids": [(6, 0, [])],
            "default_subject": "",
            "default_body": shell._compose_signature_block(),
            "default_notify": True,
            "force_email": True,
            "mail_create_nosubscribe": True,
            # Repéré par inbox_close_compose pour savoir quelle coquille
            # ramener dans la boîte une fois le composeur refermé.
            "bf_email_compose_shell_id": shell.id,
        }
        action["name"] = _("Nouveau courriel")
        return action

    @api.model
    def inbox_close_compose(self, shell_id):
        """Après fermeture du composeur : garder la ligne, ou l'effacer.

        Appelée par le composant OWL sur ``onClose``, donc **par RPC** : le nom
        ne peut pas commencer par un souligné, Odoo refuse d'exposer les
        méthodes privées (« Private methods cannot be called remotely »). Les
        tests unitaires ne voient pas ce mur — ils appellent le modèle en
        direct — seule une sonde HTTP le révèle. Une coquille sur laquelle
        rien n'a été posté n'a aucune raison de survivre — c'est un composeur
        annulé. Une coquille qui porte un message devient un courriel sortant
        ordinaire, remis en boîte pour qu'on puisse le router.

        Retourne ``True`` quand la ligne a été conservée.
        """
        shell = self.browse(int(shell_id)).exists()
        if not shell or shell.user_id.id != self.env.uid:
            return False
        posted = self.env["mail.message"].search([
            ("model", "=", self._name),
            ("res_id", "=", shell.id),
            ("message_type", "in", ("email", "comment")),
        ], order="date desc, id desc", limit=1)
        if not posted:
            shell.unlink()
            return False
        msg = posted.sudo()
        recipients = ", ".join(p.email for p in msg.partner_ids if p.email)
        vals = {
            "is_handled": False,
            "handled_at": False,
            "date": msg.date or fields.Datetime.now(),
            "mail_message_id": msg.id,
            "subject": msg.subject or shell.subject or "",
            "email_to": getattr(msg, "email_to", "") or recipients,
            "email_cc": getattr(msg, "email_cc", "") or "",
            "partner_id": msg.partner_ids[:1].id or False,
        }
        # Le Message-ID n'est repris que s'il est encore libre pour cet usager :
        # si le cron de projection est passé entre l'envoi et ici, il a déjà
        # créé sa propre ligne, et la contrainte d'unicité
        # (message_id_header, company_id, user_id) rejetterait l'écriture.
        if msg.message_id:
            taken = self.with_context(active_test=False).sudo().search_count([
                ("message_id_header", "=", msg.message_id),
                ("user_id", "=", shell.user_id.id),
                ("id", "!=", shell.id),
            ])
            if not taken:
                vals["message_id_header"] = msg.message_id
        shell.write(vals)
        return True

    # ------------------------------------------------------------------
    # Synchronisation depuis la boîte OWL
    # ------------------------------------------------------------------
    @api.model
    def inbox_sync_now(self):
        """« Synchroniser maintenant », en version utilisable par l'action cliente.

        ``action_sync_now`` rend une notification assortie d'un
        ``next: {tag: reload}`` qui recharge le client web au complet : dans une
        vue liste ça passe, ici ça jetterait l'aperçu ouvert et la sélection en
        cours. On ne garde donc que le texte, et le composant rafraîchit ses
        listes lui-même.
        """
        result = self.action_sync_now() or {}
        params = result.get("params") or {}
        return {
            "title": params.get("title") or _("Synchronisation terminée"),
            "message": params.get("message") or "",
            "type": params.get("type") or "info",
        }

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    # Liste blanche : le composant OWL nomme la méthode à exécuter, le
    # serveur décide de ce qui est nommable. Sans elle, un `orm.call` arbitraire
    # depuis la console du navigateur passerait par la même porte.
    _INBOX_ACTIONS = {
        "reply": "action_reply",
        "reply_all": "action_reply_all",
        "forward": "action_forward",
        "handle": "action_archive",
        "unhandle": "action_unhandle",
        "snooze": "action_snooze",
        "reroute": "action_reroute",
        "activity": "action_create_reminder",
        "open_record": "action_open_source_record",
        "open_form": None,
        "conversation": "action_open_conversation",
        "download_eml": "action_download_eml",
        "mark_read": "action_mark_read",
        "mark_replied": "action_mark_replied",
    }

    @api.model
    def inbox_run_action(self, action, email_ids):
        """Exécute une action nommée sur une ou plusieurs lignes.

        Retourne l'``ir.actions.*`` produit par la méthode sous-jacente, ou
        ``False`` quand elle n'ouvre rien (Traité, Remettre en boîte…).
        """
        if action not in self._INBOX_ACTIONS:
            raise UserError(_("Action inconnue : %s", action))
        if isinstance(email_ids, int):
            email_ids = [email_ids]
        records = self.browse([int(i) for i in email_ids or []]).exists()
        if not records:
            raise UserError(_("Aucun courriel sélectionné."))

        if action == "open_form":
            records.ensure_one()
            return {
                "type": "ir.actions.act_window",
                "res_model": self._name,
                "res_id": records.id,
                "view_mode": "form",
                "views": [[False, "form"]],
                "target": "current",
            }

        method = getattr(records, self._INBOX_ACTIONS[action])
        # Les actions à cible unique refusent un lot : on garde le message
        # d'erreur d'Odoo plutôt que de deviner laquelle appliquer.
        result = method()
        if not isinstance(result, dict):
            return False
        # Ces actions transitent par ``call_kw`` (orm.call), qui — au contraire
        # de ``call_button`` — ne passe jamais par clean_action() : sans clé
        # ``views`` explicite, le client web fait un .map() sur undefined et la
        # fenêtre ne s'ouvre pas. Même correctif que imap_browser_quick_reroute.
        if result.get("type") == "ir.actions.act_window" and not result.get("views"):
            modes = (result.get("view_mode") or "form").split(",")
            result["views"] = [[False, m.strip()] for m in modes if m.strip()]
        return result
