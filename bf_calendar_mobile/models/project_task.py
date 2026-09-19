"""Les échéances de l'usager, dans leur propre onglet.

⚠️ Pourquoi elles ne vont PAS dans la grille de l'agenda : mesuré sur une
base réelle, une personne porte couramment plus de cent échéances sur quatorze
jours contre une poignée de rencontres, avec des pointes à plus de vingt sur
une seule journée. Les poser sur la grille noierait les rencontres au lieu de
les situer. La grille ne reçoit donc qu'un compte par jour, et l'onglet Tâches
porte la liste.

Les états d'attente (`04_waiting_*`) comptent comme ouverts : une tâche qui
attend un client a quand même une échéance qui approche, et la cacher ferait
mentir le compte affiché sur la grille.
"""

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

from . import odoo_palette

from .calendar_event import iso

# Ouvert = tout ce qui n'est ni fait ni annulé. Écrit en négatif à dessein :
# une nouvelle étape d'attente apparaîtra du bon côté sans qu'on y touche.
CLOSED_STATES = ("1_done", "1_canceled")

MAX_TASKS = 300

# La recherche rend une liste à parcourir du pouce, pas un export : au-delà, on
# précise sa recherche. Les mots au-delà du cinquième n'affinent plus rien.
SEARCH_LIMIT = 50
SEARCH_WORDS = 5


class ProjectTask(models.Model):
    _inherit = "project.task"

    @api.model
    def _mobile_domain(self):
        return [
            ("user_ids", "in", [self.env.uid]),
            ("state", "not in", CLOSED_STATES),
        ]

    def _mobile_payload(self):
        """Assez pour AGIR sur la tâche, pas seulement la lire.

        La version précédente ne rendait que du texte, si bien que le seul
        geste offert était « ouvrir dans Odoo ». Les étiquettes, l'étape et
        l'état partent maintenant avec leur identifiant, parce qu'un écran qui
        affiche « Prioritaire » sans pouvoir l'enlever est une consultation
        déguisée en outil.
        """
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return {
            "id": self.id,
            "name": self.name or "",
            "project": self.project_id.display_name or "",
            "project_id": self.project_id.id or 0,
            "deadline": iso(self.date_deadline),
            "priority": self.priority or "0",
            "state": self.state or "",
            "state_label": dict(self._fields["state"].selection or []).get(
                self.state, self.state or ""),
            "stage": self.stage_id.display_name or "",
            "stage_id": self.stage_id.id or 0,
            "partner": self.partner_id.display_name or "",
            # La couleur propre de la tâche, quand elle en porte une : c'est
            # celle que le kanban affiche.
            "color": odoo_palette.couleur_etiquette(self.color) if self.color else "",
            "tags": [
                {"id": tag.id,
                 "name": tag.display_name or "",
                 "color": odoo_palette.couleur_etiquette(tag.color)}
                for tag in self.tag_ids
            ],
            "done": self.state in CLOSED_STATES,
            "url": "%s/odoo/project.task/%s" % (base, self.id) if base else "",
        }

    def mobile_detail(self):
        """Une tâche par identifiant, pour l'ouvrir depuis un courriel.

        Hors de « mes tâches » à dessein : un courriel classé sur une tâche
        que je ne porte pas s'ouvre quand même, dans la limite de ce que mes
        droits laissent lire — l'ORM refuse le reste et le contrôleur rend 403.
        """
        self.ensure_one()
        return {"ok": True, "task": self._mobile_payload()}

    @api.model
    def mobile_todo(self, date_from=None, date_to=None, include_undated=False):
        """Trois seaux : en retard, dans la fenêtre, et sans échéance.

        Le retard est rendu quelle que soit la fenêtre demandée. Une échéance
        dépassée ne disparaît pas parce qu'on regarde la semaine prochaine.
        """
        start, stop = self.env["calendar.event"]._mobile_window(date_from, date_to)
        now = fields.Datetime.now()
        base = self._mobile_domain()

        overdue = self.search(
            base + [("date_deadline", "<", now), ("date_deadline", "!=", False)],
            order="date_deadline asc", limit=MAX_TASKS)
        window = self.search(
            base + [("date_deadline", ">=", now), ("date_deadline", "<", stop)],
            order="date_deadline asc", limit=MAX_TASKS)

        result = {
            "ok": True,
            "from": iso(start),
            "to": iso(stop),
            "overdue": [task._mobile_payload() for task in overdue],
            "window": [task._mobile_payload() for task in window],
            "undated_count": self.search_count(base + [("date_deadline", "=", False)]),
        }
        if include_undated:
            undated = self.search(
                base + [("date_deadline", "=", False)],
                order="priority desc, write_date desc", limit=MAX_TASKS)
            result["undated"] = [task._mobile_payload() for task in undated]
        return result

    @api.model
    def mobile_deadline_counts(self, date_from=None, date_to=None, tz=None):
        """Le compte par jour pour la pastille de la grille.

        Regroupé côté serveur : rapporter des centaines de tâches pour n'en
        afficher que le nombre ferait payer au téléphone une liste qu'il jette.

        ⚠️ Le jour est calculé dans un fuseau, pas en UTC. Une échéance du 4
        septembre 20:00 à Montréal tombe le 5 en UTC, et la pastille se poserait
        alors sur la mauvaise case. On évite aussi ``read_group`` sur ``:day`` :
        sa clé est une étiquette traduite, donc illisible pour un client.

        ⚠️ Et dans le fuseau de l'APPAREIL quand l'app le donne (``tz``,
        identifiant IANA). L'app pose ces clés sur les jours de sa propre
        grille : un compte réglé sur Auckland lu depuis un téléphone à Montréal
        décalait chaque pastille d'un jour. Un
        fuseau absent ou inconnu retombe sur celui du compte, comme avant ; la
        réponse dit toujours lequel a servi.
        """
        start, stop = self.env["calendar.event"]._mobile_window(date_from, date_to)
        rows = self.search_read(
            self._mobile_domain() + [
                ("date_deadline", ">=", start), ("date_deadline", "<", stop),
            ],
            ["date_deadline"],
            limit=MAX_TASKS * 4,
        )
        tz = pytz.timezone(self._mobile_grouping_tz(tz))
        counts = {}
        for row in rows:
            deadline = row.get("date_deadline")
            if not deadline:
                continue
            local = pytz.utc.localize(deadline).astimezone(tz).date()
            key = local.isoformat()
            counts[key] = counts.get(key, 0) + 1
        return {"ok": True, "tz": str(tz), "counts": counts}

    @api.model
    def _mobile_grouping_tz(self, tz=None):
        """Le fuseau demandé s'il existe, sinon celui du compte, sinon UTC.

        Validé contre la liste de ``pytz`` et non par ``pytz.timezone`` sous
        ``try`` : la liste est fermée, et une chaîne arbitraire venue du
        téléphone n'a pas à atteindre le chargeur de fichiers de zones.
        """
        if isinstance(tz, str) and tz.strip() in pytz.all_timezones_set:
            return tz.strip()
        return self.env.user.tz or "UTC"

    @api.model
    def mobile_search(self, query, limit=SEARCH_LIMIT):
        """Retrouver une de mes tâches ouvertes, où que tombe son échéance.

        Les seaux de l'écran ne couvrent que l'horizon choisi, et le plafond
        des sans-échéance en laisse dehors sur une base bien remplie : filtrer ce que
        le téléphone tient déjà laisserait introuvable une tâche du mois
        prochain. La recherche part donc au serveur, sur toutes mes tâches
        ouvertes.

        Chaque mot doit se trouver quelque part (titre, projet, client ou
        étiquette) : « facture nord » trouve la tâche « Facture » du projet
        « Nord », ce qu'une seule chaîne cherchée dans le titre raterait. Un
        nombre seul, avec ou sans « # », trouve aussi la tâche par identifiant,
        la forme sous laquelle on se les cite.
        """
        terme = " ".join((query or "").split())
        numero = terme.lstrip("#")
        par_numero = numero.isdigit() and int(numero) < 2 ** 31
        if len(terme) < 2 and not par_numero:
            return {"ok": True, "query": terme, "tasks": [], "more": False}
        try:
            limit = max(1, min(int(limit or SEARCH_LIMIT), SEARCH_LIMIT))
        except (TypeError, ValueError):
            limit = SEARCH_LIMIT

        correspond = expression.AND([
            expression.OR([
                [("name", "ilike", mot)],
                [("project_id.name", "ilike", mot)],
                [("partner_id.name", "ilike", mot)],
                [("tag_ids.name", "ilike", mot)],
            ])
            for mot in terme.split()[:SEARCH_WORDS]
        ])
        if par_numero:
            correspond = expression.OR([[("id", "=", int(numero))], correspond])

        # Une de plus que la limite : de quoi dire « il y en a d'autres » sans
        # payer un second comptage.
        taches = self.search(
            expression.AND([self._mobile_domain(), correspond]),
            order="date_deadline asc nulls last, priority desc, id desc",
            limit=limit + 1)
        return {
            "ok": True,
            "query": terme,
            "tasks": [t._mobile_payload() for t in taches[:limit]],
            "more": len(taches) > limit,
        }

    # ------------------------------------------------------------------
    # Écrire
    # ------------------------------------------------------------------

    # Liste blanche assumée. Une tâche porte des dizaines de champs, dont des
    # calculés et des financiers ; ouvrir `write` en confiance donnerait au
    # téléphone plus de pouvoir que l'écran d'à côté.
    _MOBILE_WRITE_FIELDS = (
        "name", "date_deadline", "priority", "state", "stage_id",
        "project_id", "tag_ids", "description",
    )

    _MOBILE_CREATE_FIELDS = (
        "name", "project_id", "date_deadline", "priority", "tag_ids",
        "description",
    )

    def mobile_write(self, vals):
        """Modifier une tâche depuis le téléphone, champ par champ.

        ⚠️ `bf_time_of_day` réécrit l'HEURE d'une échéance sur la plage horaire
        de la tâche. Poser une échéance depuis le mobile sans effacer la plage
        rendrait une heure différente de celle demandée, sans le dire. On
        efface donc la plage quand l'appelant fixe explicitement une heure.
        """
        self.ensure_one()
        vals = {k: v for k, v in (vals or {}).items() if k in self._MOBILE_WRITE_FIELDS}
        if not vals:
            raise UserError(_("Rien à modifier."))
        if "state" in vals and vals["state"] not in dict(
                self._fields["state"].selection or []):
            raise UserError(_("État de tâche inconnu."))
        if "tag_ids" in vals:
            vals["tag_ids"] = [(6, 0, [int(t) for t in vals["tag_ids"]])]
        if "date_deadline" in vals and "time_of_day_id" in self._fields:
            vals["time_of_day_id"] = False
        self.write(vals)
        return {"ok": True, "task": self._mobile_payload()}

    def mobile_done(self, done=True):
        """Le geste le plus fréquent, à un seul appel.

        Rouvrir remet « en cours » plutôt que l'état d'avant : celui-ci n'est
        pas conservé, et inventer un retour en arrière serait pire que de
        nommer franchement où la tâche repart.
        """
        self.ensure_one()
        self.write({"state": "1_done" if done else "01_in_progress"})
        return {"ok": True, "task": self._mobile_payload()}

    @api.model
    def mobile_create(self, vals):
        """Créer une tâche. Le projet est obligatoire, faute de quoi elle
        atterrirait hors de tout suivi."""
        vals = {k: v for k, v in (vals or {}).items() if k in self._MOBILE_CREATE_FIELDS}
        if not vals.get("name"):
            raise UserError(_("Une tâche a besoin d'un titre."))
        if not vals.get("project_id"):
            raise UserError(_("Choisissez un projet."))
        if "tag_ids" in vals:
            vals["tag_ids"] = [(6, 0, [int(t) for t in vals["tag_ids"]])]
        vals["user_ids"] = [(6, 0, [self.env.uid])]
        if "date_deadline" in vals and "time_of_day_id" in self._fields:
            vals["time_of_day_id"] = False
        task = self.create(vals)
        return {"ok": True, "task": task._mobile_payload()}

    # Combien de tâches on remonte pour établir l'ordre de fréquentation.
    # 500 couvre plusieurs mois d'activité sans faire payer la lecture.
    RECENCE_ECHANTILLON = 500

    def _mobile_projets_recents(self, limite=200):
        """Les projets, du plus récemment fréquenté au plus ancien.

        🔴 L'ordre alphabétique est le pire pour ce geste. Sur des centaines de
        projets, celui qu'on cherche en créant une tâche est presque toujours
        celui où l'on vient de travailler, et un « A » avant un « W » ne dit
        rien de cela.

        La fréquentation se mesure sur les TÂCHES de la personne, pas sur la
        date d'écriture du projet : un projet touché par le cron d'un autre
        remonterait sinon en tête sans que personne y ait mis les pieds. Les
        tâches closes comptent, parce qu'avoir fermé une tâche hier dans un
        projet est un excellent signe qu'on va en rouvrir une aujourd'hui.

        Les projets jamais fréquentés suivent, par ordre alphabétique : sans
        repère de récence, le nom redevient le meilleur classement.
        """
        lignes = self.sudo().search_read(
            [("user_ids", "in", [self.env.uid]), ("project_id", "!=", False)],
            ["project_id"],
            # ⚠️ `id desc` en second : Odoo complète un tri par `id` ASCENDANT,
            # et toutes les tâches écrites dans la même transaction partagent
            # le même `write_date`. À égalité, l'ordre partait donc du plus
            # ANCIEN, exactement l'inverse de ce qu'on demande ici.
            order="write_date desc, id desc",
            limit=self.RECENCE_ECHANTILLON,
        )
        vus = []
        for ligne in lignes:
            pid = ligne["project_id"] and ligne["project_id"][0]
            if pid and pid not in vus:
                vus.append(pid)

        Projet = self.env["project.project"]
        # ⚠️ L'échantillon passe en `sudo` pour rester rapide, mais la liste
        # rendue est relue avec les DROITS DE L'APPELANT : une `search` refait
        # sur les seuls identifiants vus suffit, elle applique les règles
        # d'enregistrement et ne rend que le lisible. Sans ce second passage,
        # un projet interdit apparaîtrait dans le sélecteur parce qu'une tâche
        # y pointe.
        lisibles = set(Projet.search([("id", "in", vus)]).ids)
        ordonnes = [pid for pid in vus if pid in lisibles]

        restants = Projet.search(
            [("id", "not in", ordonnes)],
            order="name asc", limit=max(limite - len(ordonnes), 0))
        return list(Projet.browse(ordonnes)) + list(restants)

    @api.model
    def mobile_options(self, project_id=None):
        """De quoi remplir les sélecteurs, sans deviner côté app.

        Les étapes dépendent du projet : les servir toutes ferait choisir une
        étape qui n'existe pas là où la tâche vit.
        """
        projets = self._mobile_projets_recents()
        etapes = self.env["project.task.type"]
        if project_id:
            etapes = etapes.search(
                [("project_ids", "in", [int(project_id)])], order="sequence asc")
        etiquettes = self.env["project.tags"].search([], order="name asc", limit=300)
        return {
            "ok": True,
            "projects": [{"id": p.id, "name": p.display_name or ""} for p in projets],
            "stages": [{"id": e.id, "name": e.display_name or ""} for e in etapes],
            "tags": [
                {"id": t.id, "name": t.display_name or "",
                 "color": odoo_palette.couleur_etiquette(t.color)}
                for t in etiquettes
            ],
            "states": [
                {"value": v, "label": l}
                for v, l in (self._fields["state"].selection or [])
            ],
            "priorities": [
                {"value": v, "label": l}
                for v, l in (self._fields["priority"].selection or [])
            ],
        }
