"""Le journal des incarnations.

Aucune ACL n'y donne l'écriture, à personne, administrateurs compris : le
code écrit en ``sudo()`` et lui seul. L'OCA ``impersonate_login`` donnait
l'écriture à tout interne, qui pouvait donc réécrire qui avait incarné qui.

Le journal se lit à deux hauteurs :
un **résumé** groupé par fiche et par geste, qui dit ce qui a été fait en
quelques phrases, et le **détail** ligne par ligne, qui reste la preuve. Le
résumé sert au formulaire, au rapport PDF et à l'avis de fin : une seule
source, trois rendus.
"""
import json
import logging
from datetime import timedelta

import pytz
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import config, format_date, format_time

from .. import impersonation as imp

_logger = logging.getLogger(__name__)

NOTIFY_PARAM = "bf_impersonate.notify"
NOTIFY_NEVER = "never"
NOTIFY_START = "start"
NOTIFY_START_END = "start_end"
# Le défaut livré avise à la fin AUSSI (18.0.1.1.0) : sans l'avis de fin, le
# résumé de ce qui a été fait n'atteint jamais la personne, et l'avis de début
# ne peut rien promettre (il part avant le premier geste).
NOTIFY_DEFAULT = NOTIFY_START_END

# Combien de fiches nommées par phrase du résumé, et combien de phrases.
LABELS_PAR_PHRASE = 3
PHRASES_MAX_COURRIEL = 12


class BfImpersonateSession(models.Model):
    _name = "bf.impersonate.session"
    _description = "Impersonation session"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    user_id = fields.Many2one(
        "res.users", string="Impersonated by", required=True, readonly=True,
        index=True, ondelete="restrict")
    target_user_id = fields.Many2one(
        "res.users", string="Person seen", required=True, readonly=True,
        index=True, ondelete="restrict")
    reason = fields.Text(required=True, readonly=True)
    mode = fields.Selection(
        [("read", "Read only"), ("write", "Read and write")],
        required=True, readonly=True)
    date_start = fields.Datetime(
        string="Started at", required=True, readonly=True,
        default=fields.Datetime.now)
    expires_at = fields.Datetime(string="Planned end", required=True, readonly=True)
    date_end = fields.Datetime(string="Actual end", readonly=True)
    end_reason = fields.Selection(
        [
            ("manual", "Returned to own account"),
            ("expired", "Time limit reached"),
            ("forced", "Ended by an administrator"),
            ("logout", "Logged out"),
        ],
        readonly=True)
    state = fields.Selection(
        [("open", "In progress"), ("closed", "Ended")],
        compute="_compute_state", store=True)
    ip_address = fields.Char(string="IP address", readonly=True, groups="base.group_system")
    line_ids = fields.One2many(
        "bf.impersonate.session.line", "session_id", string="Actions",
        readonly=True)
    line_count = fields.Integer(compute="_compute_line_count", string="Actions taken")
    duration_minutes = fields.Integer(compute="_compute_duration", string="Minutes")
    summary_html = fields.Html(
        # `sanitize=False` : le contenu est bâti par le serveur en Markup, et
        # chaque valeur interpolée (nom de fiche, motif) y est échappée. Assainir
        # en plus ne protégerait de rien et réécrirait le balisage.
        string="What was done", compute="_compute_summary_html", sanitize=False,
        help="The journal, grouped by record and by action: what was changed, "
             "and what was looked at.")

    @api.depends("date_end")
    def _compute_state(self):
        for journal in self:
            journal.state = "closed" if journal.date_end else "open"

    @api.depends("line_ids", "line_ids.kind")
    def _compute_line_count(self):
        """Les GESTES, pas les consultations : « Actions : 42 » sur une séance
        en lecture seule qui n'a rien changé se lirait comme 42 changements
        (relecture adverse du 2026-11-08)."""
        for journal in self:
            journal.line_count = len(
                journal.line_ids.filtered(lambda line: line.kind != "read"))

    @api.depends("date_start", "date_end", "expires_at")
    def _compute_duration(self):
        now = fields.Datetime.now()
        for journal in self:
            end = journal.date_end or min(now, journal.expires_at or now)
            start = journal.date_start or end
            journal.duration_minutes = max(0, int((end - start).total_seconds() // 60))

    @api.depends("line_ids", "line_ids.writes_json", "line_ids.read_count")
    def _compute_summary_html(self):
        for journal in self:
            journal.summary_html = journal._bf_summary_html()

    @api.depends("user_id", "target_user_id", "date_start")
    def _compute_display_name(self):
        for journal in self:
            journal.display_name = _(
                "%(origin)s as %(target)s",
                origin=journal.user_id.name or "",
                target=journal.target_user_id.name or "",
            )

    # ------------------------------------------------------------------
    # Ouverture et fermeture, appelées par le code seulement

    @api.model
    def _bf_open(self, origin, target, reason, mode, minutes, ip_address=None):
        now = fields.Datetime.now()
        journal = self.sudo().with_context(
            mail_create_nosubscribe=True, mail_create_nolog=True,
            tracking_disable=True,
        ).create({
            "user_id": origin.id,
            "target_user_id": target.id,
            "reason": reason,
            "mode": mode,
            "date_start": now,
            "expires_at": now + timedelta(minutes=minutes),
            "ip_address": ip_address,
        })
        journal._bf_notify("start")
        return journal

    def _bf_close(self, reason, when=None, notify=True):
        """Fermer les entrées encore ouvertes ; rend celles qu'on vient de fermer."""
        closed = self.sudo().filtered(lambda j: not j.date_end)
        for journal in closed:
            journal.write({
                "date_end": when or fields.Datetime.now(),
                "end_reason": reason,
            })
            if notify:
                journal._bf_notify("end")
        return closed

    def action_force_end(self):
        """Mettre fin à une incarnation en cours : la session de l'incarnateur
        le voit à sa requête suivante et revient à son compte."""
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator can end someone else's impersonation."))
        self.check_access("read")
        # Une séance dont la durée est déjà passée a pris fin à son échéance, pas
        # au clic : la fermer « maintenant » inscrivait 54 minutes au journal pour
        # une séance bornée à 10. On la ferme comme la tâche planifiée l'aurait fait.
        maintenant = fields.Datetime.now()
        echues = self.sudo().filtered(
            lambda j: not j.date_end and j.expires_at and j.expires_at <= maintenant)
        for journal in echues:
            journal._bf_close("expired", when=journal.expires_at)
        (self - echues)._bf_close("forced")
        return True

    @api.model
    def _cron_close_expired(self):
        """Fermer les entrées dont la durée est passée : l'incarnateur est revenu
        à son compte à sa première requête après l'échéance, sans écrire en base
        (le curseur y est souvent en lecture seule), ou s'est déconnecté."""
        now = fields.Datetime.now()
        for journal in self.sudo().search([
            ("date_end", "=", False), ("expires_at", "<=", now),
        ]):
            journal._bf_close("expired", when=journal.expires_at)

    # ------------------------------------------------------------------
    # Le relevé des consultations, en lecture seule

    @api.model
    def _bf_note_read(self, journal_id, model):
        """Consigner qu'une application a été consultée.

        Une seule ligne par séance et par modèle, le compteur porté par un
        ``ON CONFLICT`` (index unique partiel posé par ``init``) : une seule
        instruction par requête, sans lecture préalable et sans course entre
        deux agents.

        🔴 **Le modèle se vérifie au registre.** Il vient des paramètres de la
        requête (``thread_model``), donc de l'incarnateur : sans ce contrôle, une
        adresse tapée à la main (`/odoo/contacts?thread_model=Rien d'anormal`)
        inscrit la ligne de son choix au journal qui sert à le surveiller, et
        une valeur non textuelle fait lever la requête. Relecture adverse du
        2026-11-08, constat B1.

        🔴 **Toujours un curseur à part en production, même quand celui de la
        requête écrit.** L'``INSERT`` prend un verrou de ligne que la requête ne
        relâcherait qu'à sa validation : une requête lente (IMAP, appel externe,
        tous deux possibles sous incarnation) ferait attendre les suivantes de la
        même séance sur le même modèle. Validé tout de suite, le verrou dure une
        milliseconde. Et un échec sur un curseur à part n'avorte pas la
        transaction de la requête : « échouer ouvert » devient vrai, alors qu'une
        instruction en échec sur le curseur de la requête empoisonne tout ce qui
        suit (constats B2 et B3 de la même relecture). ⚠️ Aucun locataire n'a de
        réplica, donc ``cr.readonly`` est toujours faux : s'en servir pour
        choisir le chemin rendait l'autre mort.

        🔴 **Sauf sous essai** : un curseur à part n'y marche pas, pour deux
        raisons différentes. Dans un essai HTTP, ``Registry.cursor()`` rend un
        ``TestCursor`` qui prend le verrou du curseur d'essai que la requête
        tient déjà, et la requête se FIGE. Dans un essai de transaction, il rend
        une vraie connexion, qui ne voit pas la séance que l'essai vient de
        créer et n'a pas encore validée : clé étrangère violée. L'essai écrit
        donc sur le curseur de la requête, sous un point de sauvegarde pour ne
        pas empoisonner sa transaction.

        ⚠️ Et le mode essai ne se lit PAS à ``registry.in_test_mode()`` : seul un
        essai HTTP y entre (il doit partager son curseur avec le travailleur),
        un ``TransactionCase`` non. Le drapeau qui vaut pour les deux est celui
        du serveur, ``config["test_enable"]``.
        """
        if not journal_id or not isinstance(model, str):
            # Un paramètre de requête n'est pas forcément une chaîne : un
            # `thread_model` en liste ou en dictionnaire ferait lever
            # l'appartenance au registre, donc une pile par requête, à la main
            # de l'incarnateur.
            return
        try:
            registre = self.env.registry
            if model not in registre or imp.read_is_noise(model):
                return
            if config["test_enable"] or registre.in_test_mode():
                with self.env.cr.savepoint(flush=False):
                    self._bf_note_read_sql(self.env.cr, journal_id, model,
                                           transaction_neuve=False)
                return
            # Le registre est déjà en main : `Registry(dbname)` reprendrait le
            # verrou global et une recherche de cache pour le même objet.
            with registre.cursor() as propre:
                self._bf_note_read_sql(propre, journal_id, model)
        except Exception:  # noqa: BLE001 - échouer ouvert, voir la docstring
            _logger.exception(
                "bf_impersonate : consultation de %s non consignée", model)

    @api.model
    def _bf_note_read_sql(self, cr, journal_id, model, transaction_neuve=True):
        # 🔴 Odoo met TOUTE connexion en REPEATABLE READ, le curseur à part
        # compris. Dans ce niveau, un `ON CONFLICT DO UPDATE` dont la ligne a
        # été touchée par une transaction validée après la prise d'instantané
        # est ANNULÉ (40001), il n'attend pas et ne relit pas comme en READ
        # COMMITTED. Or un seul écran du client web tire plusieurs appels en
        # parallèle sur le même modèle : le premier crée la ligne, les autres
        # tomberaient, le compteur sous-compterait et chaque requête
        # journaliserait une pile. Ce curseur-là ne fait QUE cette instruction,
        # donc baisser son niveau ne coûte rien. Relecture adverse du 2026-11-08.
        # 
        #
        # ⚠️ Seulement sur une transaction NEUVE : le niveau d'isolation ne se
        # change pas en cours de transaction, et le curseur de la requête (le
        # chemin des essais) en a déjà une en vol.
        if transaction_neuve:
            cr.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cr.execute("""
            INSERT INTO bf_impersonate_session_line
                (session_id, kind, model, read_count, date,
                 create_uid, write_uid, create_date, write_date)
            VALUES (%s, 'read', %s, 1, (now() AT TIME ZONE 'UTC'),
                    1, 1, (now() AT TIME ZONE 'UTC'), (now() AT TIME ZONE 'UTC'))
            ON CONFLICT (session_id, model) WHERE kind = 'read'
            DO UPDATE SET read_count = bf_impersonate_session_line.read_count + 1,
                          write_date = (now() AT TIME ZONE 'UTC')
        """, (journal_id, model))

    # ------------------------------------------------------------------
    # Le résumé : une source, trois rendus (formulaire, PDF, avis de fin)

    def _bf_summary_items(self):
        """Ce qui s'est passé, groupé par fiche et par geste.

        Rend deux listes de dictionnaires, ``{"model", "verb", "count",
        "labels", "at_least"}`` : les gestes qui ont changé quelque chose, puis
        les applications consultées. Le groupe se bâtit sur les ÉCRITURES
        réelles de chaque ligne (``writes_json``), pas sur le nom de la méthode
        appelée : un formulaire enregistré pour la première fois passe par
        ``web_save`` mais a bel et bien CRÉÉ la fiche, et la personne doit lire
        « créée ».
        """
        self.ensure_one()
        verbes = self._bf_verbs()
        ecrits, lus = {}, {}
        for line in self.line_ids:
            if line.kind == "read":
                groupe = lus.setdefault(line.model, {"count": 0})
                groupe["count"] += max(1, line.read_count)
                continue
            for write in line._bf_writes() or line._bf_writes_from_call():
                # La plomberie est écartée à l'écriture depuis la 18.0.1.1.0,
                # mais les lignes d'avant la portent : le résumé la refuse aussi,
                # sinon une séance relue annonce « a créé 20 fiches « Bus de
                # communication » », ce que personne n'a fait.
                if write.get("model") in imp.NOISE_MODELS:
                    continue
                cle = (write.get("model"), write.get("op"))
                groupe = ecrits.setdefault(
                    cle, {"ids": set(), "labels": [], "coupe": False,
                          "n": 0, "ecritures": 0})
                ids = [i for i in (write.get("ids") or [])]
                groupe["ids"].update(ids)
                groupe["coupe"] = groupe["coupe"] or bool(write.get("trunc"))
                groupe["n"] = max(groupe["n"], write.get("n") or 0)
                groupe["ecritures"] += write.get("w") or 1
                for label in write.get("labels") or []:
                    if label and label not in groupe["labels"]:
                        groupe["labels"].append(label)
        items = [
            {
                "model": self._bf_model_label(model),
                "verb": verbes.get(op) or op or "",
                "count": self._bf_count(groupe)[0],
                # Jamais plus de noms que de fiches : une même fiche touchée
                # deux fois sous deux noms en laisse deux au relevé, et
                # « 1 fiche (ancien nom, nouveau nom) » ne se lit pas. Le
                # premier est celui qu'elle portait au premier geste.
                "labels": groupe["labels"][:min(LABELS_PAR_PHRASE,
                                                len(groupe["ids"]) or 1)],
                "at_least": self._bf_count(groupe)[1],
            }
            for (model, op), groupe in ecrits.items()
        ]
        items.sort(key=lambda item: (-item["count"], item["model"]))
        vus = [
            {
                "model": self._bf_model_label(model),
                "verb": verbes["_read"],
                "count": groupe["count"],
                "labels": [],
                "at_least": False,
                "times": True,
            }
            for model, groupe in lus.items()
        ]
        vus.sort(key=lambda item: (-item["count"], item["model"]))
        return items, vus

    def _bf_count(self, groupe):
        """``(combien, est_un_plancher)`` pour un groupe du résumé.

        Trois cas, et le troisième seul est un plancher :

        * aucune liste d'identifiants coupée : le compte est le nombre de fiches
          DISTINCTES, exact ;
        * une seule écriture, même coupée : son propre compte est exact, parce
          que le relevé garde le nombre vrai de l'écriture à côté de sa liste ;
        * plusieurs écritures dont au moins une coupée : les listes incomplètes
          peuvent se recouper, donc rien n'est exact. On annonce le plus grand
          des deux chiffres connus, et on dit que c'est un minimum.

        Annoncer plus de fiches qu'il n'y en a eu serait pire que d'en annoncer
        moins : le journal porte une valeur de preuve (INC-001).
        """
        distinctes = len(groupe["ids"])
        if not groupe["coupe"]:
            return (distinctes or 1), False
        if groupe["ecritures"] <= 1:
            return (groupe["n"] or distinctes or 1), False
        return max(distinctes, groupe["n"]) or 1, True

    def _bf_verbs(self):
        """Le geste en mots. Table courte et volontairement incomplète : le repli
        est le nom de la méthode, qui vaut mieux qu'une devinette.

        Chaque entrée est un littéral dans ``env._()`` : un msgid que le ``.pot``
        du module ne porte pas est JETÉ en silence au chargement, et une table
        de chaînes nues n'est pas exportable.

        En français, ces verbes se traduisent avec leur auxiliaire (« a ouvert »),
        donc le participe reste INVARIABLE : il n'a pas à s'accorder avec le nom
        du modèle, dont le code ne connaît pas le genre.
        """
        env = self.env
        return {
            "create": env._("created"),
            "write": env._("changed"),
            "web_save": env._("changed"),
            "unlink": env._("deleted"),
            "message_post": env._("posted a note on"),
            "_read": env._("viewed"),
            # Boutons maison dont le nom technique ne dit rien à la personne.
            "action_archive": env._("archived"),
            "action_unarchive": env._("unarchived"),
            "toggle_active": env._("archived or restored"),
            "activity_schedule": env._("planned an activity on"),
            "action_feedback": env._("closed an activity on"),
            "inbox_get_body": env._("opened"),
            "inbox_run_action": env._("filed"),
            "held_decide": env._("decided on a held email of"),
            "action_sync_now": env._("synchronised"),
            "action_test_connection": env._("tested the connection of"),
            "action_refresh_folders": env._("refreshed the folders of"),
        }

    def _bf_model_label(self, model):
        """Le nom de l'application ou de la fiche, dans la langue de qui lit."""
        if not model:
            return ""
        described = self.env["ir.model"]._get(model)
        return described.name if described else model

    def _bf_phrase(self, item):
        """« a ouvert 14 fiches « Courriel » (Re: facture, et 13 autres) ».

        Le verbe vient en tête parce que le français le fait suivre d'un
        auxiliaire : après « a », le participe ne s'accorde pas avec son objet,
        et la phrase tient quel que soit le genre du nom du modèle. Les guillemets
        autour de ce nom appartiennent au gabarit, donc à la langue.
        """
        env = self.env
        if item.get("times"):
            combien = (
                env._("1 time") if item["count"] == 1
                else env._("%s times", item["count"]))
            phrase = env._(
                "%(verb)s %(model)s, %(count)s",
                verb=item["verb"], model=item["model"], count=combien)
        else:
            combien = (
                env._("1 record") if item["count"] == 1
                else env._("%s records", item["count"]))
            phrase = env._(
                "%(verb)s %(count)s of %(model)s",
                verb=item["verb"], count=combien, model=item["model"])
            if item["at_least"]:
                # Le relevé garde 50 identifiants par écriture : au-delà, le
                # compte est un plancher, et le dire vaut mieux qu'un chiffre faux.
                phrase = env._("%s (at least)", phrase)
        if item["labels"]:
            restants = item["count"] - len(item["labels"])
            nommees = ", ".join(item["labels"])
            if restants > 0:
                nommees = env._(
                    "%(named)s and %(others)s more", named=nommees, others=restants)
            phrase = env._("%(phrase)s (%(named)s)", phrase=phrase, named=nommees)
        return phrase

    def _bf_summary_html(self):
        """Le résumé en HTML, pour le formulaire. Markup de bout en bout : un
        champ HTML échappe tout ``str``, et « str + Markup » ré-échappe."""
        self.ensure_one()
        items, vus = self._bf_summary_items()
        env = self.env
        morceaux = []
        if items:
            morceaux.append(Markup("<p><b>%s</b></p>") % env._("What was changed"))
            morceaux.append(Markup("<ul>%s</ul>") % Markup("").join(
                Markup("<li>%s</li>") % self._bf_phrase(item) for item in items))
        elif self.mode == "write":
            morceaux.append(Markup("<p>%s</p>") % env._("Nothing was changed."))
        else:
            morceaux.append(Markup("<p>%s</p>") % env._(
                "Read only: nothing could be changed."))
        if vus:
            morceaux.append(Markup("<p><b>%s</b></p>") % env._("What was looked at"))
            morceaux.append(Markup("<ul>%s</ul>") % Markup("").join(
                Markup("<li>%s</li>") % self._bf_phrase(item) for item in vus))
        return Markup("").join(morceaux)

    def _bf_button_label(self):
        """Le libellé du bouton de l'avis, qui dit où il mène.

        Appelé par la mise en page du module. Il se décide sur l'état : l'avis
        de début part pendant que la séance est ouverte, celui de fin après sa
        fermeture. « Voir Session d'incarnation », que la mise en page commune
        fabriquait, ne disait ni l'un ni l'autre.
        """
        self.ensure_one()
        if self.state == "open":
            return self.env._("See this session")
        return self.env._("See what was done")

    def _bf_brand_company(self):
        """La société dont le rapport prend les couleurs et le logo, en ``sudo()``.

        🔴 Lue en ``sudo()`` exprès : le rapport se rend SOUS LA PERSONNE VUE, et
        la société de qui l'a incarnée n'est pas forcément une des siennes. Sans
        ça, la règle multi-société d'Odoo fait lever le rendu avant la première
        ligne (mesuré au banc le 2026-11-08). L'image de marque n'est pas
        un renseignement personnel.
        """
        self.ensure_one()
        journal = self.sudo()
        # La société de la personne VUE d'abord : c'est elle qui lit ce rapport.
        # Prendre celle de qui l'a incarnée sortait le rapport d'une personne
        # aux couleurs d'une société qui n'est pas la sienne, dès que les deux
        # comptes n'ont pas la même (relecture adverse).
        return (journal.target_user_id.company_id
                or journal.user_id.company_id
                or self.env.company).sudo()

    def action_download_report(self):
        """Le rapport de séance, en PDF. Un rapport QWeb n'écrit rien en base :
        c'est la seule forme de téléchargement qui passe ici, puisque AUCUNE ACL
        ne donne l'écriture sur ce modèle, pas même aux administrateurs, et que
        créer une pièce jointe liée l'exigerait."""
        self.ensure_one()
        self.check_access("read")
        return self.env.ref(
            "bf_impersonate.action_report_bf_impersonate_session").report_action(self)

    # ------------------------------------------------------------------
    # Avis à la personne

    def _bf_notify(self, moment):
        self.ensure_one()
        policy = self.env["ir.config_parameter"].sudo().get_param(
            NOTIFY_PARAM, NOTIFY_DEFAULT)
        if policy == NOTIFY_NEVER:
            return
        if moment == "end" and policy != NOTIFY_START_END:
            return
        target = self.target_user_id
        # Un contexte neuf, pas celui du client : le navigateur pourrait sinon
        # repousser l'avis (mail_defer_seconds, mail_notify_force_send).
        journal = self.sudo().with_context({"lang": target.lang or self.env.lang})
        subject, body = journal._bf_notification(moment)
        # Envoyé sur-le-champ (force_send), jamais mis en file ni différé : la file
        # des courriels peut être inactive, et mail_post_defer repousserait l'avis.
        # L'avis de début part AVANT la validation : après elle, la session est déjà
        # incarnée et les gardes d'envoi (à raison) le refuseraient. Vu au banc le
        # 2026-10-09 sur une base sans mail_post_defer. L'avis de fin, lui, part
        # après la restauration : il garde l'envoi après validation, pour qu'une
        # panne du serveur de courriel ne bloque pas le retour au compte.
        journal.message_notify(
            partner_ids=target.partner_id.ids,
            author_id=self.user_id.partner_id.id,
            subject=subject,
            body=body,
            # La mise en page du module, pas la commune : elle place le bouton
            # APRÈS le texte et lui donne son libellé.
            email_layout_xmlid="bf_impersonate.mail_layout_impersonate",
            force_send=True,
            send_after_commit=moment != "start",
        )

    def _bf_minutes(self, minutes):
        if minutes < 1:
            return self.env._("less than a minute")
        if minutes == 1:
            return self.env._("1 minute")
        return self.env._("%s minutes", minutes)

    def _bf_when(self, value):
        """« 8 octobre 2026 à 14 h 05 », dans le fuseau et la langue de la personne."""
        tz = pytz.timezone(self.target_user_id.tz or "UTC")
        local = pytz.utc.localize(value).astimezone(tz)
        return self.env._(
            "%(date)s at %(time)s",
            date=format_date(self.env, local.date(), date_format="long"),
            time=format_time(self.env, local.time(), time_format="short"),
        )

    def _bf_notification(self, moment):
        self.ensure_one()
        env = self.env
        origin = self.user_id.name
        mode = dict(self._fields["mode"]._description_selection(env)).get(self.mode)
        if moment == "start":
            policy = env["ir.config_parameter"].sudo().get_param(
                NOTIFY_PARAM, NOTIFY_DEFAULT)
            subject = env._("%s is seeing Symbifox as you", origin)
            # Ce que l'avis de début peut dire sans mentir : il part AVANT le
            # premier geste, donc le journal est encore vide à cette seconde.
            # L'ancienne phrase (« le journal dit ce qui a été fait ») promettait
            # le contraire, et le résumé de fin n'existait pas.
            suite = (
                env._("When the session ends, you will receive a summary of what was done.")
                if policy == NOTIFY_START_END
                else env._("The journal of this session records everything that is done."))
            body = Markup("<p>%s</p><p>%s</p><p>%s</p>") % (
                env._(
                    "%(origin)s opened a session as you on %(start)s, %(mode)s, "
                    "for at most %(duration)s.",
                    origin=origin,
                    start=self._bf_when(self.date_start),
                    mode=mode.lower() if mode else "",
                    duration=self._bf_minutes(
                        int((self.expires_at - self.date_start).total_seconds() // 60)),
                ),
                env._("Reason: %s", self.reason),
                suite,
            )
            return subject, body
        end_label = dict(self._fields["end_reason"]._description_selection(env)).get(
            self.end_reason) or ""
        subject = env._("The session of %s as you has ended", origin)
        items, vus = self._bf_summary_items()
        body = Markup("<p>%s</p>") % env._(
            "Ended on %(end)s (%(reason)s) after %(duration)s.",
            end=self._bf_when(self.date_end),
            reason=end_label.lower(),
            duration=self._bf_minutes(self.duration_minutes),
        )
        for titre, groupe in ((env._("What was changed"), items),
                              (env._("What was looked at"), vus)):
            if not groupe:
                continue
            body += Markup("<p><b>%s</b></p><ul>%s</ul>") % (
                titre,
                Markup("").join(
                    Markup("<li>%s</li>") % self._bf_phrase(item)
                    for item in groupe[:PHRASES_MAX_COURRIEL]),
            )
            if len(groupe) > PHRASES_MAX_COURRIEL:
                body += Markup("<p>%s</p>") % env._(
                    "And %s more, in the journal.", len(groupe) - PHRASES_MAX_COURRIEL)
        if not items:
            body += Markup("<p>%s</p>") % (
                env._("Nothing was changed.") if self.mode == "write"
                else env._("Read only: nothing could be changed."))
        return subject, body


class BfImpersonateSessionLine(models.Model):
    _name = "bf.impersonate.session.line"
    _description = "Impersonation journal line"
    _order = "date, id"

    session_id = fields.Many2one(
        "bf.impersonate.session", required=True, readonly=True,
        index=True, ondelete="cascade")
    kind = fields.Selection(
        [("write", "Change"), ("read", "Consultation")], string="Kind",
        required=True, readonly=True, default="write", index=True,
        help="A change is one request that wrote, or asked to. A consultation "
             "is one line per application looked at, with how many requests "
             "read it, kept only while the session changes nothing.")
    date = fields.Datetime(readonly=True, default=fields.Datetime.now)
    route = fields.Char(readonly=True)
    model = fields.Char(readonly=True)
    method = fields.Char(readonly=True)
    record_ids = fields.Char(string="Records", readonly=True)
    record_labels = fields.Char(
        string="Records seen", readonly=True,
        help="The names those records carried when the action happened. A name "
             "can change afterwards, and a deleted record has none: the "
             "journal keeps what was true at the time.")
    field_names = fields.Char(string="Fields", readonly=True)
    read_count = fields.Integer(
        string="Requests", readonly=True, default=0,
        help="How many requests read this application during the session.")
    writes_json = fields.Text(
        string="Writes", readonly=True,
        help="What this request really wrote, as data: model, operation, "
             "records and the names they carried. The summary is built from it.")
    label = fields.Char(string="Action", compute="_compute_label")
    details = fields.Text(
        readonly=True,
        help="Every write made as the person during this call: model, "
             "operation, records and fields.")

    def init(self):
        """Une seule ligne de consultation par séance et par modèle.

        Index unique PARTIEL : les lignes d'écriture, elles, se répètent autant
        de fois que la personne a cliqué. Posé ici parce qu'Odoo ne sait pas
        déclarer un index conditionnel, et c'est lui que l'``ON CONFLICT`` de
        ``_bf_note_read`` vise.
        """
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS bf_impersonate_session_line_read_uniq
            ON bf_impersonate_session_line (session_id, model)
            WHERE kind = 'read'
        """)

    @api.depends("kind", "model", "method", "route", "record_ids", "record_labels",
                 "field_names", "read_count")
    def _compute_label(self):
        # La table des verbes une fois pour le lot : par ligne, elle coûtait 19
        # recherches de traduction, soit 19 000 pour mille lignes.
        verbes = self.session_id[:1]._bf_verbs() if self else {}
        for line in self:
            line.label = line._bf_label(verbes=verbes)

    def _bf_writes(self):
        """Les écritures réelles de cette ligne, telles que le journal les porte."""
        self.ensure_one()
        if not self.writes_json:
            return []
        try:
            writes = json.loads(self.writes_json)
        except ValueError:
            _logger.warning("bf_impersonate : writes_json illisible, ligne %s", self.id)
            return []
        return [w for w in writes if isinstance(w, dict) and w.get("model")]

    def _bf_writes_from_call(self):
        """Le repli des lignes d'avant la 18.0.1.1.0, et des appels qui n'ont rien
        écrit : l'appel lui-même compte comme un geste."""
        self.ensure_one()
        if not self.model:
            return []
        # Des ENTIERS, comme en rend `_bf_writes` : mêlés, « 7 » et 7
        # compteraient pour deux fiches (relecture adverse, constat C4).
        ids = [int(i) for i in (self.record_ids or "").split(",") if i.strip().isdigit()]
        return [{
            "model": self.model,
            "op": self.method or self.route or "",
            "ids": ids,
            "n": len(ids),
            "w": 1,
            "trunc": False,
            "labels": [x for x in (self.record_labels or "").split(" · ") if x],
        }]

    def _bf_label(self, verbes=None):
        """« a classé « Courriel » (Re: facture) », lisible par la personne avisée."""
        self.ensure_one()
        env = self.env
        session = self.session_id
        if not self.model:
            return self.route or ""
        modele = session._bf_model_label(self.model)
        verbes = verbes or session._bf_verbs()
        if self.kind == "read":
            return env._(
                "%(verb)s %(model)s, %(count)s",
                verb=verbes["_read"], model=modele,
                count=(env._("1 time") if self.read_count <= 1
                       else env._("%s times", self.read_count)),
            )
        verbe = verbes.get(self.method) or (
            verbes["message_post"] if self.route == "/mail/message/post"
            else self.method or self.route or "")
        noms = self.record_labels or self.record_ids
        label = env._("%(verb)s %(model)s", verb=verbe, model=modele)
        if noms:
            label = env._("%(label)s (%(records)s)", label=label, records=noms)
        champs = self._bf_fields_label()
        if champs:
            label = env._("%(label)s [%(fields)s]", label=label, fields=champs)
        return label

    def _bf_fields_label(self):
        """Les champs écrits sous leur libellé, pas sous leur nom technique :
        « État, Traité le » plutôt que « status, handled_at ».

        Un nom qu'aucun champ du modèle ne porte reste tel quel : la colonne mêle
        parfois les champs de plusieurs modèles, celui de l'appel et ceux que son
        code a écrits en passant.
        """
        self.ensure_one()
        noms = [nom.strip() for nom in (self.field_names or "").split(",") if nom.strip()]
        if not noms:
            return ""
        champs = {}
        if self.model and self.model in self.env:
            champs = self.env[self.model]._fields
        libelles = []
        for nom in noms:
            champ = champs.get(nom)
            libelles.append(
                (champ and champ._description_string(self.env)) or nom)
        return ", ".join(libelles)
