"""Le geste « faire un relevé » : la grille à l'écran, puis le relevé.

Premier tapotement : une question qui porte la grille en ``formulaire``, et rien
d'écrit. Le téléphone la remplit et renvoie ``choix = enregistrer`` avec les
réponses : c'est ce second appel qui écrit le relevé.

⚠️ ``_enregistrer_releve`` est l'aide que d'autres satellites appellent (le point de
tournée avec grille) : elle ne sait rien de la porte, du menu ou de la tournée.
"""
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

CHOIX_ENREGISTRER = "enregistrer"


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("reading", "Faire un relevé")],
        ondelete={"reading": "cascade"},
    )

    def _question_releve(self, tag, grille, params, titre=None, message=None):
        return {
            "titre": titre or grille.name,
            "message": message or " · ".join(filter(None, [tag.place, grille.reference])),
            "formulaire": grille.sudo()._formulaire(porte=(params or {}).get("porte")),
            "choix": [{"cle": CHOIX_ENREGISTRER, "libelle": _("Enregistrer le relevé"),
                       "style": "principal", "saisie": None}],
        }

    def _executer_reading(self, tag, tap, params):
        self.ensure_one()
        grille = tag.sudo().checklist_id
        if not grille:
            raise UserError(_("Cette pastille ne porte aucune grille de relevé."))
        if not grille.active:
            raise UserError(_("La grille « %s » a été retirée.", grille.name))
        if params.get("choix") != CHOIX_ENREGISTRER:
            return self._question_releve(tag, grille, params)
        releve = self._enregistrer_releve(tag, grille, params)
        return {"titre": grille.name, "url": None, "message": releve._phrase()}

    def _enregistrer_releve(self, tag, grille, params, place=None, nom=None):
        """Valide les réponses, écrit le relevé, et signale les anomalies. Rend le relevé."""
        self.ensure_one()
        grille = grille.sudo()
        champs = grille._formulaire(porte=params.get("porte"))
        if not champs:
            raise UserError(_("La grille « %s » n'a aucun élément.", grille.name))
        # 🔴 Un téléphone qui n'a jamais reçu la grille (ancienne application) renvoie
        # « enregistrer » sans rien : on refuse avec la raison, au lieu d'écrire un
        # relevé vide qui aurait l'air conforme.
        if not (params.get("reponses") or {}):
            raise UserError(_("Ce relevé demande de remplir la grille : mettez à jour "
                              "l'application, ou ouvrez la pastille par son code QR."))
        lues = self._lire_formulaire(champs, params)
        qui = self.env.user
        lignes, anomalies = [], []
        for rang, element in enumerate(grille.item_ids):
            valeur = lues.get(element._cle())
            anomalie = element._en_anomalie(valeur)
            if anomalie:
                anomalies.append(element.name)
            plage = ("%g à %g %s" % (element.min_value, element.max_value, element.unit or "")).strip() \
                if element.kind == "nombre" and element.has_range else False
            lignes.append((0, 0, {
                "sequence": rang, "item_id": element.id, "name": element.name, "kind": element.kind,
                "answered": valeur is not None, "anomaly": anomalie, "unit": element.unit,
                "range_text": plage,
                "value_bool": bool(valeur) if element.kind == "conforme" else False,
                "value_float": valeur if element.kind == "nombre" and valeur is not None else 0.0,
                "value_text": valeur if element.kind in ("texte", "choix") else False,
            }))
        # 🔴 Une grille dont tous les éléments sont facultatifs, ou une application
        # qui renvoie une clé inconnue, produisait un relevé « conforme » où rien
        # n'avait été répondu. C'est le « relevé vide qui a l'air conforme » que la
        # garde d'au-dessus vise, et elle ne l'attrapait pas.
        if not any(valeur is not None for cle, valeur in lues.items() if cle != "_nom"):
            raise UserError(_("Ce relevé est vide : répondez à au moins un élément de la grille."))
        releve = self.env["bf.nfc.reading"].sudo().create({
            "checklist_id": grille.id,
            "tag_id": tag.id,
            "tag_name": nom or tag.name,
            "place": place or tag.place or "",
            "res_model": tag.res_model or False,
            "res_id": tag.res_id or False,
            "user_id": qui.id,
            "signed_name": lues.get("_nom") if params.get("porte") == "signed" else False,
            "tapped_at": self._moment(params),
            "offline": bool(params.get("differe")),
            "door": params.get("porte"),
            "company_id": tag.company_id.id,
            "anomaly_count": len(anomalies),
            "state": "anomalie" if anomalies else "conforme",
            "line_ids": lignes,
        })
        if anomalies:
            releve._signaler(anomalies)
        return releve


class BfNfcReading(models.Model):
    _inherit = "bf.nfc.reading"

    def _phrase(self):
        self.ensure_one()
        if self.state == "conforme":
            return _("Relevé %s enregistré : tout est conforme.", self.name)
        responsable = self.checklist_id.responsible_id
        if responsable:
            return _("Relevé %(nom)s enregistré : %(n)s anomalie(s) signalée(s) à %(qui)s.",
                     nom=self.name, n=self.anomaly_count, qui=responsable.name)
        return _("Relevé %(nom)s enregistré : %(n)s anomalie(s) à corriger.",
                 nom=self.name, n=self.anomaly_count)

    def _signaler(self, anomalies):
        """Une activité à la personne responsable, et une note qui nomme les éléments."""
        self.ensure_one()
        responsable = self.checklist_id.responsible_id or self.tag_id.create_uid or self.env.user
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        self.sudo().activity_schedule(
            activity_type_id=todo.id if todo else False, user_id=responsable.id,
            summary=_("Anomalie : %s", " · ".join(filter(None, [self.tag_name, self.place]))),
            note=Markup("<p>%s</p>") % escape(", ".join(anomalies)))
        self.sudo().message_post(
            body=Markup("<p>%s</p>") % escape(_("Anomalie relevée par %(qui)s : %(quoi)s.",
                                                qui=self.verifier, quoi=", ".join(anomalies))),
            message_type="comment", subtype_xmlid="mail.mt_note")
