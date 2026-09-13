"""bf.partner.absence — l'absence d'un contact, du côté de celui qui écrit.

Odoo sait qu'un EMPLOYÉ est en congé (`hr_holidays` calcule
`leave_date_to`, transforme `im_status` en `leave_online` et publie
`out_of_office_date_end` sur le partenaire), mais trois choses l'empêchent de
servir ici, et elles ont été vérifiées avant d'écrire ce fichier :

- ça ne vaut que pour un partenaire qui porte un `res.users` interne ;
- l'affichage ne vit que dans Discuss (le bandeau de `hr_holidays` est posé
  sous condition `props.thread.model === 'discuss.channel'`), donc le chatter
  d'une fiche ou d'une tâche n'en voit rien ;
- `hr_holidays` surcharge `res.partner._to_store` et pose
  `out_of_office_date_end: False` pour un contact sans utilisateur, ce qui
  écraserait une valeur posée par un autre module.

On n'emprunte donc que le vocabulaire, pas le canal.

**Deux règles de conception tiennent tout le reste.**

1. **La date de fin est obligatoire.** Une absence sans retour laisse un
   bandeau allumé pour toujours, et un avertissement qui crie au loup se fait
   désarmer au troisième affichage. Pas de fin, pas d'absence.
2. **On avertit, on ne barre jamais.** Écrire à quelqu'un en vacances est
   souvent exactement ce qu'on veut faire.

⚠️ Vie privée : la nature d'une absence se choisit dans une liste courte qui ne
contient AUCUN motif de santé, et le module ne recopie jamais le texte libre
d'un répondeur d'absence. Une fenêtre, une nature, une relève.
"""

import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date

_logger = logging.getLogger(__name__)

# L'heure, le matin du jour de reprise, à laquelle un envoi reporté part.
# Réglable par `bf_contact_absence.return_hour`.
DEFAULT_RETURN_HOUR = 9

# 🔴 Combien d'absences un bandeau nomme avant de compter. Vingt-cinq
# destinataires absents rendaient 2 440 caractères dans le composeur, ce qui
# n'est plus un avertissement mais un mur. Trouvé à l'audit du 2026-09-13.
MAX_LIGNES_BANDEAU = 3


class BfPartnerAbsence(models.Model):
    _name = "bf.partner.absence"
    _description = "Absence d'un contact"
    _order = "date_from desc, id desc"
    _rec_name = "display_name"

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contact",
        required=True,
        index=True,
        ondelete="cascade",
    )
    partner_is_company = fields.Boolean(related="partner_id.is_company")

    date_from = fields.Date(
        string="Du",
        required=True,
        default=fields.Date.context_today,
        help="Premier jour d'absence, inclus.",
    )
    date_to = fields.Date(
        string="Au",
        required=True,
        help="DERNIER jour d'absence, inclus. Une absence sans fin laisserait "
             "un avertissement allumé pour toujours : la fin est obligatoire.",
    )
    # La prose des répondeurs d'absence donne les deux formes : « jusqu'au
    # 3 mai » (dernier jour absent) et « de retour le 4 mai » (jour de
    # reprise). Le champ de reprise est offert en saisie pour qu'on puisse
    # recopier l'une ou l'autre sans se tromper d'un jour, et c'est cette
    # erreur d'un jour qui ferait parler le bandeau le jour du retour.
    date_return = fields.Date(
        string="De retour le",
        compute="_compute_date_return",
        inverse="_inverse_date_return",
        store=True,
        readonly=False,
        help="Premier jour de présence. Se déduit de « Au », et le corrige "
             "quand on saisit celui-ci à la place.",
    )

    nature = fields.Selection(
        selection=[
            ("vacation", "Vacances"),
            ("leave", "Congé"),
            ("closure", "Fermeture"),
            ("training", "Formation ou congrès"),
            ("other", "Autre"),
        ],
        string="Nature",
        required=True,
        default="vacation",
        help="Liste volontairement courte et sans motif de santé : ce champ "
             "est une donnée personnelle qu'on garde au minimum.",
    )

    backup_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Relève",
        help="À qui écrire pendant l'absence. C'est la moitié de l'utilité du "
             "module : un bandeau qui redirige vaut mieux qu'un bandeau qui "
             "dit seulement d'attendre.",
    )
    backup_info = fields.Char(
        string="Relève (texte)",
        help="Quand la relève n'a pas de fiche : une adresse, un numéro, "
             "« la réception ».",
    )

    applies_to_children = fields.Boolean(
        string="Vaut pour tous les contacts de la société",
        default=True,
        help="Sur une société, une fermeture avertit pour toutes les "
             "personnes qui lui sont rattachées. Décocher pour une absence "
             "qui ne vise que la fiche de la société elle-même.",
    )

    reminder = fields.Boolean(
        string="Rappel au retour",
        default=True,
        help="Pose une activité « prendre des nouvelles » au lendemain du "
             "retour. Le lendemain, et non le jour même : personne ne veut "
             "être le premier message d'une boîte pleine.",
    )
    reminder_activity_id = fields.Many2one(
        comodel_name="mail.activity",
        string="Activité de reprise",
        readonly=True,
        ondelete="set null",
    )

    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Suivi par",
        default=lambda self: self.env.user,
        help="Qui reçoit le rappel de reprise.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(string="Actif", default=True)

    source = fields.Selection(
        selection=[
            ("manual", "Saisie à la main"),
            ("autoreply", "Répondeur d'absence"),
            ("import", "Importée"),
        ],
        string="Source",
        default="manual",
        required=True,
    )
    note = fields.Char(
        string="Note",
        help="Une ligne, pour nous. Jamais le texte d'un répondeur.",
    )

    state = fields.Selection(
        selection=[
            ("planned", "À venir"),
            ("running", "En cours"),
            ("over", "Terminée"),
        ],
        string="État",
        compute="_compute_state",
        search="_search_state",
    )
    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends("date_to")
    def _compute_date_return(self):
        for absence in self:
            absence.date_return = (
                absence.date_to + timedelta(days=1) if absence.date_to else False
            )

    def _inverse_date_return(self):
        for absence in self:
            if absence.date_return:
                absence.date_to = absence.date_return - timedelta(days=1)

    @api.depends("date_from", "date_to")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for absence in self:
            if not absence.date_from or not absence.date_to:
                absence.state = "planned"
            elif absence.date_to < today:
                absence.state = "over"
            elif absence.date_from > today:
                absence.state = "planned"
            else:
                absence.state = "running"

    def _search_state(self, operator, value):
        # ⚠️ Un champ calculé non stocké SANS `search=` voit son critère écarté
        # en silence : le filtre rendrait alors toute la base. Cette méthode
        # existe pour ça, et elle refuse bruyamment ce qu'elle ne sait pas
        # traduire plutôt que de rendre un résultat faux.
        if operator not in ("=", "!=", "in", "not in"):
            raise UserError(_("Filtre non supporté sur l'état d'une absence."))
        values = value if isinstance(value, (list, tuple)) else [value]
        today = fields.Date.context_today(self)
        domains = {
            "running": [("date_from", "<=", today), ("date_to", ">=", today)],
            "planned": [("date_from", ">", today)],
            "over": [("date_to", "<", today)],
        }
        retenus = self.env["bf.partner.absence"]
        for val in values:
            if val in domains:
                retenus |= self.search(domains[val])
        positif = operator in ("=", "in")
        return [("id", "in" if positif else "not in", retenus.ids)]

    @api.depends("partner_id", "date_from", "date_to", "nature")
    def _compute_display_name(self):
        natures = dict(self._fields["nature"].selection)
        for absence in self:
            if not absence.partner_id:
                absence.display_name = natures.get(absence.nature, "")
                continue
            absence.display_name = "%s : %s du %s au %s" % (
                absence.partner_id.display_name,
                natures.get(absence.nature, ""),
                absence._jour(absence.date_from) or "?",
                absence._jour(absence.date_to) or "?",
            )

    # ------------------------------------------------------------------
    # Garde-fous
    # ------------------------------------------------------------------
    @api.constrains("date_from", "date_to")
    def _check_period(self):
        for absence in self:
            if not absence.date_to:
                raise ValidationError(_(
                    "Une absence doit avoir une date de fin. Sans elle, "
                    "l'avertissement resterait allumé indéfiniment."
                ))
            if absence.date_to < absence.date_from:
                raise ValidationError(_(
                    "La fin d'une absence ne peut pas précéder son début."
                ))

    @api.constrains("partner_id", "date_from", "date_to", "active")
    def _check_no_overlap(self):
        for absence in self.filtered("active"):
            chevauche = self.search([
                ("id", "!=", absence.id),
                ("partner_id", "=", absence.partner_id.id),
                ("date_from", "<=", absence.date_to),
                ("date_to", ">=", absence.date_from),
            ], limit=1)
            if chevauche:
                raise ValidationError(_(
                    "%(contact)s a déjà une absence qui couvre ces dates "
                    "(%(autre)s). Deux périodes qui se chevauchent rendraient "
                    "le bandeau ambigu.",
                    contact=absence.partner_id.display_name,
                    autre=chevauche.display_name,
                ))

    @api.constrains("partner_id", "backup_partner_id")
    def _check_backup_not_self(self):
        for absence in self:
            if absence.backup_partner_id == absence.partner_id:
                raise ValidationError(_(
                    "La relève ne peut pas être la personne absente."
                ))

    # ------------------------------------------------------------------
    # Écritures
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        absences = super().create(vals_list)
        absences._sync_reminder()
        return absences

    def write(self, vals):
        res = super().write(vals)
        if {"reminder", "date_to", "date_return", "partner_id",
            "user_id", "active"} & set(vals):
            self._sync_reminder()
        return res

    def unlink(self):
        self.reminder_activity_id.sudo().unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # Le rappel de reprise
    # ------------------------------------------------------------------
    def _activity_type(self):
        """Le type d'activité « à faire », ou le premier disponible.

        ⚠️ Ne jamais créer une `mail.activity` sans `activity_type_id` : Odoo
        n'en pose pas de défaut utilisable et la création échoue.
        """
        type_id = self.env.ref("mail.mail_activity_data_todo",
                               raise_if_not_found=False)
        if type_id:
            return type_id
        return self.env["mail.activity.type"].search(
            [("res_model", "in", (False, "res.partner"))], limit=1)

    def _last_exchange_hint(self):
        """L'objet du dernier échange avec ce contact, pour le rappel.

        Un rappel qui dit « prendre des nouvelles » sans dire de quoi se fait
        reporter. Celui-ci nomme le dernier sujet, et il le cherche dans
        `mail.message` : aucune dépendance de plus, et ça marche que l'échange
        soit passé par une tâche, une opportunité ou la fiche elle-même.
        """
        self.ensure_one()
        if not self.partner_id:
            return ""
        message = self.env["mail.message"].sudo().search([
            "|",
            ("author_id", "=", self.partner_id.id),
            ("partner_ids", "in", self.partner_id.ids),
            ("message_type", "in", ("email", "comment", "email_outgoing")),
        ], order="date desc", limit=1)
        if not message:
            return ""
        titre = (message.subject or message.record_name or "").strip()
        return titre[:120]

    def _sync_reminder(self):
        """Pose, déplace ou retire l'activité de reprise.

        Le rappel tombe au LENDEMAIN du retour, comme les cadences du marché
        le font : la première journée sert à vider la boîte.
        """
        type_activite = self._activity_type()
        for absence in self:
            veut = (
                absence.reminder
                and absence.active
                and absence.date_return
                and absence.partner_id
            )
            if not veut:
                absence.reminder_activity_id.sudo().unlink()
                continue
            echeance = absence.date_return + timedelta(days=1)
            # ⚠️ L'assigné doit être un utilisateur INTERNE : une activité
            # assignée à un portail lui envoie une notification.
            assigne = absence.user_id
            if not assigne or assigne.share:
                assigne = self.env.user
            if absence.reminder_activity_id:
                absence.reminder_activity_id.sudo().write({
                    "date_deadline": echeance,
                    "user_id": assigne.id,
                    "note": absence._reminder_note(),
                })
                continue
            if not type_activite:
                _logger.warning(
                    "bf_contact_absence: aucun type d'activité disponible, "
                    "rappel de reprise non posé pour l'absence %s", absence.id)
                continue
            activite = self.env["mail.activity"].sudo().create({
                "res_model_id": self.env["ir.model"]._get_id("res.partner"),
                "res_id": absence.partner_id.id,
                "activity_type_id": type_activite.id,
                "summary": _("Prendre des nouvelles au retour"),
                "note": absence._reminder_note(),
                "date_deadline": echeance,
                "user_id": assigne.id,
            })
            absence.reminder_activity_id = activite

    def _reminder_note(self):
        self.ensure_one()
        note = _("Absence de %(contact)s jusqu'au %(fin)s.",
                 contact=self.partner_id.name,
                 fin=self._jour(self.date_to))
        prise = self._last_exchange_hint()
        if prise:
            note += " " + _("Dernier échange : « %s ».") % prise
        return note

    # ------------------------------------------------------------------
    # Ce que les autres modules appellent
    # ------------------------------------------------------------------
    @api.model
    def _applicable_domain(self, at_date=None):
        """Les absences en cours, dans les sociétés permises.

        🔴 La cloison de sociétés doit valoir pour le BANDEAU autant que pour
        la liste, sinon la règle d'enregistrement est décorative : l'audit du
        2026-09-13 a montré une absence notée dans la société B invisible à la
        liste d'un usager de A, et pourtant affichée dans son bandeau. Le
        filtre est posé ici plutôt que laissé à la règle parce que les lectures
        se font en `sudo()` : un calcul qui lève une erreur d'accès casserait
        la lecture d'une fiche contact, y compris au portail.
        """
        at_date = at_date or fields.Date.context_today(self)
        return [
            ("date_from", "<=", at_date),
            ("date_to", ">=", at_date),
            ("company_id", "in", [False] + self.env.companies.ids),
        ]

    @api.model
    def _for_partners(self, partner_ids, at_date=None):
        """Rend {partner_id: absence} pour les contacts absents à cette date.

        Une absence posée sur une SOCIÉTÉ vaut pour ses contacts rattachés :
        c'est ce que veut dire « nous serons fermés ». La fiche de la personne
        gagne quand elle en a une à elle.
        """
        partner_ids = [pid for pid in (partner_ids or []) if pid]
        if not partner_ids:
            return {}
        partners = self.env["res.partner"].browse(partner_ids).exists()
        parents = partners.mapped("parent_id")
        candidats = partners | parents
        absences = self.sudo().search(
            self._applicable_domain(at_date)
            + [("partner_id", "in", candidats.ids)]
        )
        par_partenaire = {}
        for absence in absences:
            par_partenaire.setdefault(absence.partner_id.id, absence)
        resultat = {}
        for partner in partners:
            propre = par_partenaire.get(partner.id)
            if propre:
                resultat[partner.id] = propre
                continue
            parent = par_partenaire.get(partner.parent_id.id)
            if parent and parent.applies_to_children:
                resultat[partner.id] = parent
        return resultat

    @api.model
    def _partners_away_ids(self, at_date=None):
        """Les ids de contacts absents à cette date, héritage compris."""
        absences = self.sudo().search(self._applicable_domain(at_date))
        ids = set()
        for absence in absences:
            ids.add(absence.partner_id.id)
            if absence.applies_to_children and absence.partner_id.is_company:
                ids.update(absence.partner_id.child_ids.ids)
        return ids

    def _jour(self, valeur):
        """« 17 septembre 2026 » plutôt que « 09/17/2026 ».

        ⚠️ Le format court de la locale est illisible dans une phrase, et il
        est AMBIGU entre le Québec et les États-Unis : 09/17 se lit tout seul,
        09/11 ne se lit pas. Un bandeau qu'on doit décoder ne sert à rien.
        """
        if not valeur:
            return ""
        return format_date(self.env, valeur, date_format="d MMMM y")

    def _phrase(self, for_partner=None):
        """Une phrase, pas un rapport. Elle nomme la personne, la date de
        retour et la relève, parce que c'est ce qui change le geste.

        ⚠️ `name` et non `display_name` : sur un contact rattaché, le nom
        affiché commence par la société (« Société Exemple, Prénom Nom ») et le
        bandeau devient une ligne de formulaire au lieu d'une phrase.
        """
        self.ensure_one()
        cible = for_partner or self.partner_id
        retour = self._jour(self.date_return)
        herite = cible != self.partner_id
        # 🔴 Aucun accord en genre dans la phrase. Odoo ne porte AUCUN genre
        # sur une fiche contact, et le deviner d'après un prénom se trompe sur
        # de vraies personnes : « Prénom Nom est absent » était écrit tous les
        # jours dans le bandeau. La tournure nominale règle le problème au lieu
        # de le déplacer, et elle se lit mieux dans un bandeau.
        if self.nature == "closure" or (herite and self.partner_id.is_company):
            phrase = _("%(qui)s : fermeture jusqu'au %(fin)s, réouverture le %(retour)s.",
                       qui=self.partner_id.name,
                       fin=self._jour(self.date_to),
                       retour=retour)
        else:
            phrase = _("%(qui)s : absence jusqu'au %(fin)s, retour le %(retour)s.",
                       qui=self.partner_id.name,
                       fin=self._jour(self.date_to),
                       retour=retour)
        releve = self.backup_partner_id.name or self.backup_info
        if releve:
            phrase += " " + _("Relève : %s.") % releve
        return phrase

    VIDE = {"lines": [], "return_date": False}

    @api.model
    def _hint_for_partners(self, partner_ids, at_date=None):
        """La charge que les surfaces d'envoi affichent.

        Rend `{"lines": [...], "return_date": "AAAA-MM-JJ" | False}`.
        Jamais d'exception : une surface d'envoi ne se casse pas parce qu'un
        avertissement n'a pas pu se calculer.

        ⚠️ Rien pour un utilisateur EXTERNE. Savoir qui est en vacances chez
        nos clients est une information interne, et le portail n'a aucune
        raison de la lire, même sur sa propre fiche.
        """
        if self.env.user.share:
            return dict(self.VIDE)
        try:
            trouvees = self._for_partners(partner_ids, at_date=at_date)
        except Exception:  # noqa: BLE001 - un avertissement ne bloque rien
            _logger.exception("bf_contact_absence: calcul de l'avertissement")
            return dict(self.VIDE)
        lignes = []
        retour = False
        partners = self.env["res.partner"].browse(list(trouvees)).exists()
        par_id = {p.id: p for p in partners}
        for partner_id, absence in trouvees.items():
            partner = par_id.get(partner_id)
            if not partner:
                continue
            lignes.append(absence._phrase(for_partner=partner))
            if absence.date_return and (not retour or absence.date_return > retour):
                retour = absence.date_return
        # Un mur de phrases n'avertit plus personne : on en nomme trois et on
        # compte le reste.
        if len(lignes) > MAX_LIGNES_BANDEAU:
            reste = len(lignes) - MAX_LIGNES_BANDEAU
            lignes = lignes[:MAX_LIGNES_BANDEAU] + [
                _("Et %s autre(s) destinataire(s) absent(s).") % reste]
        return {
            "lines": lignes,
            "return_date": fields.Date.to_string(retour) if retour else False,
        }

    @api.model
    def hint_for_thread(self, res_model, res_id):
        """Même charge, mais pour le chatter d'une fiche.

        ⚠️ SEULE méthode publique du module, donc seule porte d'entrée RPC :
        le composeur OWL l'appelle par `orm.call`. Tout le reste est préfixé,
        pour que la surface appelable depuis `/web/dataset/call_kw` tienne en
        une ligne qu'on peut relire.

        Les destinataires d'un message de chatter sont ses abonnés ; sur une
        fiche contact, c'est le contact lui-même.
        """
        if self.env.user.share:
            return dict(self.VIDE)
        if not res_model or not res_id or res_model not in self.env:
            return dict(self.VIDE)
        # ⚠️ TOUT est sous garde : la lecture du fil peut lever une erreur
        # d'accès (les abonnés d'une fiche ne sont pas lisibles par tout le
        # monde), et un avertissement qui lève casse la surface qu'il devait
        # servir. Vérifié à l'audit du 2026-09-13.
        try:
            record = self.env[res_model].browse(int(res_id)).exists()
            if not record:
                return dict(self.VIDE)
            partner_ids = set()
            if res_model == "res.partner":
                partner_ids |= set(record.ids)
            if "message_partner_ids" in record._fields:
                # ⚠️ Les abonnés INTERNES ne sont pas des destinataires à
                # avertir : on ne prévient pas qu'un collègue est en vacances
                # parce qu'on écrit au client dans le chatter d'une tâche.
                partner_ids |= set(record.message_partner_ids.filtered(
                    lambda p: not p.user_ids or all(u.share for u in p.user_ids)
                ).ids)
            if "partner_id" in record._fields and record.partner_id:
                partner_ids |= set(record.partner_id.ids)
        except (TypeError, ValueError):
            return dict(self.VIDE)
        except AccessError:
            return dict(self.VIDE)
        return self._hint_for_partners(list(partner_ids))

    @api.model
    def _return_datetime(self, date_str):
        """Le matin du jour de reprise, en UTC, pour un envoi différé."""
        if not date_str:
            return False
        jour = fields.Date.to_date(date_str)
        heure = DEFAULT_RETURN_HOUR
        param = self.env["ir.config_parameter"].sudo().get_param(
            "bf_contact_absence.return_hour")
        # ⚠️ Un paramètre système VIDE rend la valeur par défaut, mais une
        # valeur illisible ne doit pas faire tomber l'envoi.
        if param:
            try:
                heure = max(0, min(23, int(param)))
            except (TypeError, ValueError):
                _logger.warning(
                    "bf_contact_absence.return_hour illisible : %r", param)
        # ⚠️ Odoo garde les datetimes en UTC naïf. « 9 h » veut dire 9 h CHEZ
        # L'UTILISATEUR : sans ce passage par son fuseau, un envoi reporté
        # partirait à 5 h du matin heure du Québec.
        fuseau = pytz.timezone(
            self.env.user.tz or self.env.context.get("tz") or "America/Toronto")
        local = fuseau.localize(datetime.combine(jour, time(hour=heure)))
        return fields.Datetime.to_string(
            local.astimezone(pytz.utc).replace(tzinfo=None))

    # ------------------------------------------------------------------
    # Entretien
    # ------------------------------------------------------------------
    @api.model
    def _cron_sync_reminders(self):
        """Rattrape les rappels des absences dont la fin a bougé."""
        today = fields.Date.context_today(self)
        self.search([
            ("reminder", "=", True),
            ("date_to", ">=", today - timedelta(days=2)),
        ])._sync_reminder()

    def action_end_now(self):
        """Le contact est rentré plus tôt : l'avertissement s'éteint AUJOURD'HUI.

        🔴 La première version posait `date_to = date_from` quand l'absence
        avait commencé le jour même, ce qui laissait la personne marquée absente
        pour la journée en cours. Le bouton dit « rentré », donc il rend la
        personne présente : une absence qui n'a pas encore eu de journée
        complète est archivée plutôt que raccourcie à zéro jour.
        """
        today = fields.Date.context_today(self)
        for absence in self:
            if absence.date_from >= today:
                absence.active = False
            else:
                absence.date_to = today - timedelta(days=1)
        return True
