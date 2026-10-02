"""Mes activités au téléphone, dans l'onglet Tâches.

Une personne porte couramment plusieurs centaines d'activités ouvertes
(relances de pistes, maintenances, documents, tâches), et l'app n'en montrait
aucune. Le rappel posé sur une note disparaissait de l'app dès sa création.

⚠️ Tout passe par les droits de l'appelant, jamais en sudo : le téléphone
ne lit et ne ferme que ce que le bureau permet. (La règle de `mail.activity`
laisse lire et fermer une activité qui m'est assignée, même sur une fiche que
je ne peux pas lire : c'est celle du bureau, on ne l'élargit ni ne la
restreint.)

🔴 Fermer une activité n'est pas toujours fermer la chose. Une maintenance
d'hébergement fabrique son activité à partir de sa planification ; seule sa
propre méthode (`action_mark_done`) pose la prochaine échéance. Fermer
l'activité seule laisserait la maintenance due, sans plus rien pour la
rappeler. Ces cas sont listés dans ``CLOTURES``.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from . import texte_simple

#: Plafond de la liste, toutes dates confondues ; l'app n'en demande que
#: jusqu'à son horizon.
MAX_ACTIVITIES = 500
MAX_FEEDBACK = 5000
MAX_SUMMARY = 200
MAX_NOTE = 20000
#: La note d'une activité est un rappel, pas un document : l'app en montre le
#: début, la fiche d'Odoo garde le reste.
NOTE_EXTRAIT = 2000

#: Les fiches dont l'activité se ferme par la fiche : modèle → (type
#: d'activité qu'elle fabrique, méthode qui la clôt). Un autre type d'activité
#: posé à la main sur la même fiche se ferme normalement.
CLOTURES = {
    "hosting.maintenance.schedule": (
        "hosting_management.mail_activity_type_hosting_maintenance",
        "action_mark_done",
    ),
}

#: Les fiches où l'app planifie une activité. La fiche d'une tâche est le seul
#: écran qui le propose ; un modèle de plus se décide, il ne s'ouvre pas seul.
PLANIFIABLES = ("project.task",)


def _jour(valeur):
    """``AAAA-MM-JJ`` → date, ou ``UserError``. Une date mal formée ne doit
    pas devenir un 500."""
    try:
        jour = fields.Date.to_date(str(valeur or "")[:10])
    except (TypeError, ValueError):
        jour = None
    if not jour:
        raise UserError(_("Date invalide."))
    return jour


class MailActivity(models.Model):
    _inherit = "mail.activity"

    # ------------------------------------------------------------------
    # Lire
    # ------------------------------------------------------------------

    def _mobile_cloture(self):
        """La méthode de fiche qui ferme cette activité, ou ``None``."""
        self.ensure_one()
        regle = CLOTURES.get(self.res_model)
        if not regle or self.res_model not in self.env:
            return None
        attendu = self.env.ref(regle[0], raise_if_not_found=False)
        if not attendu or self.activity_type_id != attendu:
            return None
        fiche = self.env[self.res_model].browse(self.res_id).exists()
        if not fiche or not hasattr(fiche, regle[1]):
            return None
        return getattr(fiche, regle[1])

    def _mobile_event(self):
        """La rencontre que l'activité suit, quand le module agenda la lie."""
        if "calendar_event_id" not in self._fields:
            return self.env["calendar.event"]
        return self.calendar_event_id

    def _mobile_payload(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        echeance = self.date_deadline
        if echeance < today:
            etat = "overdue"
        elif echeance == today:
            etat = "today"
        else:
            etat = "planned"
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        # Le nom du modèle (« Tâche », « Piste ») : une étiquette, pas une donnée.
        modele = self.sudo().res_model_id
        event = self._mobile_event()
        return {
            "id": self.id,
            "res_model": self.res_model or "",
            "res_id": self.res_id or 0,
            "res_name": self.res_name or "",
            "model_label": modele.name or self.res_model or "",
            "type": self.activity_type_id.name or "",
            "type_id": self.activity_type_id.id or 0,
            "icon": self.activity_type_id.icon or "",
            "category": self.activity_category or "default",
            "summary": self.summary or "",
            "note": html2plaintext(self.note or "").strip()[:NOTE_EXTRAIT],
            "deadline": fields.Date.to_string(echeance),
            "state": etat,
            "user": self.user_id.display_name or "",
            "mine": self.user_id == self.env.user,
            # « Fait » passe par la fiche (maintenance) : l'app le dit avant.
            "closes_record": bool(self._mobile_cloture()),
            # Une activité qui suit une rencontre se déplace avec la rencontre.
            "event_id": event.id or 0,
            "url": "%s/odoo/%s/%s" % (base, self.res_model, self.res_id) if base else "",
        }

    @api.model
    def mobile_mine(self, date_to=None):
        """Mes activités ouvertes jusqu'à ``date_to`` incluse, retards compris.

        Sans ``date_to``, toutes. L'ordre est celui de l'échéance : l'app les
        range ensuite dans les intertitres par jour des tâches.
        """
        domain = [("user_id", "=", self.env.uid)]
        if date_to:
            domain.append(("date_deadline", "<=", _jour(date_to)))
        total = self.search_count(domain)
        activites = self.search(domain, order="date_deadline asc, id asc", limit=MAX_ACTIVITIES)
        today = fields.Date.context_today(self)
        return {
            "ok": True,
            "today": fields.Date.to_string(today),
            "activities": [a._mobile_payload(today) for a in activites],
            "total": total,
            "truncated": total > len(activites),
        }

    @api.model
    def mobile_types(self, res_model=None):
        """Les types qu'on peut planifier sur ``res_model`` depuis le téléphone.

        Les types « Réunion » (catégorie ``meeting``) sont écartés : au bureau
        ils ouvrent l'agenda pour créer la rencontre, et une activité « Réunion »
        sans rencontre n'est qu'un rappel mal nommé.
        """
        domain = [("category", "!=", "meeting")]
        domain.append(("res_model", "in", [False, res_model]) if res_model else ("res_model", "=", False))
        return {
            "ok": True,
            "types": [{
                "id": t.id,
                "name": t.name or "",
                "icon": t.icon or "",
                "category": t.category or "default",
                "summary": t.summary or "",
                "delay_count": t.delay_count or 0,
                "delay_unit": t.delay_unit or "days",
            } for t in self.env["mail.activity.type"].search(domain)],
        }

    # ------------------------------------------------------------------
    # Agir
    # ------------------------------------------------------------------

    def mobile_done(self, feedback=None):
        """Fait, avec un mot facultatif.

        Pour une activité fabriquée par sa fiche (``CLOTURES``), c'est la
        fiche qui se clôt ; le mot va alors au fil de la fiche, en note
        interne, puisque la clôture ne prend pas de commentaire.
        """
        self.ensure_one()
        mot = (feedback or "").strip()
        if len(mot) > MAX_FEEDBACK:
            raise UserError(_("Le commentaire est trop long."))
        cloture = self._mobile_cloture()
        if cloture:
            fiche = cloture.__self__
            # Contrôlé AVANT : la clôture journalise avant d'écrire, et un
            # refus à mi-chemin laisserait une trace d'une chose non faite.
            fiche.check_access("write")
            cloture()
            if mot:
                fiche.message_post(
                    body=texte_simple.texte_vers_html(mot),
                    message_type="comment", subtype_xmlid="mail.mt_note")
            return {"ok": True, "closed_record": True}
        self.action_feedback(feedback=mot or False)
        return {"ok": True, "closed_record": False}

    def mobile_reschedule(self, date):
        """Reporter à ``date``. L'heure n'existe pas sur une activité."""
        self.ensure_one()
        jour = _jour(date)
        if self._mobile_event():
            raise UserError(_(
                "Cette activité suit une rencontre : déplacez la rencontre, "
                "l'activité suivra."))
        self.write({"date_deadline": jour})
        return {"ok": True, "activity": self._mobile_payload()}

    @api.model
    def mobile_create(self, vals):
        """Planifier une activité, à mon nom, sur une fiche de ``PLANIFIABLES``."""
        vals = vals or {}
        res_model = vals.get("res_model")
        if res_model not in PLANIFIABLES or res_model not in self.env:
            raise UserError(_("Ce type de fiche n'accepte pas d'activité depuis le téléphone."))
        try:
            res_id = int(vals.get("res_id") or 0)
            type_id = int(vals.get("activity_type_id") or 0)
        except (TypeError, ValueError):
            raise UserError(_("Fiche ou type d'activité invalide.")) from None
        fiche = self.env[res_model].browse(res_id).exists()
        if not fiche:
            raise UserError(_("Fiche introuvable."))
        fiche.check_access("read")
        type_activite = self.env["mail.activity.type"].browse(type_id).exists()
        offerts = {t["id"] for t in self.mobile_types(res_model)["types"]}
        if not type_activite or type_activite.id not in offerts:
            raise UserError(_("Type d'activité inconnu pour cette fiche."))
        resume = (vals.get("summary") or "").strip()
        note = (vals.get("note") or "").strip()
        if len(resume) > MAX_SUMMARY or len(note) > MAX_NOTE:
            raise UserError(_("Le résumé ou la note est trop long."))
        activite = fiche.activity_schedule(
            activity_type_id=type_activite.id,
            date_deadline=_jour(vals.get("date")),
            summary=resume or False,
            note=texte_simple.texte_vers_html(note) if note else False,
            user_id=self.env.uid,
            # `activity_schedule` marque d'office l'activité « automatique » ;
            # celle-ci, une personne l'a planifiée.
            automated=False,
        )
        return {"ok": True, "activity": activite._mobile_payload()}
