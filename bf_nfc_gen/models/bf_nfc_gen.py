"""Les skills de Gen qu'une pastille peut lancer, et les demandes qu'elle a faites.

🔴 **Le pont décide de ce qui est lançable, l'instance de ce qui est permis.** La
liste des skills vient du pont (``/nfc-skills``) et ne se crée pas à la main ici :
l'administrateur coche « Permis sur cette instance ». Le pont revalide chaque
demande contre sa propre liste, parce que rien n'authentifie qui l'appelle.

🔴 **Le skill est un CHAMP de la pastille**, jamais un paramètre : ce que la
personne qui tape envoie n'entre pas dans ``_params`` (cf. le socle).

⚠️ L'appel au pont part APRÈS le commit (``postcommit``) et dans un fil : la demande
doit exister en base quand le pont écrit son état, et la passe dure des minutes.
"""
import logging
import threading
from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.tools.misc import get_lang
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_ai_bridge.tools import transport

_logger = logging.getLogger(__name__)

CHOIX_LANCER = "lancer"
# Une demande « en cours » depuis plus longtemps que ça ne bloque plus un nouveau
# lancement : le pont a pu redémarrer et la perdre sans jamais écrire son état.
PEREMPTION = timedelta(minutes=20)
ETATS = [("queued", "Sent"), ("running", "In progress"), ("done", "Done"),
         ("busy", "Gen busy"), ("error", "Failed")]


class BfNfcGenSkill(models.Model):
    _name = "bf.nfc.gen.skill"
    _description = "Gen skill a tag can run"
    _order = "name"

    skill = fields.Char(string="Skill", required=True, readonly=True)
    name = fields.Char(string="Label", required=True, readonly=True)
    description = fields.Text(readonly=True)
    modeles = fields.Char(string="Record types", readonly=True,
                          help="The models this skill accepts, separated "
                               "by commas.")
    allowed = fields.Boolean(
        string="Allowed on this instance",
        help="Checked: an “Ask Gen” tag can run this skill. The bridge "
             "remains the sole judge of what it accepts.")
    published = fields.Boolean(string="Published by the bridge", readonly=True, default=True,
                               help="Unchecked: the bridge no longer "
                                    "publishes it; it can no longer be "
                                    "run.")
    last_sync = fields.Datetime(string="Refreshed on", readonly=True)

    _sql_constraints = [("skill_unique", "unique(skill)", "This skill is "
                                                          "already in the "
                                                          "list.")]

    def _modeles(self):
        self.ensure_one()
        return [m.strip() for m in (self.modeles or "").split(",") if m.strip()]

    @api.model
    def action_actualiser(self):
        """Relit la liste que le pont publie pour ce locataire."""
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Refreshing Gen's skills is reserved for "
                                "managers."))
        pont = self.env["bf.ai.bridge"]
        pont.check_available(_("The skill list cannot be refreshed."))
        locataire = pont.tenant()
        try:
            reponse = pont.call("/nfc-skills", {"tenant": locataire}, timeout=15)
        except Exception as exc:  # noqa: BLE001 — la cause est rendue à l'écran
            raise UserError(_("The bridge did not return the skill list (%s).", type(exc).__name__))
        # 🔴 Une liste vide n'est pas une réponse : le pont répond aussi « aucun skill »
        # quand le locataire déclaré ne lui dit rien (paramètre bf_ai_bridge.tenant mal
        # posé). Écrire cette réponse-là déclasserait tous les skills d'un coup.
        if not (reponse.get("skills") or []):
            raise UserError(_(
                "The bridge publishes no tag-launchable skill for tenant "
                "“%s”. Nothing was changed: check the bf_ai_bridge.tenant "
                "system parameter.", locataire))
        vus = self.browse()
        maintenant = fields.Datetime.now()
        for entree in reponse.get("skills") or []:
            nom = str(entree.get("skill") or "").strip()
            if not nom:
                continue
            valeurs = {"name": str(entree.get("libelle") or nom)[:120],
                       "description": str(entree.get("description") or "")[:2000],
                       "modeles": ",".join(str(m) for m in entree.get("modeles") or []),
                       "published": True, "last_sync": maintenant}
            ligne = self.sudo().with_context(active_test=False).search([("skill", "=", nom)], limit=1)
            if ligne:
                ligne.write(valeurs)
            else:
                ligne = self.sudo().create(dict(valeurs, skill=nom))
            vus |= ligne
        # ⚠️ Un skill que le pont ne publie plus reste en liste mais ne se lance plus :
        # le retirer ferait tomber les pastilles qui le portent.
        # 🔴 Et sa permission n'est PAS décochée : une absence temporaire (pont en cours
        # de mise à jour, locataire mal déclaré) effacerait des choix d'administration
        # qu'il faudrait refaire à la main, un par un. Le geste vérifie les deux.
        (self.sudo().search([]) - vus).write({"published": False})
        return {"type": "ir.actions.client", "tag": "reload"}


class BfNfcGenRequest(models.Model):
    _name = "bf.nfc.gen.request"
    _description = "Gen request by tag"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    skill_id = fields.Many2one("bf.nfc.gen.skill", string="Skill", required=True, readonly=True,
                               ondelete="restrict")
    tag_id = fields.Many2one("bf.nfc.tag", string="Tag", readonly=True, ondelete="set null")
    user_id = fields.Many2one("res.users", string="Requested by", required=True, readonly=True)
    res_model = fields.Char(readonly=True, required=True)
    res_id = fields.Many2oneReference(model_field="res_model", readonly=True, required=True)
    target_name = fields.Char(string="Record", readonly=True)
    state = fields.Selection(ETATS, string="Status", default="queued", required=True, readonly=True,
                             tracking=True, index=True)
    message = fields.Char(readonly=True)
    date_end = fields.Datetime(string="Finished on", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True, default=lambda self: self.env.company)

    def _en_cours(self):
        return self.state in ("queued", "running") and \
            self.create_date and fields.Datetime.now() - self.create_date < PEREMPTION

    def set_state(self, state, message=""):
        """L'état que le pont écrit en fin de passe. Réservé à la gestion et au système.

        ⚠️ Public parce que le pont l'appelle en XML-RPC. Ce qu'il ne fait pas : rien
        d'autre qu'un état, un message court et une activité à la personne.
        """
        # Sans langue au contexte (travail planifié, XML-RPC), les libellés écrits au chatter
        # (suivi des états, message de création) sortaient en anglais depuis la bascule.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
        if not (self.env.user.has_group("bf_nfc.group_nfc_manager")
                or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Only the bridge writes the status of a "
                                "Gen request."))
        if state not in dict(ETATS):
            raise UserError(_("Unknown status: %s", state))
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for demande in self.sudo():
            valeurs = {"state": state, "message": (message or "")[:250]}
            if state in ("done", "error", "busy"):
                valeurs["date_end"] = fields.Datetime.now()
            demande.write(valeurs)
            if state in ("done", "error"):
                demande.activity_schedule(
                    activity_type_id=todo.id if todo else False, user_id=demande.user_id.id,
                    summary=(_("Gen finished: %s", demande.skill_id.name) if state == "done"
                             else _("Gen did not complete: %s", demande.skill_id.name)),
                    note=_("%(fiche)s. %(message)s", fiche=demande.target_name,
                           message=(message or "")[:250]))
        return True

    @api.model
    def _cron_perimer(self):
        """Déclasse les demandes dont le pont n'a plus donné de nouvelles."""
        # Sans langue au contexte (travail planifié, XML-RPC), les libellés écrits au chatter
        # (suivi des états, message de création) sortaient en anglais depuis la bascule.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
        limite = fields.Datetime.now() - PEREMPTION
        oubliees = self.sudo().search([("state", "in", ("queued", "running")),
                                       ("create_date", "<", limite)])
        if oubliees:
            oubliees.set_state("error", _(
                "No news from the bridge for %s minutes. Check the AI "
                "assistant service logs; nothing says whether the pass "
                "ran.", int(PEREMPTION.total_seconds() // 60)))
        return len(oubliees)

    def _transmettre_apres_commit(self):
        """Poser l'appel au pont pour après la validation de la transaction."""
        self.ensure_one()
        pont = self.env["bf.ai.bridge"]
        charge = {
            "tenant": pont.tenant(),
            "skill": self.skill_id.skill,
            "res_model": self.res_model,
            "res_id": self.res_id,
            "request_id": self.id,
            "triggered_by": self.user_id.login,
        }
        socket_path = pont.socket_path()
        db_name, demande_id, uid = self.env.cr.dbname, self.id, self.env.uid

        def _envoyer():
            from odoo import api as _api
            from odoo.modules.registry import Registry as _registry
            # ⚠️ La demande doit être EN BASE avant qu'un agent parte : un point de
            # reprise défait après l'enregistrement de l'appel l'aurait effacée, et le
            # pont lancerait une passe que rien ne suit.
            with _registry(db_name).cursor() as cr:
                if not _api.Environment(cr, uid, {})["bf.nfc.gen.request"].sudo().browse(demande_id).exists():
                    _logger.warning("Demande à Gen %s absente au moment d'appeler le pont : rien n'est lancé.",
                                    demande_id)
                    return
            cause = None
            try:
                reponse = transport.post(socket_path, "/nfc-skill", charge, 20)
                statut, message = reponse.get("status", "?"), reponse.get("message", "")
            except Exception as exc:  # noqa: BLE001 — la cause est rendue telle quelle
                statut, message, cause = "error", "", type(exc).__name__
            if statut == "ok":
                return
            with _registry(db_name).cursor() as cr:
                # ⚠️ En superutilisateur : la personne qui a tapé n'a pas forcément le
                # droit d'écrire un état (le geste peut être ouvert au-delà de la gestion).
                env = _api.Environment(cr, SUPERUSER_ID, {})
                demande = env["bf.nfc.gen.request"].browse(demande_id).exists()
                if demande:
                    if cause:
                        # 🔴 18.0.1.0.2 : `_()` hors de tout environnement levait dans ce fil, et la
                        # demande restait « Transmise » jusqu'au guet quand le pont était
                        # injoignable. Le texte se rédige ici, dans la langue de la personne.
                        message = env["bf.nfc.gen.request"].with_context(
                            lang=demande.user_id.lang or get_lang(env).code).env._("Bridge "
                                                                        "unreachable "
                                                                        "(%s).") % cause
                    demande.set_state("busy" if statut == "busy" else "error", message)

        self.env.cr.postcommit.add(lambda: threading.Thread(target=_envoyer, daemon=True).start())


class BfNfcTag(models.Model):
    _inherit = "bf.nfc.tag"

    gen_skill_id = fields.Many2one(
        "bf.nfc.gen.skill", string="Gen skill", ondelete="restrict", tracking=True,
        domain=[("allowed", "=", True), ("published", "=", True)])
    gen_request_ids = fields.One2many("bf.nfc.gen.request", "tag_id", string="Gen "
                                                                             "requests")


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("gen_skill", "Ask Gen")],
        ondelete={"gen_skill": "cascade"},
    )

    def _executer_gen_skill(self, tag, tap, params):
        self.ensure_one()
        self._exiger_une_personne(params)
        skill = tag.sudo().gen_skill_id
        if not skill:
            raise UserError(_("This tag points to no Gen skill."))
        if not (skill.allowed and skill.published):
            raise UserError(_("“%s” is not allowed on this instance.", skill.name))
        if tag.res_model not in skill._modeles():
            raise UserError(_("“%(skill)s” does not target this record type.", skill=skill.name))
        cible = tag._cible()
        if not cible.exists():
            raise UserError(_("This tag's record no longer exists."))
        pont = self.env["bf.ai.bridge"]
        if not pont.available():
            raise UserError(_("Gen cannot be reached right now."))
        Demande = self.env["bf.nfc.gen.request"].sudo()
        deja = Demande.search([("skill_id", "=", skill.id), ("res_model", "=", tag.res_model),
                               ("res_id", "=", tag.res_id), ("state", "in", ("queued", "running"))],
                              order="create_date desc", limit=1)
        if deja and deja._en_cours():
            return {"titre": skill.name, "url": None, "choix": [],
                    "message": _("Gen has been working on it since "
                                 "%(heure)s, requested by %(qui)s.",
                                 heure=self._heure(deja.create_date), qui=deja.user_id.name)}
        if params.get("choix") != CHOIX_LANCER:
            return {
                "titre": skill.name,
                "message": _("On “%s”. Gen will work on its own; an "
                             "activity will let you know when it is done.", cible.display_name),
                "choix": [{"cle": CHOIX_LANCER, "libelle": _("Run"), "style": "principal",
                           "saisie": None}],
            }
        demande = Demande.create({
            "skill_id": skill.id, "tag_id": tag.id, "user_id": self.env.user.id,
            "res_model": tag.res_model, "res_id": tag.res_id,
            "target_name": cible.display_name, "company_id": tag.company_id.id,
        })
        demande._transmettre_apres_commit()
        return {"titre": skill.name, "url": None,
                "message": _("Request sent to Gen, on “%s”.", cible.display_name)}
