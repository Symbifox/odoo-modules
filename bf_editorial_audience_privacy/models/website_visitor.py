"""La durée de conservation de la chaîne d'agent, et la trace de son effacement.

Deux chemins effacent la chaîne d'agent utilisateur d'un visiteur :

* le ménage quotidien de ``bf_editorial_audience`` (``_cron_forget_agents``),
  qui efface la chaîne et garde le visiteur, son verdict et sa famille ;
* la purge native d'Odoo (``_cron_unlink_old_visitors``), qui supprime le
  visiteur inactif non rattaché à un contact, chaîne comprise (website_crm et
  website_event en retirent ceux qui sont liés à un prospect ou à une
  inscription).

Les deux s'inscrivent au registre de destruction, par lot et par société (celle
du site du visiteur). Le premier prend sa durée dans la politique de
conservation de la finalité, qui devient la seule durée APPLIQUÉE tant qu'elle
est active.

⚠️ Les deux chemins ne traitent pas un échec d'inscription de la même façon :

* ménage : BLOQUANT. L'effacement et son inscription vont ensemble ; si
  l'inscription lève, la chaîne revient et rien n'est effacé ce jour-là. Si
  l'échec dure, le ménage s'arrête et Odoo finit par désactiver son cron
  (l'administrateur en est averti) : les anonymes partent quand même avec la
  purge, mais les chaînes des visiteurs rattachés sont alors gardées au-delà
  de la durée.
* purge native : NON bloquant. Si l'inscription lève, la suppression continue
  et l'échec est journalisé. Une purge arrêtée chaque nuit sur le même lot
  finirait par faire désactiver son cron par Odoo (échecs répétés), et les
  visiteurs s'accumuleraient alors sans borne : pire, pour la vie privée,
  qu'une entrée de registre manquante.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

PURPOSE_XMLID = "bf_editorial_audience_privacy.purpose_editorial_audience"
CALENDAR_XMLID = "bf_editorial_audience_privacy.retention_editorial_audience_agent"

# Drapeau de contexte posé par la purge native : seul ce chemin-là est une
# destruction au titre de la conservation. Un ``unlink`` d'ailleurs (fusion de
# deux visiteurs quand une personne se connecte, suppression à la main) n'en
# est pas une, et ne doit rien certifier.
#
# 🔴 Le drapeau seul ne suffit pas : n'importe quel appelant RPC peut forger une
# clé de contexte, et un concepteur du site qui supprime un visiteur avec elle
# graverait au registre immuable une fausse « purge native ». ``unlink`` ne le
# lit donc que sous superutilisateur (``env.su``), ce que la surcharge du cron
# garantit en appelant la purge sur ``self.sudo()``.
CTX_PURGE = "bf_ua_privacy_purge"

# Fuseau de la date écrite dans le nom de l'entrée. Le registre relève de la
# Loi 25 : la date est celle du Québec, pas l'UTC de l'usager root du cron ni
# le fuseau de la société principale, qui peut être à l'autre bout du monde.
TZ_REGISTRE = "America/Toronto"


def _purpose(env):
    return env.ref(PURPOSE_XMLID, raise_if_not_found=False)


class PrivacyRetentionPolicy(models.Model):
    _inherit = "privacy.retention.policy"

    @api.constrains("retention_days", "purpose_id")
    def _check_bf_ua_retention_days(self):
        """Une durée nulle ou négative ne se déclare pas pour cette finalité.

        Le ménage la lirait comme « ne rien effacer » (c'est la convention du
        module d'audience), et le registre des traitements afficherait
        « 0 jour » pour une chaîne en réalité conservée sans borne.
        """
        purpose = _purpose(self.env)
        if not purpose:
            return
        for policy in self:
            if policy.purpose_id == purpose and policy.retention_days < 1:
                raise ValidationError(_(
                    "La durée de conservation de l'agent utilisateur doit être "
                    "d'au moins un jour. Pour cesser d'effacer, archivez la "
                    "politique : le ménage revient alors au paramètre du "
                    "module d'audience (bf_editorial_audience.ua_retention_days), "
                    "et le registre ne déclare plus de durée qui ne s'applique "
                    "pas. Reprenez alors aussi le résumé public de la finalité, "
                    "qui promet encore un effacement."
                ))


class WebsiteVisitor(models.Model):
    _inherit = "website.visitor"

    # --- la durée -------------------------------------------------------
    @api.model
    def _bf_ua_retention_policy(self):
        """La politique active de la finalité, ou un ensemble vide.

        S'il y en a plusieurs (une par société), la plus courte gagne : les
        visiteurs ne portent pas de société propre, et appliquer la plus
        longue ferait conserver au-delà de ce qu'une société a déclaré.
        """
        Policy = self.env["privacy.retention.policy"].sudo()
        purpose = _purpose(self.env)
        if not purpose:
            return Policy.browse()
        return Policy.search(
            [("purpose_id", "=", purpose.id), ("active", "=", True)],
            order="retention_days asc, id asc", limit=1,
        )

    # --- la société -----------------------------------------------------
    def _bf_ua_comptes(self):
        """``{société: (total, robots, rattachés)}`` des visiteurs de ``self``.

        La société est celle du site du visiteur, repli sur la société
        principale : PAS ``env.company``, qui est celle de l'usager du cron et
        n'a rien à voir avec le site dont les visiteurs viennent. Le registre
        chaîne ses empreintes par société : une entrée portée à la mauvaise
        société entrerait dans la mauvaise chaîne.
        """
        principale = self.env.ref("base.main_company", raise_if_not_found=False)
        comptes = {}
        for visiteur in self.sudo():
            societe = visiteur.website_id.company_id or principale or self.env.company
            total, robots, rattaches = comptes.get(societe, (0, 0, 0))
            comptes[societe] = (
                total + 1,
                robots + bool(visiteur.is_bot),
                rattaches + bool(visiteur.partner_id),
            )
        return comptes

    # --- chemin 1 : le ménage du module d'audience -----------------------
    @api.model
    def _cron_forget_agents(self):
        """Effacer les chaînes échues, d'après la politique, et l'inscrire.

        Sans politique active, le module d'audience reprend la main avec son
        paramètre, et rien n'est inscrit : aucune durée n'est alors déclarée
        au registre des traitements.
        """
        policy = self._bf_ua_retention_policy()
        if not policy:
            return super()._cron_forget_agents()

        limite = fields.Datetime.subtract(
            fields.Datetime.now(), days=policy.retention_days,
        )
        vieux = self.sudo().search([
            ("user_agent", "!=", False),
            ("last_connection_datetime", "<", limite),
        ])
        if not vieux:
            return 0
        # Compter AVANT d'effacer : après, rien ne distingue plus ces lignes.
        comptes = vieux._bf_ua_comptes()
        total = len(vieux)
        robots = sum(c[1] for c in comptes.values())
        vieux.write({"user_agent": False})
        # 🔴 BLOQUANT, et voulu : même transaction que l'effacement, sans point
        # de reprise. Si l'inscription lève, la chaîne revient et rien n'est
        # effacé ce jour-là. Si l'échec dure, Odoo finit par désactiver le cron
        # du ménage (échecs répétés ; l'administrateur est averti) : les
        # anonymes partent quand même avec la purge native, mais les chaînes
        # des visiteurs rattachés sont alors gardées au-delà de la durée. Un
        # effacement non inscrit serait pire (la purge native, elle, ne bloque
        # pas : voir ``unlink``).
        for societe, (n, n_robots, n_rattaches) in comptes.items():
            self._bf_ua_register(
                total=n, robots=n_robots, rattaches=n_rattaches,
                scope="partial", policy=policy, company=societe,
            )
        _logger.info(
            "bf_editorial_audience_privacy : %s chaîne(s) d'agent effacée(s) "
            "(%s jours, dont %s robot(s) déclaré(s)).",
            total, policy.retention_days, robots,
        )
        return total

    # --- chemin 2 : la purge native d'Odoo -------------------------------
    def _cron_unlink_old_visitors(self, batch_size=1000, limit=None):
        # ``sudo()`` : la purge native fait ``self.env['website.visitor']``
        # puis ``browse`` ; chaque lot passé à ``unlink`` hérite donc de cet
        # environnement, et porte ``su`` même si le cron changeait d'usager.
        return super(
            WebsiteVisitor, self.sudo().with_context(**{CTX_PURGE: True})
        )._cron_unlink_old_visitors(batch_size=batch_size, limit=limit)

    def unlink(self):
        """Inscrire les chaînes que la purge native emporte avec le visiteur.

        ⚠️ La purge valide par lots de mille (``cr.commit()`` après chaque
        lot) : l'entrée s'écrit DANS l'appel de chaque lot, donc dans la même
        transaction que sa suppression.

        ⚠️ ``exists()`` d'abord : la purge cherche tous les ids puis les
        supprime lot par lot, en validant entre deux. Un visiteur fusionné
        (connexion) entre la recherche et son lot a disparu ; le lire lèverait
        MissingError et arrêterait la purge. Seuls les existants sont comptés
        et passés à la suppression native.

        🔴 Le drapeau n'est lu que sous superutilisateur : voir ``CTX_PURGE``.
        """
        if not (self.env.context.get(CTX_PURGE) and self.env.su):
            # Hors purge, ou drapeau posé par un appelant qui n'est pas
            # superutilisateur (contexte forgé en RPC) : suppression
            # ordinaire, rien n'est inscrit.
            return super().unlink()
        existants = self.exists()
        comptes = existants.sudo().filtered("user_agent")._bf_ua_comptes()
        res = super(WebsiteVisitor, existants).unlink()
        policy = self._bf_ua_retention_policy()
        for societe, (n, n_robots, n_rattaches) in comptes.items():
            # 🔴 NON bloquant, et voulu (l'inverse du ménage) : la suppression
            # est faite, l'inscription ne doit pas la défaire. Si elle levait,
            # la purge s'arrêterait sur ce lot chaque nuit, Odoo finirait par
            # désactiver son cron, et les visiteurs s'accumuleraient sans
            # borne. Le point de reprise isole l'échec (une erreur SQL
            # laisserait sinon la transaction inutilisable pour la suite du
            # lot) ; l'erreur est journalisée pour qu'on rattrape l'entrée.
            try:
                with self.env.cr.savepoint():
                    self._bf_ua_register(
                        total=n, robots=n_robots, rattaches=n_rattaches,
                        scope="full", policy=policy, company=societe,
                    )
            except Exception:
                _logger.error(
                    "bf_editorial_audience_privacy : %s chaîne(s) d'agent "
                    "emportée(s) par la purge native SANS inscription au "
                    "registre de destruction (société %s, %s robot(s), %s "
                    "rattaché(s)). La suppression est faite ; l'entrée de lot "
                    "est à rattraper à la main.",
                    n, societe.display_name, n_robots, n_rattaches,
                    exc_info=True,
                )
        return res

    # --- le registre -----------------------------------------------------
    @api.model
    def _bf_ua_register(self, total, robots, rattaches, scope, policy, company):
        """Une entrée de lot au registre immuable.

        Pas de ``res_id`` : l'entrée vise un lot, pas un visiteur. Nommer les
        visiteurs un par un recopierait au registre, sans borne cette fois,
        l'identifiant qu'on vient d'effacer.
        """
        Register = self.env["privacy.destruction.register"].sudo()
        aujourdhui = fields.Date.to_string(
            fields.Date.context_today(self.with_context(tz=TZ_REGISTRE)))
        if scope == "partial":
            nom = _("Visiteurs du site — chaînes d'agent effacées le %s", aujourdhui)
            quoi = _(
                "%(total)s chaîne(s) d'agent utilisateur effacée(s) par le "
                "ménage quotidien de l'atelier éditorial, sur des visiteurs "
                "inactifs depuis plus de %(jours)s jours.\n"
                "Le verdict (robot déclaré) et la famille d'agent tirés de la "
                "chaîne sont conservés : à eux seuls, ils ne permettent pas de "
                "reconnaître un appareil. Le visiteur, lui, n'est PAS "
                "supprimé : il garde son témoin de navigateur (visitor_uuid), "
                "son pays, sa langue, son fuseau horaire et ses pages vues "
                "jusqu'à la purge native d'Odoo, ou sans borne tant qu'il est "
                "rattaché à un contact, à un prospect ou à une inscription à "
                "un événement.",
                total=total, jours=policy.retention_days,
            )
        else:
            nom = _("Visiteurs du site — purge native du %s", aujourdhui)
            quoi = _(
                "%(total)s visiteur(s) anonyme(s) inactif(s) supprimé(s) par la "
                "purge native d'Odoo, chaîne d'agent utilisateur comprise : "
                "ils la portaient encore au moment de la purge.",
                total=total,
            )
        quoi += _(
            "\nDont %(robots)s robot(s) déclaré(s), et %(rattaches)s visiteur(s) "
            "rattaché(s) à un contact.",
            robots=robots, rattaches=rattaches,
        )
        vals = {
            "destruction_date": fields.Datetime.now(),
            "destroyed_by_id": self.env.user.id,
            # Exigé par le registre. La règle AUD-001 ne demande aucune
            # approbation : celui qui « approuve » est celui qui exécute, comme
            # dans les autres ponts dont la purge tourne seule.
            "approved_by_id": self.env.user.id,
            "res_model": self._name,
            "res_name": nom,
            "document_description": quoi,
            "pi_categories": _(
                "Identifiant d'appareil : chaîne d'agent utilisateur (navigateur, "
                "version, système d'exploitation, parfois le modèle de "
                "l'appareil)."
            ),
            # Un visiteur est un témoin de navigateur, pas une personne, et un
            # robot n'est personne : le nombre de personnes concernées est AU
            # PLUS celui des visiteurs qui ne se déclarent pas robots.
            "subject_count": total - robots,
            # `delete` et non `secure_wipe` : la valeur d'avant survit dans les
            # blocs de la table, le journal d'écriture et les sauvegardes.
            "destruction_method": "delete",
            "destruction_scope": scope,
            "legal_basis": _(
                "Art. 23 LPRPSP : le renseignement personnel est détruit "
                "lorsque la fin pour laquelle il a été recueilli est accomplie. "
                "La fin est le classement de l'agent (lecteur ou robot) dans "
                "les mesures d'audience, et son reclassement quand une "
                "signature de robot s'ajoute."
            ),
            "company_id": company.id,
        }
        if scope == "partial":
            vals["res_field"] = "user_agent"
        calendar = self.env.ref(CALENDAR_XMLID, raise_if_not_found=False)
        if calendar:
            vals["retention_calendar_id"] = calendar.id
        return Register.create(vals)
