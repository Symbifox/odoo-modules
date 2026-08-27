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

# Clé du dossier « Brouillons ». C'est le seul dossier de la colonne de gauche
# dont la source n'est pas ``bf.email`` mais ``mail.scheduled.message`` : un
# envoi programmé n'est pas encore un courriel, il n'a ni Message-ID ni
# contrepartie IMAP, et lui fabriquer une ligne bf.email juste pour qu'il
# s'affiche ici en ferait un faux courriel dans tous les comptages. La liste
# bascule donc de source selon le dossier ouvert.
DRAFTS_FOLDER = "drafts"

# Dossiers IMAP réels dans l'arbre de gauche (). L'arborescence
# vient du serveur (cache `bf.email.account.folder_cache`) mais le CONTENU
# reste des lignes `bf.email` : ouvrir « Archives/2026 » montre les lignes
# Odoo classées là, avec Traité / Router / Ajouter et le lien vers le
# chatter. Lire le dossier en direct sur IMAP donnerait des messages sans
# fiche, donc sans association possible — c'est précisément ce que le
# réglage d'arrêt ci-dessous permet à une organisation de refuser.
IMAP_GROUP = "imapfolders"
IMAP_ACCOUNT_PREFIX = "imapacct:"
IMAP_FOLDER_PREFIX = "imapf:"

# Réglage administrateur : `bf_email.show_imap_folders` à « 0 » retire le
# groupe pour tout le monde.
IMAP_FOLDERS_PARAM = "bf_email.show_imap_folders"

_IMAP_FOLDER_ICONS = (
    ("INBOX", "fa-inbox"),
    ("SENT", "fa-paper-plane-o"),
    ("DRAFT", "fa-pencil-square-o"),
    ("TRASH", "fa-trash-o"),
    ("DELETED", "fa-trash-o"),
    ("JUNK", "fa-ban"),
    ("SPAM", "fa-ban"),
    ("ARCHIVE", "fa-archive"),
)


def _imap_folder_icon(name):
    upper = (name or "").upper()
    for token, icon in _IMAP_FOLDER_ICONS:
        if upper == token or upper.startswith(token):
            return icon
    return "fa-folder-o"


def _imap_folder_sort_key(folder):
    """INBOX, puis Sent, puis alphabétique — l'ordre d'un client courriel."""
    name = folder.get("name") or ""
    upper = name.upper()
    if upper == "INBOX":
        return (0, "")
    if upper.startswith("SENT"):
        return (1, name)
    return (2, name)


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
        inbox_domain = self._inbox_domain()
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
        # Les vrais dossiers du serveur, juste sous la boîte de réception.
        # Repliés par défaut : la colonne de gauche ne change pour personne
        # tant que le groupe n'est pas ouvert.
        imap_defs = self._inbox_imap_folder_defs()
        if imap_defs:
            at = next(
                (i + 1 for i, d in enumerate(defs) if d["key"] == "inbox"),
                len(defs),
            )
            defs[at:at] = imap_defs

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
    def _inbox_domain(self):
        """Ce que « boîte de réception » veut dire, en un seul endroit.

        Cette définition existait en **six** exemplaires — l'arbre, le filtre
        mobile, le tableau de bord, le filtre de la vue liste, le domaine de
        l'action, et le badge du systray (en JavaScript) — avec un commentaire
        dans chacun demandant aux autres de rester d'accord. C'était une
        promesse, pas un mécanisme. Les quatre exemplaires Python en dérivent
        désormais ; les deux autres sont épinglés par un test.

        Trois façons d'être « dans la boîte » :

        1. le message est physiquement dans l'INBOX du serveur ;
        2. la ligne vient d'un chatter ou de la passerelle, elle n'a pas de
           contrepartie IMAP à consulter ;
        3. ⚠️ on ne sait plus **où** est la copie serveur (`imap_folder` vide).
           Ce troisième cas naît quand une remise en boîte échoue parce que le
           dossier a été renommé ou vidé au webmail. Sans lui, la ligne
           quittait « Traités » sans jamais réapparaître dans la boîte : elle
           tombait hors de toute liste de travail, en silence.
        """
        return [
            ("is_handled", "=", False),
            "|", "|", ("imap_in_inbox", "=", True),
            ("source", "in", ("chatter", "gateway")),
            ("imap_folder", "=", False),
        ]

    @api.model
    def _inbox_imap_folders_enabled(self):
        """Le groupe « Dossiers IMAP » est-il offert dans cette base ?

        Une organisation peut le refuser : naviguer par dossier de serveur
        invite à traiter le courriel comme un classeur personnel, alors que
        la valeur de cette boîte est de rattacher chaque message à une tâche,
        un ticket ou un contact. Le réglage vit dans les paramètres système,
        pas dans les préférences de l'usager, parce que c'est une décision
        d'organisation et non un goût d'affichage.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            IMAP_FOLDERS_PARAM, "1",
        )
        return str(raw).strip().lower() not in ("0", "false", "no", "")

    @api.model
    def _inbox_imap_accounts(self):
        """Mes comptes IMAP actifs, dans l'ordre d'affichage."""
        return self.env["bf.email.account"].search([
            ("user_id", "=", self.env.uid), ("active", "=", True),
        ], order="id")

    @api.model
    def _inbox_imap_folder_defs(self):
        """Sous-arbre des dossiers IMAP réels, ou ``[]`` s'il n'y a rien.

        Ne lève jamais et n'ouvre jamais de connexion de son propre chef :
        ``get_imap_folders`` sert son cache. Un serveur injoignable rend le
        dernier arbre connu ; à défaut, le groupe disparaît simplement.
        """
        if not self._inbox_imap_folders_enabled():
            return []
        accounts = self._inbox_imap_accounts()
        if not accounts:
            return []

        out = [{
            "key": IMAP_GROUP, "label": _("Dossiers IMAP"),
            "icon": "fa-server", "parent": False, "domain": None,
        }]
        multi = len(accounts) > 1
        for account in accounts:
            try:
                folders = account._get_imap_folders()
            except Exception:  # pragma: no cover - défensif
                _logger.debug(
                    "bf.email: dossiers IMAP illisibles pour le compte %s",
                    account.id, exc_info=True,
                )
                folders = []
            if not folders:
                continue
            account_key = "%s%s" % (IMAP_ACCOUNT_PREFIX, account.id)
            if multi:
                out.append({
                    "key": account_key,
                    "label": account.name or account.login,
                    "icon": "fa-at", "parent": IMAP_GROUP,
                    "domain": [("account_id", "=", account.id)],
                    "unread": False,
                })
            known = {f.get("name") for f in folders}
            # Les dossiers que les lignes citent encore et que le serveur ne
            # liste plus : renommés ou supprimés au webmail. Sans eux, les
            # lignes concernées n'ont plus aucun nœud dans l'arbre — elles ne
            # sont pas perdues (« Tous les courriels », catégories, recherche
            # les gardent) mais elles disparaissent de cet axe de navigation
            # sans un mot. Les montrer rend la dérive visible au lieu de la
            # cacher.
            orphans = [
                {"name": name, "delimiter": "", "has_children": False,
                 "noselect": False, "stale": True}
                for name in sorted(self._inbox_imap_orphan_folders(account))
                if name not in known
            ]
            for folder in sorted(folders, key=_imap_folder_sort_key) + orphans:
                name = folder.get("name")
                if not name:
                    continue
                delim = folder.get("delimiter") or ""
                parent = account_key if multi else IMAP_GROUP
                label = name
                if delim and delim in name:
                    head, _sep, tail = name.rpartition(delim)
                    label = tail or name
                    if head and head in known:
                        parent = "%s%s:%s" % (
                            IMAP_FOLDER_PREFIX, account.id, head,
                        )
                selectable = not folder.get("noselect")
                stale = folder.get("stale")
                out.append({
                    "key": "%s%s:%s" % (IMAP_FOLDER_PREFIX, account.id, name),
                    "label": label,
                    "title": _(
                        "%(nom)s — absent du serveur. Le dossier a sans doute "
                        "été renommé ou supprimé ; les courriels restent ici.",
                        nom=name,
                    ) if stale else name,
                    "icon": "fa-chain-broken" if stale else _imap_folder_icon(name),
                    "parent": parent,
                    "domain": [
                        ("account_id", "=", account.id),
                        ("imap_folder", "=", name),
                    ] if selectable else None,
                    "imap_counts": (account.id, name) if selectable else None,
                })
        # Un groupe sans un seul dossier ne mérite pas sa ligne.
        return out if len(out) > 1 else []

    @api.model
    def _inbox_imap_orphan_folders(self, account):
        """Dossiers cités par MES lignes de ce compte, quels qu'ils soient.

        L'appelant retire ceux que le serveur liste ; il reste les orphelins.
        Un regroupement, pas une boucle : le champ n'est pas indexé.
        """
        return {
            folder
            for _acc, folder, _count in self._read_group(
                [("user_id", "=", self.env.uid),
                 ("account_id", "=", account.id),
                 ("imap_folder", "!=", False)],
                ["account_id", "imap_folder"], ["__count"],
            )
            if folder
        }

    @api.model
    def _inbox_imap_key_domain(self, folder):
        """Domaine d'une clé ``imapacct:``/``imapf:`` sans relire le serveur.

        ``_inbox_folder_domain`` est appelé à chaque ouverture de dossier et
        à chaque page ; passer par ``_inbox_folder_defs`` y ferait porter le
        coût d'un rafraîchissement de cache — et donc, un jour sur deux, un
        aller-retour IMAP au moment du clic. Retourne ``None`` quand la clé
        n'est pas une clé IMAP.
        """
        if folder.startswith(IMAP_ACCOUNT_PREFIX):
            raw_id, name = folder[len(IMAP_ACCOUNT_PREFIX):], None
        elif folder.startswith(IMAP_FOLDER_PREFIX):
            raw_id, _sep, name = folder[len(IMAP_FOLDER_PREFIX):].partition(":")
        else:
            return None
        if not self._inbox_imap_folders_enabled():
            raise UserError(_(
                "Les dossiers IMAP sont désactivés dans cette base."
            ))
        try:
            account_id = int(raw_id)
        except (TypeError, ValueError):
            raise UserError(_("Dossier inconnu : %s", folder)) from None
        # La propriété du compte est vérifiée ici et non déduite de la clé :
        # celle-ci arrive du navigateur.
        account = self._inbox_imap_accounts().filtered(
            lambda a: a.id == account_id
        )
        if not account:
            raise UserError(_("Dossier inconnu : %s", folder))
        domain = [("user_id", "=", self.env.uid),
                  ("account_id", "=", account.id)]
        if name:
            domain.append(("imap_folder", "=", name))
        return domain

    @api.model
    def _inbox_drafts_domain(self):
        """Mes envois programmés — ceux dont je suis l'auteur.

        ``mail.scheduled.message._search`` borne déjà le résultat aux fiches
        sur lesquelles l'usager peut poster ; ce filtre-ci ajoute la seule
        chose que ça ne dit pas : que le brouillon est le mien. Sans lui, la
        colonne de gauche annoncerait dans MA boîte les brouillons d'un
        collègue sur une tâche que nous suivons tous les deux.
        """
        return [("author_id", "=", self.env.user.partner_id.id)]

    @api.model
    def _inbox_folder_domain(self, folder):
        """Domaine complet (portée usager incluse) pour une clé de dossier."""
        if folder == DRAFTS_FOLDER:
            # Une erreur explicite plutôt qu'un domaine bf.email vide, qui
            # rendrait toute la boîte en croyant rendre les brouillons.
            raise UserError(_(
                "« Brouillons » ne se lit pas comme un dossier de courriels : "
                "utiliser inbox_get_drafts."
            ))
        imap_domain = self._inbox_imap_key_domain(folder)
        if imap_domain is not None:
            return imap_domain
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
    def _inbox_imap_counts(self, defs):
        """``({(compte, dossier): total}, {…: non lus})`` pour les defs IMAP."""
        keys = [d["imap_counts"] for d in defs if d.get("imap_counts")]
        if not keys:
            return {}, {}
        account_ids = list({k[0] for k in keys})
        domain = [
            ("user_id", "=", self.env.uid),
            ("account_id", "in", account_ids),
            ("imap_folder", "!=", False),
        ]
        totals = {
            (account.id, folder): count
            for account, folder, count in self._read_group(
                domain, ["account_id", "imap_folder"], ["__count"],
            )
        }
        unread = {
            (account.id, folder): count
            for account, folder, count in self._read_group(
                domain + [("status", "=", "new")],
                ["account_id", "imap_folder"], ["__count"],
            )
        }
        return totals, unread

    @api.model
    def inbox_get_folders(self):
        """Arborescence de gauche avec ses compteurs.

        ``count`` = total du dossier, ``unread_count`` = ce qui mérite le gras.
        Un parent (``domain`` nul) additionne ses enfants.
        """
        base = [("user_id", "=", self.env.uid)]
        defs = self._inbox_folder_defs()
        # Un `search_count` par dossier IMAP ferait grimper la colonne de
        # gauche d'une douzaine de requêtes à chaque ouverture, sur un champ
        # qui n'est pas indexé. Deux regroupements suffisent.
        imap_total, imap_unread = self._inbox_imap_counts(defs)
        out = []
        for d in defs:
            if d["domain"] is None:
                out.append({
                    "key": d["key"], "label": d["label"], "icon": d["icon"],
                    "title": d.get("title") or d["label"],
                    "parent": d["parent"], "selectable": False,
                    "count": 0, "unread_count": 0,
                })
                continue
            imap_key = d.get("imap_counts")
            if imap_key:
                count = imap_total.get(imap_key, 0)
                out.append({
                    "key": d["key"], "label": d["label"], "icon": d["icon"],
                    "title": d.get("title") or d["label"],
                    "parent": d["parent"], "selectable": True,
                    "count": count,
                    "unread_count": imap_unread.get(imap_key, 0),
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
                "title": d.get("title") or d["label"],
                "parent": d["parent"], "selectable": True,
                "count": count, "unread_count": unread,
            })
        by_key = {f["key"]: f for f in out}
        for f in out:
            parent = by_key.get(f["parent"]) if f["parent"] else None
            if parent and not parent["selectable"]:
                parent["count"] += f["count"]
                parent["unread_count"] += f["unread_count"]
        # « Brouillons » se glisse juste après « Envoyés » : c'est du courrier
        # qui n'est pas encore parti, sa place est du côté sortant et non à la
        # fin, après les catégories.
        drafts = {
            "key": DRAFTS_FOLDER, "label": _("Brouillons"),
            "icon": "fa-pencil-square-o", "parent": False, "selectable": True,
            "count": self.env["mail.scheduled.message"].search_count(
                self._inbox_drafts_domain()
            ),
            "unread_count": 0,
        }
        insert_at = next(
            (i + 1 for i, f in enumerate(out) if f["key"] == "sent"), len(out)
        )
        out.insert(insert_at, drafts)
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
            # Alimente le menu « Ajouter » : une entrée « Ticket » sur une
            # base sans module d'assistance ouvrirait une fenêtre sur un
            # modèle inexistant.
            "has_helpdesk": rec.has_helpdesk,
            "has_expense": rec.has_expense,
            "has_crm": rec.has_crm,
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
    # Dossier « Brouillons » — les envois programmés
    # ------------------------------------------------------------------
    def _inbox_draft_row(self, draft):
        """Un envoi programmé, au format de ligne de la liste.

        Les clés reprennent celles d'un courriel là où elles ont un sens, pour
        que le gabarit n'ait pas deux vocabulaires à connaître : ``date`` porte
        la date d'envoi PRÉVUE — c'est elle qui compte pour un brouillon —
        et ``correspondent`` les destinataires.
        """
        recipients = [p.display_name for p in draft.partner_ids]
        return {
            "id": draft.id,
            "kind": "draft",
            "date": (
                draft.scheduled_date
                and fields.Datetime.to_string(draft.scheduled_date) or False
            ),
            "subject": draft.subject or "",
            "correspondent": ", ".join(recipients),
            "to": ", ".join(recipients),
            "recipient_count": len(recipients),
            "is_note": draft.is_note,
            "res_model": draft.model or False,
            "res_id": draft.res_id or False,
            "record_name": draft.record_name or "",
            "attachment_count": len(draft.attachment_ids),
            "has_attachments": bool(draft.attachment_ids),
            "is_late": bool(
                draft.scheduled_date
                and draft.scheduled_date < fields.Datetime.now()
            ),
            # La liste met en gras ce qui n'est pas vu ; un brouillon est
            # toujours de nous, rien n'y est « non lu ».
            "seen": True,
        }

    @api.model
    def inbox_get_drafts(self, offset=0, limit=100, search=None):
        """Une page du dossier « Brouillons », du plus proche au plus lointain.

        L'ordre est l'inverse de celui des courriels : sur du courrier reçu on
        veut le plus récent en haut, sur des envois programmés on veut le
        prochain à partir.
        """
        Scheduled = self.env["mail.scheduled.message"]
        domain = self._inbox_drafts_domain()
        term = (search or "").strip()
        if term:
            domain += ["|", ("subject", "ilike", term),
                       ("record_name", "ilike", term)]
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 100), MAX_PAGE))
        total = Scheduled.search_count(domain)
        drafts = Scheduled.search(
            domain, offset=offset, limit=limit,
            order="scheduled_date asc, id asc",
        )
        return {
            "folder": DRAFTS_FOLDER,
            "messages": [self._inbox_draft_row(d) for d in drafts],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    @api.model
    def inbox_get_draft_body(self, draft_id):
        """Corps et pièces jointes d'un envoi programmé."""
        draft = self.env["mail.scheduled.message"].browse(
            int(draft_id)
        ).exists()
        # ``_search`` de mail.scheduled.message porte le contrôle d'accès ;
        # un ``browse`` direct le contourne, d'où la relecture par search.
        if not draft or not self.env["mail.scheduled.message"].search_count(
            [("id", "=", draft.id)] + self._inbox_drafts_domain()
        ):
            raise UserError(_("Brouillon introuvable (#%s).", draft_id))
        data = self._inbox_draft_row(draft)
        data.update({
            "body_html": draft.body or "",
            "from": draft.author_id.display_name or "",
            "cc": "",
            "author_id": draft.author_id.id,
            "attachments": [
                {"id": a.id, "name": a.name or _("(sans nom)")}
                for a in draft.attachment_ids
            ],
        })
        return data

    # Même principe que ``_INBOX_ACTIONS`` : le composant nomme, le serveur
    # décide de ce qui est nommable.
    _INBOX_DRAFT_ACTIONS = ("send_now", "edit", "open_record", "cancel")

    @api.model
    def inbox_draft_action(self, action, draft_ids):
        """Envoyer, modifier, ouvrir la fiche liée ou annuler un brouillon.

        Retourne soit un ``ir.actions.*`` à exécuter, soit un dictionnaire de
        notification — jamais le ``next: {tag: reload}`` d'``action_send_now``,
        qui rechargerait le client web au complet et jetterait l'aperçu
        ouvert, comme pour ``inbox_sync_now``.
        """
        if action not in self._INBOX_DRAFT_ACTIONS:
            raise UserError(_("Action inconnue : %s", action))
        if isinstance(draft_ids, int):
            draft_ids = [draft_ids]
        ids = [int(i) for i in draft_ids or []]
        Scheduled = self.env["mail.scheduled.message"]
        # Relecture par ``search`` : c'est elle qui applique le contrôle
        # d'accès du modèle, et elle écarte au passage les identifiants d'un
        # brouillon qui n'est pas le mien.
        drafts = Scheduled.search([("id", "in", ids)] + self._inbox_drafts_domain())
        if not drafts:
            raise UserError(_("Aucun brouillon sélectionné."))

        if action == "edit":
            drafts.ensure_one()
            return drafts.open_edit_form()
        if action == "open_record":
            drafts.ensure_one()
            result = drafts.action_open_record()
            return result or False
        if action == "cancel":
            count = len(drafts)
            drafts.unlink()
            return {
                "notification": {
                    "title": _("Brouillon annulé"),
                    "message": _("%s envoi(s) programmé(s) supprimé(s).", count),
                    "type": "success",
                },
            }
        # send_now — ``post_message`` est unitaire et refuse ce qui n'a pas été
        # créé par l'usager ; on boucle et on laisse remonter le refus.
        count = 0
        for draft in drafts:
            draft.post_message()
            count += 1
        return {
            "notification": {
                "title": _("Brouillons envoyés"),
                "message": _("%s message(s) posté(s).", count),
                "type": "success",
            },
        }

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
        identity = self.env["bf.email.identity"]._default_for(self.env.user)
        shell = self.create({
            "subject": "",
            "direction": "out",
            "source": "chatter",
            "status": "read",
            "is_handled": True,
            "handled_at": fields.Datetime.now(),
            "user_id": self.env.uid,
            "date": fields.Datetime.now(),
            "email_from": identity.email_formatted if identity
                          else (self.env.user.email or ""),
        })
        signature = identity._signature_for()
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
            "default_body": shell._compose_signature_block(identity),
            "default_notify": True,
            "force_email": True,
            "mail_create_nosubscribe": True,
            # Repéré par inbox_close_compose pour savoir quelle coquille
            # ramener dans la boîte une fois le composeur refermé.
            "bf_email_compose_shell_id": shell.id,
        }
        if identity:
            action["context"]["default_bf_identity_id"] = identity.id
            action["context"]["default_bf_signature_snapshot"] = signature
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
        # « Ajouter » — créer une fiche À PARTIR du courriel, celui-ci étant
        # importé dans le chatter de la fiche neuve. Mêmes méthodes que le
        # menu « Nouveau ▾ » de la fiche complète du courriel : la boîte de
        # réception n'a pas sa propre notion de « créer une tâche », sinon les
        # deux dériveraient. Toutes unitaires (``ensure_one``).
        "create_task": "action_create_task",
        "create_lead": "action_create_crm_lead",
        "create_ticket": "action_create_helpdesk_ticket",
        "create_expense": "action_create_expense",
        "create_vendor_bill": "action_create_vendor_bill",
        "create_customer_invoice": "action_create_customer_invoice",
    }

    # Actions qui ne valent que sur une ligne. Le composant OWL le sait déjà et
    # n'en envoie qu'une, mais un appel direct passerait outre, et
    # ``action_create_task`` sur un lot lèverait une erreur d'``ensure_one``
    # illisible plutôt que de dire ce qui ne va pas.
    _INBOX_SINGLE_ACTIONS = (
        "reply", "reply_all", "forward", "activity", "open_record",
        "open_form", "conversation", "download_eml",
        "create_task", "create_lead", "create_ticket", "create_expense",
        "create_vendor_bill", "create_customer_invoice",
    )

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

        if action in self._INBOX_SINGLE_ACTIONS and len(records) > 1:
            raise UserError(_(
                "« %s » agit sur un seul courriel à la fois ; %s ont été "
                "envoyés.", action, len(records),
            ))
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
