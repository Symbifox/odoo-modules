"""Compléter une société par une recherche en ligne de Gen.

Même principe que la lecture des factures fournisseur : la fiche part dans une
file, une tâche planifiée appelle le pont, et le résultat revient sur la fiche
sans que personne attende devant l'écran (20 à 60 s par société, mesuré le
2026-10-18). Ce que Gen trouve est ensuite proposé ou écrit selon la
préférence de l'usager qui a lancé la demande (``res.users.bf_gen_company_mode``).

Sociétés seulement : les coordonnées d'une entreprise sont celles d'un lieu de
travail, que l'art. 1 al. 5 de la Loi sur le privé (Québec) sort des sections
II et III. Une personne n'est jamais cherchée ici.
"""
import json
import logging

from markupsafe import Markup

from odoo import _, api, fields, models, modules
from odoo.exceptions import AccessError, UserError

from ..tools import bridge, matching
from ..tools import sources as sources_html

_logger = logging.getLogger(__name__)

_GROUP = "bf_contact_enrichment.group_bf_contact_enrich"
_CRON = "bf_contact_enrichment.ir_cron_bf_gen_companies"
#: Sous ce seuil, rien n'est proposé : la recherche n'a pas reconnu la société.
_SEUIL_PROPOSER = 30
#: Au-dessus, la préférence « Remplir » écrit sans demander.
_SEUIL_REMPLIR = 60
#: Le pont fait deux temps de 120 s au plus (recherche, puis site).
_DELAI_PONT = 270
#: Ce qu'une réponse du pont peut apporter à la fiche.
_CHAMPS_UTILES = ("website", "email", "phone", "street", "city", "zip")
#: L'état de la complétion. ``readonly`` ne vaut que pour l'écran : sans la
#: garde de ``create``/``write``, n'importe quel usager qui peut modifier un
#: contact mettrait une fiche en file par RPC, et la tâche planifiée paierait
#: la recherche sans que le groupe ait été vérifié. Les clés sont écartées
#: plutôt que refusées : la fusion native des contacts recopie les champs de la
#: fiche source sans sudo, et une erreur ici la ferait tomber.
_CHAMPS_GEN = ("x_bf_gen_state", "x_bf_gen_requested_by",
               "x_bf_gen_proposal", "x_bf_gen_message")


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_bf_gen_state = fields.Selection(
        [("queued", "Queued"),
         ("proposed", "Proposal to review"),
         ("done", "Completed"),
         ("dismissed", "Proposal dismissed"),
         ("nothing", "Nothing reliable found"),
         ("error", "Error")],
        string="Completion by Gen", copy=False, readonly=True,
    )
    x_bf_gen_requested_by = fields.Many2one(
        "res.users", string="Completion requested by", copy=False,
        readonly=True, ondelete="set null",
    )
    x_bf_gen_proposal = fields.Text(
        string="Gen's proposal", copy=False, readonly=True,
        groups=_GROUP,
        help="Bridge response (JSON), awaiting confirmation.",
    )
    x_bf_gen_message = fields.Char(
        string="Gen's message", copy=False, readonly=True,
    )

    # ── Mise en file ────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._bf_gen_sans_ses_champs(v) for v in vals_list]
        # 🔴 Les `default_x_bf_gen_*` du contexte s'ajoutent DANS `super().create`,
        # après la garde des `vals` : hors sudo, ils mettaient en file (recherche
        # payée) ou faisaient remplir au nom d'un autre demandeur.
        Partner = self
        if not self.env.su and any(
                k.startswith("default_x_bf_gen_") for k in self.env.context):
            Partner = self.with_context({
                k: v for k, v in self.env.context.items()
                if not k.startswith("default_x_bf_gen_")})
        partners = super(ResPartner, Partner).create(vals_list)
        partners._bf_gen_mettre_en_file_a_la_creation()
        return partners.with_env(self.env)

    def write(self, vals):
        return super().write(self._bf_gen_sans_ses_champs(vals))

    def _bf_gen_sans_ses_champs(self, vals):
        """Seul le module écrit l'état de la complétion, et il le fait en sudo."""
        if self.env.su or not any(k in vals for k in _CHAMPS_GEN):
            return vals
        return {k: v for k, v in vals.items() if k not in _CHAMPS_GEN}

    def _bf_gen_incomplete(self):
        """Une société à qui il manque le site, le téléphone ou la rue."""
        self.ensure_one()
        return not (self.website and (self.phone or self.mobile) and self.street)

    def _bf_gen_peut_demander(self, user):
        """Un usager interne du groupe d'enrichissement : c'est lui qui engage
        le forfait Claude du locataire."""
        return bool(user) and not user.share and user.has_group(_GROUP)

    def _bf_gen_mettre_en_file_a_la_creation(self):
        ctx = self.env.context
        # Une création de programme (sudo, import, installation) n'est pas une
        # société saisie par quelqu'un : on ne la paie pas en douce.
        if (self.env.su or ctx.get("import_file") or ctx.get("install_mode")
                or ctx.get("bf_gen_no_queue")):
            return
        user = self.env.user
        if user.bf_gen_company_mode == "off" or not self._bf_gen_peut_demander(user):
            return
        # Qui ne peut pas modifier la fiche ne fait pas chercher pour elle (N2).
        a_completer = self.filtered(
            lambda p: p.is_company and (p.name or "").strip()
            and p._bf_gen_incomplete())._filtered_access("write")
        if a_completer:
            a_completer._bf_gen_enfiler(user)

    def _bf_gen_enfiler(self, user):
        self.sudo().write({
            "x_bf_gen_state": "queued",
            "x_bf_gen_requested_by": user.id,
            "x_bf_gen_proposal": False,
            "x_bf_gen_message": False,
        })
        cron = self.env.ref(_CRON, raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    def action_bf_gen_complete(self):
        """Bouton « Compléter avec Gen » : même file que la création."""
        self.ensure_one()
        if not self._bf_gen_peut_demander(self.env.user):
            raise AccessError(
                _("Access denied: the “Contact Enrichment” group is required."))
        # La tâche écrit en sudo l'état : sans cela, un lecteur ferait payer
        # une recherche sur une fiche qu'il ne peut pas modifier (N2).
        self.check_access("write")
        if not self.is_company:
            raise UserError(_("Gen only completes companies."))
        if not (self.name or "").strip():
            raise UserError(_("The company has no name to search for."))
        self._bf_gen_enfiler(self.env.user)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Completion by Gen"),
                "message": _("Gen is searching online for “%s”. The "
                             "result will appear on the record in about a "
                             "minute.") % self.name,
                "type": "info",
                "sticky": False,
            },
        }

    def action_bf_gen_review(self):
        """Bouton « Voir la proposition de Gen » : l'assistant actuel/proposé."""
        self.ensure_one()
        if not self._bf_gen_peut_demander(self.env.user):
            raise AccessError(
                _("Access denied: the “Contact Enrichment” group is required."))
        if self.x_bf_gen_state != "proposed":
            raise UserError(_("No pending proposal from Gen."))
        data = self._bf_gen_lire_proposition()
        wizard = self.env["bf.contact.enrich.wizard"].create({
            "partner_id": self.id,
            "source": "gen",
        })
        wizard._fill_proposals(data)
        return wizard._reopen()

    def _bf_gen_lire_proposition(self):
        self.ensure_one()
        try:
            data = json.loads(self.sudo().x_bf_gen_proposal or "{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    # ── Tâche planifiée ─────────────────────────────────────────────

    @api.model
    def _cron_bf_gen_companies(self, chunk=1):
        """Une société à la fois : il n'y a qu'un fil de tâches planifiées, et
        un appel peut durer quatre minutes. La suivante repasse aussitôt."""
        en_file = self.search(
            [("x_bf_gen_state", "=", "queued")], order="write_date asc, id asc",
            limit=chunk).ids
        for partner_id in en_file:
            self._bf_gen_traiter(partner_id)
        if en_file and self.search_count([("x_bf_gen_state", "=", "queued")]):
            cron = self.env.ref(_CRON, raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()

    @api.model
    def _bf_gen_traiter(self, partner_id):
        """Une fiche : lire la charge, valider, appeler le pont, valider, relire,
        écrire.

        🔴 Aucune transaction n'est tenue pendant l'appel (20 à 270 s). Avant,
        une fiche enregistrée par quelqu'un pendant la recherche faisait échouer
        l'écriture (SerializationFailure) : la fiche restait en file et la
        recherche se repayait à la tâche suivante.
        """
        partner = self.browse(partner_id).exists()
        if not partner or partner.x_bf_gen_state != "queued":
            return
        try:
            if not partner.is_company:
                # Passée en personne depuis la mise en file : on ne la cherche
                # pas, et on la sort de la file (N4).
                partner._bf_gen_pas_une_societe()
                self._bf_gen_valider()
                return
            payload = partner._bf_gen_charge()
        except Exception:  # noqa: BLE001 — une fiche ne bloque pas la file
            _logger.exception("Gen company completion: cannot read partner %s",
                              partner_id)
            self._bf_gen_marquer_erreur(partner_id)
            self._bf_gen_valider()
            return
        self._bf_gen_valider()
        result = partner._bf_gen_appeler(payload)
        # 🔴 N1 : l'appel lit les paramètres du pont (`tenant()`, `socket_path()`),
        # et sous un cache froid ces lectures ouvrent un instantané REPEATABLE
        # READ AVANT l'appel. Une fiche enregistrée pendant la recherche n'y
        # serait pas vue, et l'écriture tomberait en SerializationFailure. On
        # termine donc la transaction ici : la relecture part d'un instantané
        # pris après l'appel.
        self._bf_gen_valider()
        # Pendant l'appel, la fiche a pu être supprimée, archivée, écartée ou
        # traitée ailleurs : on relit tout avant d'écrire quoi que ce soit.
        self.env.invalidate_all()
        partner = self.browse(partner_id).exists()
        if not (partner and partner.active
                and partner.x_bf_gen_state == "queued"):
            _logger.info("Gen company completion for partner %s discarded: "
                         "the record changed during the bridge call", partner_id)
            return
        try:
            with self.env.cr.savepoint():
                if partner.is_company:
                    partner._bf_gen_appliquer_reponse(result)
                else:
                    partner._bf_gen_pas_une_societe()
        except Exception:  # noqa: BLE001 — une fiche ne bloque pas la file
            _logger.exception("Gen company completion failed for partner %s",
                              partner_id)
            self._bf_gen_marquer_erreur(partner_id)
        self._bf_gen_valider()

    @api.model
    def _bf_gen_valider(self):
        """Valider la transaction, sauf sous les essais, où le curseur ne se
        valide pas (même garde que dans `account`)."""
        if not modules.module.current_test:
            self.env.cr.commit()

    @api.model
    def _bf_gen_marquer_erreur(self, partner_id):
        """Sortir la fiche de la file quoi qu'il arrive : restée en file, elle
        repasserait à la tâche suivante, et la recherche se repaierait."""
        # Le message est dans la langue du demandeur (sa lecture peut échouer
        # dans une transaction déjà cassée : on garde alors celle de la tâche).
        message = self._bf_gen_message_inattendu()
        try:
            with self.env.cr.savepoint():
                partner = self.browse(partner_id).exists().sudo()
                message = partner._bf_gen_dans_la_langue_de(
                    partner.x_bf_gen_requested_by)._bf_gen_message_inattendu()
                partner.write(
                    {"x_bf_gen_state": "error", "x_bf_gen_message": message})
            return
        except Exception:  # noqa: BLE001
            _logger.exception("Gen company completion: cannot flag partner %s",
                              partner_id)
        if not modules.module.current_test:
            # Une transaction en échec (sérialisation) le resterait au point
            # d'arrêt : on repart d'une transaction neuve.
            self.env.cr.rollback()
        # Dernier recours, sans l'ORM (ni suivi, ni calcul, ni garde).
        self.env.cr.execute(
            "UPDATE res_partner SET x_bf_gen_state = 'error', "
            "x_bf_gen_message = %s WHERE id = %s AND x_bf_gen_state = 'queued'",
            (message, partner_id))
        self.env.invalidate_all(flush=False)

    def _bf_gen_message_inattendu(self):
        return _("Unexpected error.")

    def _bf_gen_pas_une_societe(self):
        """Gen ne cherche que des sociétés : une fiche passée en personne sort
        de la file sans appel ni proposition.

        Le message est écrit pour le demandeur, dans sa langue (voir
        ``_bf_gen_dans_la_langue_de``) : cette sortie de file n'est pas passée
        par ``_bf_gen_appliquer_reponse``.
        """
        self = self._bf_gen_dans_la_langue_de(
            self.sudo().x_bf_gen_requested_by)
        self.sudo().write({
            "x_bf_gen_state": "nothing",
            "x_bf_gen_proposal": False,
            "x_bf_gen_message": _("This contact is no longer a company."),
        })

    def _bf_gen_charge(self):
        """Ce que le pont reçoit : lu avant l'appel, dans la transaction validée."""
        self.ensure_one()
        return {
            "name": self.name,
            "city": self.city or "",
            "state": self.state_id.name or "",
            "country": self.country_id.name or "",
            "website": self.website or "",
        }

    def _bf_gen_appeler(self, payload):
        """Le pont seul, sans rien lire ni écrire : None si le transport a échoué."""
        self.ensure_one()
        try:
            return bridge.call_bridge(
                self.env, "/enrich/company-search", payload, timeout=_DELAI_PONT)
        except Exception as e:  # noqa: BLE001 — transport : socket, délai, 404
            _logger.warning("Gen company search unavailable for partner %s: %s",
                            self.id, e)
            return None

    def _bf_gen_run(self):
        """Appeler le pont puis appliquer, sans validation entre les deux. La
        tâche planifiée passe par `_bf_gen_traiter`, qui ne tient pas de
        transaction pendant l'appel."""
        self.ensure_one()
        return self._bf_gen_appliquer_reponse(
            self._bf_gen_appeler(self._bf_gen_charge()))

    def _bf_gen_dans_la_langue_de(self, demandeur):
        """``self`` dans la langue de celui qui LIRA ce que la tâche écrit.

        🔴 La tâche planifiée tourne sous OdooBot (fr_CA sur une base réelle) :
        ``_()`` y prend la langue de qui ÉCRIT. Le demandeur, lui, lit la
        notification, le message d'état de la fiche et la note au fil, dans la
        sienne.
        """
        lang = demandeur.lang if demandeur else False
        return self.with_context(lang=lang) if lang else self

    def _bf_gen_appliquer_reponse(self, result):
        """Proposer, écrire ou dire qu'il n'y a rien.

        Toutes les écritures d'état passent en sudo : la garde de `write`
        écarte en silence celles d'un usager ordinaire, et l'usager de la tâche
        planifiée peut en être un (la fiche resterait en file, et repaierait).
        """
        self.ensure_one()
        demandeur = self.x_bf_gen_requested_by
        self = self._bf_gen_dans_la_langue_de(demandeur)
        if result is None:
            self.sudo().write({"x_bf_gen_state": "error",
                               "x_bf_gen_message": _("Gen service unavailable.")})
            self._bf_gen_avertir(demandeur, "warning",
                                 _("Gen could not complete “%s” (service "
                                   "unavailable).") % self.name)
            return "error"
        if result.get("error"):
            self.sudo().write({"x_bf_gen_state": "error",
                               "x_bf_gen_message": str(result["error"])[:250]})
            self._bf_gen_avertir(demandeur, "warning",
                                 _("Gen could not complete “%s”.") % self.name)
            return "error"

        data = result.get("data") or {}
        confiance = self._bf_gen_as_int(data.get("confidence"))
        if confiance < _SEUIL_PROPOSER or not any(
                data.get(k) for k in _CHAMPS_UTILES):
            self.sudo().write({"x_bf_gen_state": "nothing",
                               "x_bf_gen_message": _("Nothing reliable "
                                                     "found online "
                                                     "(confidence %s).")
                               % confiance})
            self._bf_gen_avertir(demandeur, "warning",
                                 _("Gen found nothing reliable for “%s”.")
                                 % self.name)
            return "nothing"

        mode = demandeur.bf_gen_company_mode if demandeur else "propose"
        # N2 : remplir, c'est écrire AU NOM du demandeur, sous ses droits. S'il
        # ne peut plus modifier la fiche, on propose au lieu d'écrire ; seuls
        # les champs d'état `x_bf_gen_*` s'écrivent en sudo.
        au_nom = self.with_user(demandeur) if demandeur else None
        if (mode == "fill" and confiance >= _SEUIL_REMPLIR
                and au_nom and au_nom.has_access("write")):
            applied = au_nom._bf_gen_appliquer(data, demandeur)
            self.sudo().write({"x_bf_gen_state": "done",
                               "x_bf_gen_proposal": False,
                               "x_bf_gen_message": False})
            self._bf_gen_avertir(
                demandeur, "success",
                _("Gen completed “%(name)s” (%(n)s field(s)).")
                % {"name": self.name, "n": len(applied)})
            return "done"

        self.sudo().write({
            "x_bf_gen_state": "proposed",
            "x_bf_gen_proposal": json.dumps(data, ensure_ascii=False),
            "x_bf_gen_message": False,
        })
        self._bf_gen_avertir(demandeur, "info",
                             _("Gen has a proposal for “%s”: open the "
                               "record to review it.") % self.name)
        return "proposed"

    # ── Écriture ────────────────────────────────────────────────────

    def _bf_gen_vals(self, data, overwrite=False):
        """Les valeurs res.partner tirées d'une réponse (ou d'un assistant)."""
        self.ensure_one()
        pays_rendu = self._enrich_country_id(data.get("country"))
        # La province se cherche dans le pays qui sera sur la fiche APRÈS
        # l'écriture : le sien s'il est rempli (et pas écrasé), sinon celui
        # rendu. Une province d'un autre pays est omise (Canada + Vermont).
        if self.country_id and not (overwrite and pays_rendu):
            pays_final = self.country_id.id
        else:
            pays_final = pays_rendu
        vals = {
            "website": data.get("website"),
            "email": data.get("email"),
            "phone": data.get("phone"),
            "street": data.get("street"),
            "city": data.get("city"),
            "zip": data.get("zip"),
            "country_id": pays_rendu,
            "state_id": self._bf_gen_state_id(data.get("state"), pays_final),
        }
        return {k: v for k, v in vals.items() if v}

    @api.model
    def _bf_gen_state_id(self, name, country_id):
        """Province ou État, cherché dans le pays connu seulement.

        Sans accent ni casse : Gen rend « Québec » et une base en anglais
        l'appelle « Quebec » (``=ilike`` ne plie pas les accents). Le nom d'une
        province n'est PAS un champ traduit (contrairement à celui d'un pays) :
        la langue du contexte n'y change rien. Les états d'un pays se comptent en
        dizaines : on les compare en Python.
        """
        cle = matching.normalize(name)
        if not cle or not country_id:
            return False
        for state in self.env["res.country.state"].search(
                [("country_id", "=", country_id)]):
            if cle in (matching.normalize(state.name),
                       matching.normalize(state.code)):
                return state.id
        return False

    def _bf_gen_appliquer(self, data, demandeur, overwrite=False):
        """Écrire (vides seulement par défaut) puis poser les sources au fil."""
        self.ensure_one()
        applied = self._apply_contact_vals(
            self._bf_gen_vals(data, overwrite=overwrite),
            source=_("online search (Gen)"),
            overwrite=overwrite)
        self._bf_gen_poser_sources(data.get("sources"), demandeur)
        return applied

    def _bf_gen_poser_sources(self, sources, demandeur):
        """La provenance au fil : qui a demandé, et ce que Gen a lu."""
        self.ensure_one()
        items = sources_html.items_html(sources)
        body = Markup(_("<p>Online search by Gen, requested by %(user)s. "
                        "Sources read:</p><ul>%(items)s</ul>")) % {
            "user": demandeur.name if demandeur else _("a user"),
            "items": items or Markup("<li>%s</li>") % _("none"),
        }
        self.message_post(body=body)

    # ── Divers ──────────────────────────────────────────────────────

    def _bf_gen_avertir(self, user, kind, message):
        """Une notification passagère au demandeur, où qu'il soit dans Odoo."""
        if not user:
            return
        user.partner_id._bus_send("simple_notification", {
            "type": kind,
            "title": _("Completion by Gen"),
            "message": message,
            "sticky": False,
        })

    @staticmethod
    def _bf_gen_as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
