"""Les appareils appariés d'une personne, tous modules confondus.

🔴 **Aucun modèle nouveau, et c'est la décision qui porte tout le reste.** Les
trois registres d'appareils existent déjà (`sms.archive.mobile.device`,
`bf.email.mobile.device`, `bf.otp.device`) et chacun est la source de vérité de
son module. Une table de plus, alimentée en parallèle, se serait désynchronisée
le jour où un module apparie sans passer par elle — c'est-à-dire tout de suite,
puisque chacun a sa route d'appariement.

⚠️ **Aucune dépendance vers les trois modules non plus.** Le module se pose chez
un locataire qui n'a que le courriel comme chez un qui a les trois, et il ne
force personne à installer ce dont il n'a pas besoin. Les sources absentes sont
simplement absentes de la page.
"""
import logging

from odoo import _, api, models
from odoo.tools import format_datetime

_logger = logging.getLogger(__name__)


class RegistreAppareils(models.AbstractModel):
    _name = "bf.device.registry"
    _description = "Registre des appareils appariés d'une personne"

    # Une source par module qui apparie des téléphones.
    #
    # ⚠️ `efface` reprend, champ pour champ, ce que le `/logout` du module
    # concerné remet à faux. Révoquer depuis cette page et se déconnecter depuis
    # le téléphone doivent laisser exactement le même enregistrement : deux
    # gestes qui prétendent la même chose et ne font pas la même chose sont
    # une trappe, pas une fonctionnalité.
    SOURCES = (
        {
            "model": "sms.archive.mobile.device",
            "libelle": "Messages",
            "efface": ("fcm_token", "push_endpoint"),
        },
        {
            "model": "bf.email.mobile.device",
            "libelle": "Courriel",
            "efface": ("push_endpoint",),
        },
        {
            "model": "bf.otp.device",
            "libelle": "Coffre de tokens",
            "efface": (),
        },
        # Les widgets du bureau de Blue Fox OS.
        #
        # ⚠️ `efface` nomme ici `token_hash`, contrairement aux trois sources
        # du haut. Ce n'est pas une inconséquence : c'est ce que fait aussi le
        # geste « Révoquer » du module lui-même, et les deux gestes doivent
        # laisser le même enregistrement. Les trois premières sources ne
        # l'effacent pas parce que leur propre `/logout` ne l'efface pas.
        {
            "model": "bf.desktop.device",
            "libelle": "Bureau",
            "efface": ("token_hash",),
        },
    )

    # 🔴 Ce qu'un jeton ouvre RÉELLEMENT, au-delà du module qui l'a émis.
    #
    # Un téléphone apparié par Messages ouvre aussi le téléphone, la dictée,
    # l'assistant, la veille du parc et l'agenda : ces modules acceptent le
    # MÊME jeton porteur, chacun par son `_DEVICE_MODELS` ou par un import
    # direct du `_authed` de Messages. La page n'affichait que le module
    # émetteur, ce qui laissait lire « révoquer, c'est couper les messages »
    # alors que c'est couper six surfaces. Relevé le 2026-09-03.
    #
    # ⚠️ La liste est vérifiée dans le code de chaque module, pas devinée :
    # `bf_softphone` importe le `_authed` de `bf_sms_archive`, donc il n'accepte
    # QUE le jeton Messages ; les quatre autres nomment les deux modèles.
    CAPACITES = (
        {"module": "bf_softphone", "libelle": "Téléphone",
         "sources": ("sms.archive.mobile.device",)},
        {"module": "bf_speech", "libelle": "Dictée",
         "sources": ("sms.archive.mobile.device", "bf.email.mobile.device")},
        {"module": "bf_claude_chat", "libelle": "Gen",
         "sources": ("sms.archive.mobile.device", "bf.email.mobile.device")},
        {"module": "bf_hosting_mobile", "libelle": "Hébergement",
         "sources": ("sms.archive.mobile.device", "bf.email.mobile.device")},
        {"module": "bf_calendar_mobile", "libelle": "Agenda et tâches",
         "sources": ("sms.archive.mobile.device", "bf.email.mobile.device")},
        # Le bloc-notes accepte les TROIS familles de jetons : un téléphone
        # apparié par Messages ou par Courriel l'ouvre déjà, et depuis 
        # un widget de bureau aussi. Vérifié dans son `_DEVICE_MODELS`.
        {"module": "bf_bloc_notes", "libelle": "Bloc-notes",
         "sources": ("sms.archive.mobile.device", "bf.email.mobile.device",
                     "bf.desktop.device")},
    )

    # ── Ce qui est là ────────────────────────────────────────────────────

    @api.model
    def _sources_presentes(self):
        """Les sources dont le modèle est chargé dans ce registre.

        ⚠️ Le contrôle porte sur `self.env`, pas sur `ir.module.module` : un
        module installé mais absent du registre (dépendance cassée, mise à jour
        en vol) ferait échouer la page entière sur un `KeyError` alors que la
        question posée est « puis-je lire ce modèle maintenant ».
        """
        return [s for s in self.SOURCES if s["model"] in self.env]

    # ── Lire ─────────────────────────────────────────────────────────────

    @api.model
    def _capacites_par_source(self):
        """Ce que chaque modèle d'appareil ouvre, sur CETTE instance.

        ⚠️ Le contrôle porte sur les modules installés : promettre « Agenda » à
        un locataire qui n'a pas `bf_calendar_mobile` ferait de la page une
        brochure, pas un inventaire.
        """
        installes = set(self.env["ir.module.module"].sudo().search([
            ("name", "in", [c["module"] for c in self.CAPACITES]),
            ("state", "=", "installed"),
        ]).mapped("name"))
        par_source = {}
        for capacite in self.CAPACITES:
            if capacite["module"] not in installes:
                continue
            for source in capacite["sources"]:
                par_source.setdefault(source, []).append(capacite["libelle"])
        return par_source

    @api.model
    def _mes_appareils(self, usager=None):
        """Les appareils actifs de [usager], ou de la personne connectée.

        🔴 **Le filtre sur la personne EST la sécurité de cette page**, et il ne
        s'appuie pas sur les règles d'enregistrement des trois modules : elles
        diffèrent d'un module à l'autre, et deux d'entre eux n'ont aucune vue,
        donc aucune règle éprouvée par un usage. La lecture se fait en `sudo`
        avec un domaine qui nomme la personne, jamais avec un identifiant venu
        de la requête.
        """
        usager = usager or self.env.user
        capacites = self._capacites_par_source()
        lignes = []
        for source in self._sources_presentes():
            appareils = self.env[source["model"]].sudo().search([
                ("user_id", "=", usager.id),
                ("active", "=", True),
            ])
            for appareil in appareils:
                vu = appareil.last_seen
                lignes.append({
                    "cle": "%s,%s" % (source["model"], appareil.id),
                    "modele": source["model"],
                    "module": source["libelle"],
                    # Les autres surfaces que ce même jeton ouvre. Vide quand
                    # aucune n'est installée, ce que la page sait afficher.
                    "capacites": capacites.get(source["model"], []),
                    "nom": appareil.name or _("Appareil"),
                    "plateforme": appareil.platform or "",
                    "version": getattr(appareil, "app_version", "") or "",
                    # ⚠️ Mis en forme ici, pas dans la page : le fuseau et la
                    # langue de la personne vivent dans l'environnement, et un
                    # gabarit de portail qui rend un objet datetime nu affiche
                    # l'heure UTC sans le dire.
                    "derniere_activite": format_datetime(self.env, vu) if vu else False,
                    "_vu": vu,
                })
        # La dernière activité d'abord, et les jamais vus à la fin : c'est
        # l'ordre dans lequel on cherche « lequel est mon vieux téléphone ».
        lignes.sort(key=lambda l: (l["_vu"] is not None and l["_vu"] is not False,
                                   l["_vu"] or 0), reverse=True)
        return lignes

    # ── Retirer ──────────────────────────────────────────────────────────

    @api.model
    def _revoquer(self, cle, usager=None):
        """Retire l'accès d'un appareil désigné par sa clé. Vrai s'il a bougé.

        ⚠️ La clé vient de la requête, donc elle ne prouve rien : le modèle est
        confronté à la liste des sources, et l'appartenance est vérifiée sur
        l'enregistrement lui-même. Sans ce second contrôle, la page révoquerait
        le téléphone de n'importe qui à partir d'un identifiant deviné.
        """
        usager = usager or self.env.user
        modele, _sep, brut = (cle or "").partition(",")
        source = next((s for s in self._sources_presentes()
                       if s["model"] == modele), None)
        if not source or not brut.isdigit():
            return False
        appareil = self.env[modele].sudo().browse(int(brut)).exists()
        if not appareil or appareil.user_id.id != usager.id:
            _logger.warning(
                "Mes appareils : révocation refusée, %s#%s n'appartient pas à "
                "l'usager %s", modele, brut, usager.id)
            return False
        return self._retirer(source, appareil)

    @api.model
    def _revoquer_tout(self, usager=None):
        """Retire l'accès de tous les appareils de la personne. Rend le compte."""
        usager = usager or self.env.user
        retires = 0
        for source in self._sources_presentes():
            appareils = self.env[source["model"]].sudo().search([
                ("user_id", "=", usager.id),
                ("active", "=", True),
            ])
            for appareil in appareils:
                if self._retirer(source, appareil):
                    retires += 1
        return retires

    @api.model
    def _retirer(self, source, appareil):
        """Le geste, identique à celui du `/logout` du module concerné.

        ⚠️ Le token porteur n'est PAS effacé, exactement comme dans les trois
        modules : `_resolve` ne rend que les appareils actifs, donc le token
        n'ouvre plus rien. L'effacer changerait le comportement d'une route que
        ce module n'a pas écrite, pour un gain nul.
        """
        # 🔴 Sonner AVANT d'écrire, et l'ordre n'est pas négociable : la
        # révocation efface `push_endpoint` (il est dans `efface` pour deux des
        # trois sources), et un appareil sans endpoint ne se réveille plus. Le
        # réveil doit donc partir tant que le chemin existe encore.
        #
        # ⚠️ Un réveil parti AVANT que l'appareil soit inactif fait rappeler un
        # téléphone qui reçoit... un 200, puisqu'il est encore valide à cette
        # seconde-là. C'est voulu et sans conséquence : le téléphone rappellera
        # de nouveau (il vient d'être sollicité), et surtout `_resolve` le
        # refusera au premier appel qui suit l'écriture. Le réveil hâte la
        # coupure, il ne la remplace pas.
        self._sonner(appareil)
        valeurs = {"active": False}
        for champ in source["efface"]:
            if champ in appareil._fields:
                valeurs[champ] = False
        appareil.sudo().write(valeurs)
        _logger.info("Mes appareils : accès retiré à %s#%s (%s)",
                     appareil._name, appareil.id, appareil.name or "")
        return True

    @api.model
    def _sonner(self, appareil):
        """Demande au module d'origine de réveiller CET appareil, s'il sait.

        ⚠️ Par canard, pas par dépendance, et c'est la même décision que tout le
        reste de ce module : il regarde des registres qu'il ne possède pas et
        n'impose aucune installation. Un module sans transport de push (le
        coffre de tokens, le chronomètre, les pastilles) n'a simplement pas de
        sonnette, et son appareil sera coupé au 401 de son prochain appel.

        Ne lève jamais : révoquer un appareil doit marcher même quand la
        sonnette est cassée. C'est l'écriture qui coupe, pas le réveil.
        """
        transport = {
            "sms.archive.mobile.device": "sms.archive.unifiedpush",
            "bf.email.mobile.device": "bf.email.unifiedpush",
        }.get(appareil._name)
        if not transport or transport not in self.env:
            return False
        try:
            self.env[transport].sudo()._reveiller(appareil.sudo())
            return True
        except Exception:  # noqa: BLE001
            _logger.warning("Mes appareils : réveil raté pour %s#%s.",
                            appareil._name, appareil.id, exc_info=True)
            return False
