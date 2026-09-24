"""Le contrat mobile du bloc-notes : ce que la page ``/notes`` et l'application
Symbifox Mobile lisent et écrivent.

Une seule couche pour les deux portes. La page (session Odoo) et l'application
(jeton d'appareil) appellent les mêmes méthodes, donc la même forme de note, les
mêmes refus et les mêmes phrases. Deux copies divergeraient au premier geste
ajouté.

Toutes les méthodes sont PRIVÉES (préfixe ``_``) : une méthode publique d'un
modèle est appelable par RPC, et rien ici n'a à l'être en dehors des deux
contrôleurs. Aucune n'élève de droit : elles tournent sous l'usager appelant,
et la règle d'enregistrement du module (auteur, ou note partagée en lecture)
s'applique telle quelle.

Deux choix qui décident de tout le reste :

* **Le téléphone écrit du texte, pas du HTML.** Une note née au téléphone est
  une suite de paragraphes ; elle fait l'aller-retour texte → HTML → texte sans
  rien perdre. Une note riche, née au bureau (image, liste, gras, lien), ne le
  ferait pas : la réécrire depuis un champ texte effacerait sa mise en forme en
  silence. Elle est donc rendue ``editable: false``, lue au téléphone et
  modifiée au bureau.
* **Une note se crée d'abord sur l'appareil.** Elle porte un ``client_uuid``
  tiré par l'appareil, et le serveur rend la même note à chaque renvoi : la file
  d'envoi hors ligne peut rejouer sans jamais dédoubler.
"""

import uuid
from datetime import datetime, timedelta

from lxml import html as lxml_html
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_date, html2plaintext

#: Gestes rapides offerts au téléphone et sur la carte kanban.
ACTIONS = ("archive", "unarchive", "pin", "unpin", "activity", "task", "reroute")

#: Délais de rappel offerts, en jours (aujourd'hui, demain, +2 j, +1 sem.).
ACTIVITY_DAYS = (0, 1, 2, 7)

#: Balises qu'un champ texte sait reproduire. Tout le reste rend la note riche.
PLAIN_TAGS = {"p", "div", "br", "span"}
BLOCK_TAGS = {"p", "div"}

#: Attributs qui ne portent aucune mise en forme. 🔴 `data-oe-version` est posé
#: par l'éditeur d'Odoo sur l'enveloppe de TOUTE note écrite au bureau : sans
#: lui dans cette liste, une grande partie des notes d'une base réelle étaient
#: rendues « riches », donc en lecture seule au téléphone, sans aucune raison.
HARMLESS_ATTRS = {"class", "data-oe-version"}

#: Plafonds côté serveur : une page ou une app raisonnable n'en approche jamais,
#: une charge hors sujet est refusée avant d'être écrite.
MAX_TEXT = 100_000
MAX_TITLE = 200
MAX_LIMIT = 200

#: Palette kanban d'Odoo : 0 (aucune) à 11.
MAX_COLOR = 11


class BfNote(models.Model):
    _inherit = "bf.note"

    client_uuid = fields.Char(
        string="Device identifier",
        copy=False,
        index=True,
        readonly=True,
        help="Identifier drawn by the phone when the note was written offline. "
             "The server returns the same note each time it is sent again.",
    )

    _sql_constraints = [
        # NULL n'entre pas en collision avec NULL : les notes nées au bureau,
        # sans identifiant, ne sont pas touchées par la contrainte.
        ("client_uuid_user_unique", "unique(user_id, client_uuid)",
         "This device identifier is already used by another note."),
    ]

    # ------------------------------------------------------------------
    # Texte ↔ HTML
    # ------------------------------------------------------------------
    @api.model
    def _mobile_text_to_html(self, text):
        """Une ligne = un paragraphe ; une ligne vide garde sa place."""
        lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        # Retirer les lignes vides de la FIN seulement : celles du milieu sont
        # l'espacement voulu par la personne.
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return ""
        return Markup("").join(
            Markup("<p>%s</p>") % line if line.strip() else Markup("<p><br></p>")
            for line in lines
        )

    @api.model
    def _mobile_html_is_plain(self, html):
        """Vrai si le corps ne porte que des paragraphes de texte.

        Le test est structurel (balises et attributs), pas un aller-retour de
        chaînes : Odoo réécrit le HTML à l'assainissement (espaces, ``<br>``
        auto-fermant), et une comparaison textuelle rendrait « riche » une note
        qui ne l'est pas.
        """
        if not html or not str(html).strip():
            return True
        try:
            fragments = lxml_html.fragments_fromstring(str(html))
        except Exception:  # noqa: BLE001 — illisible = on ne le réécrit pas
            return False
        for fragment in fragments:
            if isinstance(fragment, str):
                continue
            for element in fragment.iter():
                if not isinstance(element.tag, str):
                    # Commentaire HTML : la réécriture le perdrait.
                    return False
                if element.tag not in PLAIN_TAGS:
                    return False
                if element.tag == "span" and element.attrib:
                    return False
                if element.tag != "span" and set(element.attrib) - HARMLESS_ATTRS:
                    return False
        return True

    @api.model
    def _mobile_html_to_text(self, html):
        """Le texte d'une note : ligne par ligne pour une note simple, lecture
        seule approximative (``html2plaintext``) pour une note riche."""
        if not html or not str(html).strip():
            return ""
        if not self._mobile_html_is_plain(html):
            return html2plaintext(str(html)).strip()
        lines = []

        def en_ligne(element):
            # `<br>` à l'intérieur d'un paragraphe = saut de ligne voulu.
            parts = [element.text or ""]
            for child in element:
                parts.append("\n" if child.tag == "br" else child.text_content())
                parts.append(child.tail or "")
            texte = "".join(parts)
            # Un paragraphe qui ne contient qu'un `<br>` est une ligne vide.
            return "" if not texte.strip() else texte.rstrip("\n")

        def parcourir(noeuds, texte_de_tete=None):
            # 🔴 Un bloc peut en contenir d'autres : l'éditeur enveloppe la note
            # dans un `<div data-oe-version>` qui porte les `<p>`. Aplatir
            # l'enveloppe d'un coup collerait tous les paragraphes en une ligne.
            if texte_de_tete and texte_de_tete.strip():
                lines.append(texte_de_tete.strip())
            for noeud in noeuds:
                if isinstance(noeud, str):
                    if noeud.strip():
                        lines.append(noeud.strip())
                    continue
                if noeud.tag in BLOCK_TAGS and any(
                        isinstance(enfant.tag, str) and enfant.tag in BLOCK_TAGS
                        for enfant in noeud):
                    parcourir(list(noeud), noeud.text)
                elif noeud.tag in BLOCK_TAGS:
                    lines.append(en_ligne(noeud))
                elif noeud.tag == "br":
                    lines.append("")
                else:
                    lines.append(noeud.text_content())
                if noeud.getparent() is not None and noeud.tail and noeud.tail.strip():
                    lines.append(noeud.tail.strip())

        parcourir(lxml_html.fragments_fromstring(str(html)))
        # Les lignes vides de FIN ne sont pas gardées à l'écriture : les garder
        # à la lecture rendrait un aller-retour inexact.
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # La forme d'une note sur le fil
    # ------------------------------------------------------------------
    @api.model
    def _mobile_datetime(self, value):
        """Horodatage à la microseconde : deux modifications dans la même
        seconde doivent rester distinctes pour la détection de conflit."""
        return value.strftime("%Y-%m-%d %H:%M:%S.%f") if value else None

    @api.model
    def _mobile_parse_datetime(self, value):
        if not value:
            return None
        value = str(value).strip().replace("T", " ").rstrip("Z")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        raise UserError(_("Unreadable date: %s", value))

    def _mobile_link_name(self, link):
        """Le nom de la fiche liée, SOUS LES DROITS DE L'APPELANT ; "" sinon.

        Jamais `link.res_name`, qui est stocké et donc calculé pour
        l'auteur de la note, pas pour qui la lit. Une fiche absente ou interdite
        rendent la même chose.
        """
        if not (link.res_model and link.res_id and link.res_model in self.env):
            return ""
        try:
            rec = self.env[link.res_model].sudo(False).browse(link.res_id).exists()
            if not rec:
                return ""
            rec.check_access("read")
            return rec.display_name or ""
        except AccessError:
            return ""

    def _mobile_payload(self):
        self.ensure_one()
        links = []
        for link in self.link_ids:
            if not (link.res_model and link.res_id):
                continue
            links.append({
                "model": link.res_model,
                "id": link.res_id,
                # 🔴 le nom se résout ICI, sous les droits de
                # l'appelant. `res_name` est stocké (calculé sous les droits de
                # l'auteur de la note) : sur une note PARTAGÉE, il rendait à un
                # collègue le nom d'une fiche que lui ne peut pas ouvrir. Une
                # fiche illisible arrive sans nom plutôt que révélée.
                "name": self._mobile_link_name(link),
                "url": "/odoo/%s/%s" % (link.res_model, link.res_id),
            })
        attachments = self.env["ir.attachment"].search_count([
            ("res_model", "=", self._name), ("res_id", "=", self.id),
        ])
        return {
            "id": self.id,
            "client_uuid": self.client_uuid or None,
            "title": self.name or "",
            "text": self._mobile_html_to_text(self.body),
            "editable": self._mobile_html_is_plain(self.body),
            "pinned": bool(self.pinned),
            "color": self.color or 0,
            "deadline": fields.Date.to_string(self.deadline_date) if self.deadline_date else None,
            "active": bool(self.active),
            "shared": bool(self.is_shared),
            "mine": self.user_id == self.env.user,
            "links": links,
            "attachments": attachments,
            "url": "/odoo/m-bf.note/%s" % self.id,
            "write_date": self._mobile_datetime(self.write_date),
        }

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------
    @api.model
    def _mobile_list(self, limit=50, offset=0, archived=False, query=None, since=None):
        """Les notes de l'usager, épinglées d'abord puis les plus récentes.

        Avec ``since``, rend tout ce qui a changé depuis, ARCHIVÉES COMPRISES :
        c'est ainsi qu'une note archivée ailleurs quitte la liste de l'appareil.
        """
        try:
            limit = max(1, min(int(limit or 50), MAX_LIMIT))
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise UserError(_("Invalid limit or offset."))
        domain = [("user_id", "=", self.env.uid)]
        Note = self
        if since:
            domain.append(("write_date", ">", self._mobile_parse_datetime(since)))
            Note = self.with_context(active_test=False)
        elif archived:
            domain.append(("active", "=", False))
            Note = self.with_context(active_test=False)
        query = (query or "").strip()
        if query:
            domain += ["|", ("name", "ilike", query), ("body", "ilike", query)]
        notes = Note.search(domain, limit=limit, offset=offset,
                            order="pinned desc, write_date desc, id desc")
        return {
            "notes": [note._mobile_payload() for note in notes],
            "server_time": self._mobile_datetime(fields.Datetime.now()),
        }

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------
    @api.model
    def _mobile_clean_text(self, text):
        text = text if isinstance(text, str) else ("" if text is None else str(text))
        if len(text) > MAX_TEXT:
            raise UserError(_("This note is too long for the phone (%s characters at most).", MAX_TEXT))
        return text

    @api.model
    def _mobile_clean_title(self, title):
        title = (title if isinstance(title, str) else "").strip()
        return title[:MAX_TITLE]

    @api.model
    def _mobile_clean_uuid(self, value):
        """Un UUID bien formé, ou un refus. L'identifiant sert de clé
        d'idempotence : une valeur libre ouvrirait la porte aux collisions."""
        try:
            return str(uuid.UUID(str(value or "").strip()))
        except (TypeError, ValueError):
            raise UserError(_("The device identifier is not a valid UUID."))

    @api.model
    def _mobile_create(self, vals):
        """Crée la note, ou rend celle que ce ``client_uuid`` a déjà créée."""
        vals = vals or {}
        client_uuid = self._mobile_clean_uuid(vals.get("client_uuid"))
        existing = self.with_context(active_test=False).search([
            ("user_id", "=", self.env.uid), ("client_uuid", "=", client_uuid),
        ], limit=1)
        if existing:
            return {"note": existing._mobile_payload(), "created": False}

        text = self._mobile_clean_text(vals.get("text"))
        title = self._mobile_clean_title(vals.get("title"))
        if not text.strip() and not title:
            raise UserError(_("An empty note is not saved."))
        values = {
            "client_uuid": client_uuid,
            "user_id": self.env.uid,
            "body": self._mobile_text_to_html(text),
            "pinned": bool(vals.get("pinned")),
            "color": self._mobile_clean_color(vals.get("color")),
        }
        # Sans titre, la PREMIÈRE LIGNE fait le titre, comme dans Keep. Laisser
        # `_compute_name` le faire prendrait les 80 premiers caractères de tout
        # le texte, deuxième ligne comprise.
        values["name"] = title or self._mobile_first_line(text)
        note = self.create(values)
        return {"note": note._mobile_payload(), "created": True}

    @api.model
    def _mobile_first_line(self, text):
        for line in (text or "").splitlines():
            if line.strip():
                return line.strip()[:80]
        return _("Untitled note")

    @api.model
    def _mobile_clean_color(self, value):
        try:
            color = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return color if 0 <= color <= MAX_COLOR else 0

    @api.model
    def _mobile_browse(self, note_id):
        """La note, si l'appelant peut la LIRE ; ``None`` sinon.

        « Introuvable » et « interdite » rendent la même réponse : distinguer les
        deux dirait qu'une note existe là où l'on n'a pas le droit de regarder.
        """
        try:
            note_id = int(note_id)
        except (TypeError, ValueError):
            return None
        note = self.with_context(active_test=False).browse(note_id).exists()
        if not note:
            return None
        try:
            note.check_access("read")
        except AccessError:
            return None
        return note

    def _mobile_update(self, vals):
        """Modifie titre, texte, épingle ou couleur. Refuse sur conflit."""
        self.ensure_one()
        vals = vals or {}
        seen = vals.get("write_date")
        if seen and self._mobile_datetime(self.write_date) != self._mobile_datetime(
                self._mobile_parse_datetime(seen)):
            return {"conflict": True, "note": self._mobile_payload()}
        values = {}
        if "text" in vals:
            if not self._mobile_html_is_plain(self.body):
                raise UserError(_(
                    "This note has formatting that the phone cannot keep. "
                    "Edit it in Symbifox."))
            values["body"] = self._mobile_text_to_html(self._mobile_clean_text(vals["text"]))
        if "title" in vals:
            title = self._mobile_clean_title(vals["title"])
            # Titre vidé : le serveur le recompose à partir du texte, comme à
            # la création. `_compute_name` saute un nom déjà posé, d'où le
            # calcul fait ici.
            if not title:
                title = self._mobile_first_line(
                    vals.get("text") if "text" in vals
                    else self._mobile_html_to_text(self.body))
            values["name"] = title
        if "pinned" in vals:
            values["pinned"] = bool(vals["pinned"])
        if "color" in vals:
            values["color"] = self._mobile_clean_color(vals["color"])
        if values:
            # L'écriture passe par les droits de l'appelant : la règle
            # d'écriture du module (auteur seul) décide, pas ce code.
            self.write(values)
        return {"conflict": False, "note": self._mobile_payload()}

    # ------------------------------------------------------------------
    # Gestes rapides
    # ------------------------------------------------------------------
    def _mobile_action(self, action, params=None):
        """Applique un geste rapide et rend la note et une phrase à afficher."""
        self.ensure_one()
        params = params or {}
        if action not in ACTIONS:
            raise UserError(_("Unknown action: %s", action))
        # Chaque geste modifie la note (état, épingle, activités suivies,
        # liens) : la règle d'écriture du module s'applique à tous. La
        # vérifier ici plutôt qu'au milieu d'un geste évite une activité posée
        # sur la fiche d'un autre au nom d'une note qu'on ne peut pas toucher.
        self.check_access("write")
        result = {"url": None}
        if action == "archive":
            self.active = False
            result["message"] = _("Note archived.")
        elif action == "unarchive":
            self.active = True
            result["message"] = _("Note restored.")
        elif action in ("pin", "unpin"):
            self.pinned = action == "pin"
            result["message"] = _("Note pinned.") if self.pinned else _("Note unpinned.")
        elif action == "activity":
            try:
                days = int(params.get("days", 0))
            except (TypeError, ValueError):
                days = -1
            if days not in ACTIVITY_DAYS:
                raise UserError(_("Unsupported reminder delay."))
            activities = self._create_activities_for_links(offset_days=days)
            deadline = fields.Date.context_today(self) + timedelta(days=days)
            result["message"] = _(
                "Reminder set for %(date)s (%(count)s activity).",
                date=format_date(self.env, deadline), count=len(activities))
        elif action == "task":
            task = self._mobile_make_task(params)
            result["message"] = _("Task created: %s", task.display_name)
            result["url"] = "/odoo/project.task/%s" % task.id
            result["task_id"] = task.id
        elif action == "reroute":
            target = self._mobile_reroute(params)
            result["message"] = _("Note linked to %s.", target.display_name)
        result["note"] = self._mobile_payload()
        return result

    def _mobile_make_task(self, params):
        """La tâche, par l'assistant du bureau : mêmes champs, même lien retour."""
        try:
            project_id = int(params.get("project_id") or 0)
        except (TypeError, ValueError):
            project_id = 0
        project = self.env["project.project"].browse(project_id).exists() if project_id else None
        if not project:
            raise UserError(_("Choose a project for the task."))
        project.check_access("read")
        wizard = self.env["bf.note.task.wizard"].create({
            "note_id": self.id,
            "name": self._mobile_clean_title(params.get("name")) or self.name or _("Quick note"),
            "description": self.body or "",
            "project_id": project.id,
            "link_back": True,
            "archive_note": bool(params.get("archive")),
            "open_task": True,
        })
        action = wizard.action_create()
        return self.env["project.task"].browse(action["res_id"])

    def _mobile_reroute(self, params):
        """Rattache la note à une fiche, par l'assistant de re-routage."""
        model = params.get("model")
        try:
            res_id = int(params.get("id") or 0)
        except (TypeError, ValueError):
            res_id = 0
        target = self.env["bf.chatter.target"]._browse_if_allowed(model, res_id)
        if not target:
            raise UserError(_("This record no longer exists, or you do not have access to it."))
        mode = params.get("mode") or "replace"
        if mode not in ("replace", "add"):
            raise UserError(_("Unknown mode: %s", mode))
        wizard = self.env["bf.note.reroute"].create({
            "note_ids": [(6, 0, self.ids)],
            "target_reference": "%s,%s" % (target._name, target.id),
            "mode": mode,
        })
        wizard.action_confirm()
        if wizard.result_text and "ERR " in wizard.result_text:
            raise UserError(wizard.result_text.split("\n", 1)[0])
        return target

    # ------------------------------------------------------------------
    # Listes d'appui : projets et fiches
    # ------------------------------------------------------------------
    @api.model
    def _mobile_projects(self, query=None, limit=8):
        """Projets pour « En faire une tâche ».

        Sans recherche : ceux où l'usager a eu une tâche récemment, puis les
        favoris. Une liste alphabétique de 246 projets n'aide personne au pouce.
        """
        Project = self.env["project.project"]
        query = (query or "").strip()
        if query:
            projects = Project.search([("name", "ilike", query)], limit=limit)
        else:
            tasks = self.env["project.task"].search_read(
                [("user_ids", "in", self.env.uid), ("project_id", "!=", False)],
                ["project_id"], order="write_date desc", limit=60)
            seen, ids = set(), []
            for task in tasks:
                pid = task["project_id"][0]
                if pid not in seen:
                    seen.add(pid)
                    ids.append(pid)
                if len(ids) >= limit:
                    break
            projects = Project.browse(ids).exists()
            if len(projects) < limit and "favorite_user_ids" in Project._fields:
                projects |= Project.search([
                    ("favorite_user_ids", "in", self.env.uid),
                    ("id", "not in", projects.ids),
                ], limit=limit - len(projects))
        return [{
            "id": project.id,
            "name": project.name,
            "client": project.partner_id.display_name or "",
        } for project in projects]

    @api.model
    def _mobile_targets(self, query=None):
        return self.env["bf.chatter.target"].search_targets(query, limit=5)
