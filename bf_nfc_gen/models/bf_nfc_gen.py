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
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_ai_bridge.tools import transport

_logger = logging.getLogger(__name__)

CHOIX_LANCER = "lancer"
# Une demande « en cours » depuis plus longtemps que ça ne bloque plus un nouveau
# lancement : le pont a pu redémarrer et la perdre sans jamais écrire son état.
PEREMPTION = timedelta(minutes=20)
ETATS = [("queued", "Transmise"), ("running", "En cours"), ("done", "Terminée"),
         ("busy", "Gen occupé"), ("error", "Échec")]


class BfNfcGenSkill(models.Model):
    _name = "bf.nfc.gen.skill"
    _description = "Skill de Gen lançable par pastille"
    _order = "name"

    skill = fields.Char(string="Skill", required=True, readonly=True)
    name = fields.Char(string="Libellé", required=True, readonly=True)
    description = fields.Text(readonly=True)
    modeles = fields.Char(string="Types de fiche", readonly=True,
                          help="Les modèles que ce skill accepte, séparés par des virgules.")
    allowed = fields.Boolean(
        string="Permis sur cette instance",
        help="Coché : une pastille « Demander à Gen » peut lancer ce skill. Le pont reste "
             "seul juge de ce qu'il accepte.")
    published = fields.Boolean(string="Publié par le pont", readonly=True, default=True,
                               help="Décoché : le pont ne le publie plus ; il ne se lance plus.")
    last_sync = fields.Datetime(string="Actualisé le", readonly=True)

    _sql_constraints = [("skill_unique", "unique(skill)", "Ce skill est déjà dans la liste.")]

    def _modeles(self):
        self.ensure_one()
        return [m.strip() for m in (self.modeles or "").split(",") if m.strip()]

    @api.model
    def action_actualiser(self):
        """Relit la liste que le pont publie pour ce locataire."""
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Actualiser les skills de Gen est réservé à la gestion."))
        pont = self.env["bf.ai.bridge"]
        pont.check_available(_("La liste des skills ne peut pas être actualisée."))
        locataire = pont.tenant()
        try:
            reponse = pont.call("/nfc-skills", {"tenant": locataire}, timeout=15)
        except Exception as exc:  # noqa: BLE001 — la cause est rendue à l'écran
            raise UserError(_("Le pont n'a pas rendu la liste des skills (%s).", type(exc).__name__))
        # 🔴 Une liste vide n'est pas une réponse : le pont répond aussi « aucun skill »
        # quand le locataire déclaré ne lui dit rien (paramètre bf_ai_bridge.tenant mal
        # posé). Écrire cette réponse-là déclasserait tous les skills d'un coup.
        if not (reponse.get("skills") or []):
            raise UserError(_(
                "Le pont ne publie aucun skill lançable par pastille pour le locataire "
                "« %s ». Rien n'a été changé : vérifiez le paramètre système "
                "bf_ai_bridge.tenant.", locataire))
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
    _description = "Demande à Gen par pastille"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    skill_id = fields.Many2one("bf.nfc.gen.skill", string="Skill", required=True, readonly=True,
                               ondelete="restrict")
    tag_id = fields.Many2one("bf.nfc.tag", string="Pastille", readonly=True, ondelete="set null")
    user_id = fields.Many2one("res.users", string="Demandé par", required=True, readonly=True)
    res_model = fields.Char(readonly=True, required=True)
    res_id = fields.Many2oneReference(model_field="res_model", readonly=True, required=True)
    target_name = fields.Char(string="Fiche", readonly=True)
    state = fields.Selection(ETATS, string="État", default="queued", required=True, readonly=True,
                             tracking=True, index=True)
    message = fields.Char(readonly=True)
    date_end = fields.Datetime(string="Finie le", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True, default=lambda self: self.env.company)

    def _en_cours(self):
        return self.state in ("queued", "running") and \
            self.create_date and fields.Datetime.now() - self.create_date < PEREMPTION

    def set_state(self, state, message=""):
        """L'état que le pont écrit en fin de passe. Réservé à la gestion et au système.

        ⚠️ Public parce que le pont l'appelle en XML-RPC. Ce qu'il ne fait pas : rien
        d'autre qu'un état, un message court et une activité à la personne.
        """
        if not (self.env.user.has_group("bf_nfc.group_nfc_manager")
                or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Seul le pont écrit l'état d'une demande à Gen."))
        if state not in dict(ETATS):
            raise UserError(_("État inconnu : %s", state))
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for demande in self.sudo():
            valeurs = {"state": state, "message": (message or "")[:250]}
            if state in ("done", "error", "busy"):
                valeurs["date_end"] = fields.Datetime.now()
            demande.write(valeurs)
            if state in ("done", "error"):
                demande.activity_schedule(
                    activity_type_id=todo.id if todo else False, user_id=demande.user_id.id,
                    summary=(_("Gen a terminé : %s", demande.skill_id.name) if state == "done"
                             else _("Gen n'a pas abouti : %s", demande.skill_id.name)),
                    note=_("%(fiche)s. %(message)s", fiche=demande.target_name,
                           message=(message or "")[:250]))
        return True

    @api.model
    def _cron_perimer(self):
        """Déclasse les demandes dont le pont n'a plus donné de nouvelles."""
        limite = fields.Datetime.now() - PEREMPTION
        oubliees = self.sudo().search([("state", "in", ("queued", "running")),
                                       ("create_date", "<", limite)])
        if oubliees:
            oubliees.set_state("error", _(
                "Sans nouvelle du pont depuis %s minutes. Regardez les journaux du service "
                "d'assistance IA ; rien ne dit si la passe a tourné.", int(PEREMPTION.total_seconds() // 60)))
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
            from odoo import api as _api, registry as _registry
            # ⚠️ La demande doit être EN BASE avant qu'un agent parte : un point de
            # reprise défait après l'enregistrement de l'appel l'aurait effacée, et le
            # pont lancerait une passe que rien ne suit.
            with _registry(db_name).cursor() as cr:
                if not _api.Environment(cr, uid, {})["bf.nfc.gen.request"].sudo().browse(demande_id).exists():
                    _logger.warning("Demande à Gen %s absente au moment d'appeler le pont : rien n'est lancé.",
                                    demande_id)
                    return
            try:
                reponse = transport.post(socket_path, "/nfc-skill", charge, 20)
                statut, message = reponse.get("status", "?"), reponse.get("message", "")
            except Exception as exc:  # noqa: BLE001 — la cause est rendue telle quelle
                statut, message = "error", _("Pont injoignable (%s).", type(exc).__name__)
            if statut == "ok":
                return
            with _registry(db_name).cursor() as cr:
                # ⚠️ En superutilisateur : la personne qui a tapé n'a pas forcément le
                # droit d'écrire un état (le geste peut être ouvert au-delà de la gestion).
                env = _api.Environment(cr, SUPERUSER_ID, {})
                demande = env["bf.nfc.gen.request"].browse(demande_id).exists()
                if demande:
                    demande.set_state("busy" if statut == "busy" else "error", message)

        self.env.cr.postcommit.add(lambda: threading.Thread(target=_envoyer, daemon=True).start())


class BfNfcTag(models.Model):
    _inherit = "bf.nfc.tag"

    gen_skill_id = fields.Many2one(
        "bf.nfc.gen.skill", string="Skill de Gen", ondelete="restrict", tracking=True,
        domain=[("allowed", "=", True), ("published", "=", True)])
    gen_request_ids = fields.One2many("bf.nfc.gen.request", "tag_id", string="Demandes à Gen")


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("gen_skill", "Demander à Gen")],
        ondelete={"gen_skill": "cascade"},
    )

    def _executer_gen_skill(self, tag, tap, params):
        self.ensure_one()
        self._exiger_une_personne(params)
        skill = tag.sudo().gen_skill_id
        if not skill:
            raise UserError(_("Cette pastille ne désigne aucun skill de Gen."))
        if not (skill.allowed and skill.published):
            raise UserError(_("« %s » n'est pas permis sur cette instance.", skill.name))
        if tag.res_model not in skill._modeles():
            raise UserError(_("« %(skill)s » ne vise pas ce type de fiche.", skill=skill.name))
        cible = tag._cible()
        if not cible.exists():
            raise UserError(_("La fiche de cette pastille n'existe plus."))
        pont = self.env["bf.ai.bridge"]
        if not pont.available():
            raise UserError(_("Gen n'est pas joignable pour le moment."))
        Demande = self.env["bf.nfc.gen.request"].sudo()
        deja = Demande.search([("skill_id", "=", skill.id), ("res_model", "=", tag.res_model),
                               ("res_id", "=", tag.res_id), ("state", "in", ("queued", "running"))],
                              order="create_date desc", limit=1)
        if deja and deja._en_cours():
            return {"titre": skill.name, "url": None, "choix": [],
                    "message": _("Gen y travaille déjà depuis %(heure)s, demandé par %(qui)s.",
                                 heure=self._heure(deja.create_date), qui=deja.user_id.name)}
        if params.get("choix") != CHOIX_LANCER:
            return {
                "titre": skill.name,
                "message": _("Sur « %s ». Gen travaillera seul ; une activité vous préviendra "
                             "quand il aura fini.", cible.display_name),
                "choix": [{"cle": CHOIX_LANCER, "libelle": _("Lancer"), "style": "principal",
                           "saisie": None}],
            }
        demande = Demande.create({
            "skill_id": skill.id, "tag_id": tag.id, "user_id": self.env.user.id,
            "res_model": tag.res_model, "res_id": tag.res_id,
            "target_name": cible.display_name, "company_id": tag.company_id.id,
        })
        demande._transmettre_apres_commit()
        return {"titre": skill.name, "url": None,
                "message": _("Demande transmise à Gen, sur « %s ».", cible.display_name)}
