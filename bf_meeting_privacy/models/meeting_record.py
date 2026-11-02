import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Les types de source qui signent une captation. Un compte rendu saisi à la
# main n'a jamais été enregistré, et le régime de l'enregistrement ne lui
# est pas opposable.
_RECORDED_SOURCES = ("audio", "talk_recording")


class MeetingRecord(models.Model):
    """Rattache le compte rendu au dossier Loi 25 : consentement à l'écran,
    purge réelle de la matière première, inscription au registre."""

    _inherit = "meeting.record"

    # ------------------------------------------------------------------
    # Consentement — l'indicateur, pas la police
    # ------------------------------------------------------------------

    privacy_is_recorded = fields.Boolean(
        string="Rencontre captée",
        compute="_compute_privacy_consent",
        help="Vrai lorsque la rencontre porte une transcription ou provient "
             "d'un enregistrement : c'est là que le régime du consentement "
             "à l'enregistrement s'applique.",
    )
    privacy_external_partner_ids = fields.Many2many(
        "res.partner",
        "bf_meeting_privacy_external_rel",
        "meeting_id",
        "partner_id",
        string="Participants externes",
        compute="_compute_privacy_consent",
        help="Participants qui ne sont pas des utilisateurs internes. Le "
             "consentement à l'enregistrement les concerne ; l'équipe, elle, "
             "relève du lien d'emploi et du mandat.",
    )
    privacy_missing_consent_partner_ids = fields.Many2many(
        "res.partner",
        "bf_meeting_privacy_missing_rel",
        "meeting_id",
        "partner_id",
        string="Sans consentement d'enregistrement",
        compute="_compute_privacy_consent",
    )
    privacy_consent_state = fields.Selection(
        [
            ("na", "Sans objet"),
            ("ok", "Couvert"),
            ("missing", "Consentement manquant"),
        ],
        string="Consentement à l'enregistrement",
        compute="_compute_privacy_consent",
        help="⚠️ Indicateur de lecture, calculé à l'affichage. Il rapporte "
             "l'état du dossier de consentement au moment où on le regarde, "
             "pas celui qui prévalait le jour de la rencontre.",
    )
    privacy_raw_material_purged = fields.Boolean(
        string="Matière première purgée",
        compute="_compute_privacy_raw_material_purged",
        help="Vrai lorsque la transcription brute a été effacée du compte "
             "rendu alors que la rencontre provenait d'une captation.",
    )

    @api.depends("verbatim", "verbatim_html", "source_type",
                 "attendance_ids.partner_id", "participant_ids")
    def _compute_privacy_consent(self):
        """Nomme les participants externes sans consentement d'enregistrement.

        ⚠️ Champ NON stocké, donc invisible aux domaines de recherche : on ne
        peut pas filtrer une liste dessus. C'est délibéré — le consentement
        change dans le temps, et un état gelé à la création du compte rendu
        vieillirait mal.

        On réutilise `has_recording_consent` de `privacy_consent` plutôt que
        de refaire la lecture : un seul endroit décide de ce qui compte comme
        consentement accordé.
        """
        for record in self:
            recorded = bool(record.verbatim or record.verbatim_html) or (
                record.source_type in _RECORDED_SOURCES
            )
            record.privacy_is_recorded = recorded

            partners = record.attendance_ids.mapped("partner_id") | record.participant_ids
            external = partners.filtered(lambda p: not p._bf_privacy_is_internal())
            record.privacy_external_partner_ids = external

            # `sudo()` : les ACL de `privacy.consent` sont réservées aux
            # groupes vie privée, et `has_recording_consent` calcule sur
            # `partner.consent_ids`. Sans lui, un usager « Rencontres » hors
            # de ces groupes ne peut plus OUVRIR un compte rendu — `web_read`
            # charge tous les champs de l'arch. Vérifié, pas déduit.
            missing = external.filtered(
                lambda p: not p.sudo().has_recording_consent)
            record.privacy_missing_consent_partner_ids = missing

            if not recorded or not external:
                record.privacy_consent_state = "na"
            elif missing:
                record.privacy_consent_state = "missing"
            else:
                record.privacy_consent_state = "ok"

    @api.depends("verbatim", "verbatim_html", "source_type")
    def _compute_privacy_raw_material_purged(self):
        for record in self:
            record.privacy_raw_material_purged = (
                record.source_type in _RECORDED_SOURCES
                and not record.verbatim
                and not record.verbatim_html
            )

    # ------------------------------------------------------------------
    # Purge de la matière première
    # ------------------------------------------------------------------

    def action_privacy_purge_raw_material(self):
        """Bouton : purger la transcription brute de ces comptes rendus."""
        if not self.env.su and not self.env.user.has_group(
            "privacy_consent.group_privacy_officer"
        ):
            raise UserError(
                _("Seul le responsable de la vie privée peut purger une "
                  "transcription brute.")
            )
        purged = self.env["meeting.record"]
        for record in self:
            if record._privacy_purge_raw_material(
                reason=_("purge manuelle par le responsable de la vie privée")
            ):
                purged |= record
        if not purged:
            raise UserError(
                _("Aucune transcription brute à purger : ces comptes rendus "
                  "n'en portent plus.")
            )
        return True

    @api.model
    def _cron_privacy_purge_raw_material(self, limit=200):
        """Applique la règle RENC-002 aux comptes rendus échus.

        🔴 **Livré DÉSACTIVÉ.** Sur une base déjà remplie, la quasi-totalité
        des comptes rendus portent une transcription brute, dont certaines
        vieilles de plus d'un an : allumer ce cron effacerait des verbatims de
        façon irréversible dès que le premier atteint sa durée. C'est une décision du
        responsable de la vie privée, pas un effet de bord d'installation.

        ⚠️ La durée n'est pas écrite ici : elle est LUE sur la règle RENC-002.
        Changer la règle change le comportement, ce qui est le but d'un
        calendrier de conservation. Une durée en dur dans le code aurait fait
        mentir la règle affichée à l'écran.

        ⚠️ Ne vise que les comptes rendus TRANSMIS. La fin de la transcription
        brute est accomplie quand le compte rendu est rédigé et envoyé ;
        effacer la matière première d'un brouillon retirerait à quelqu'un le
        moyen de le finir.
        """
        calendar = self.env.ref(
            "bf_meeting_privacy.retention_meeting_raw_material",
            raise_if_not_found=False,
        )
        if not calendar:
            _logger.warning(
                "bf_meeting_privacy: règle RENC-002 introuvable, purge annulée"
            )
            return False
        days = calendar.total_retention_days
        if days <= 0:
            _logger.warning(
                "bf_meeting_privacy: RENC-002 est à %s jour(s) — purge annulée "
                "plutôt que d'effacer les transcriptions du jour même", days,
            )
            return False

        cutoff = fields.Datetime.now() - timedelta(days=days)
        due = self.search([
            ("date", "<=", fields.Datetime.to_string(cutoff)),
            ("source_type", "in", list(_RECORDED_SOURCES)),
            ("report_state", "=", "sent"),
            "|", ("verbatim", "!=", False), ("verbatim_html", "!=", False),
        ], limit=limit, order="date asc")

        purged = 0
        for record in due:
            if record._privacy_purge_raw_material(
                reason=_("règle de conservation RENC-002 (%s jours)", days)
            ):
                purged += 1
        if purged:
            _logger.info(
                "bf_meeting_privacy: %s transcription(s) brute(s) purgée(s) "
                "au titre de RENC-002", purged,
            )
        return purged

    def _privacy_purge_raw_material(self, reason=None):
        """Efface la transcription brute et consigne ce qui a disparu.

        Rend True quand quelque chose a été effacé, False quand il n'y avait
        rien à effacer. ⚠️ Rendre True dans le second cas ferait écrire au
        registre immuable une entrée pour une destruction qui n'a pas eu lieu.

        ⚠️ L'état est figé AVANT l'écriture. Après coup, plus rien ne
        distingue un compte rendu qu'on vient de purger d'un compte rendu qui
        n'a jamais porté de transcription. C'est le travers corrigé dans le
        certificat de destruction en 18.0.4.10.0, et repris tel quel de
        `bf_securetransfer_privacy`.

        ⚠️ Ce qui n'est PAS détruit : le fichier audio au bout de
        `source_nc_path`. Il vit sur Nextcloud, Odoo n'a pas la main dessus.
        Le chemin est donc CONSERVÉ — l'effacer retirerait le seul moyen
        d'aller supprimer le fichier — et l'entrée de registre le nomme comme
        matière encore détenue hors d'Odoo.
        """
        self.ensure_one()
        had_verbatim = bool(self.verbatim)
        had_html = bool(self.verbatim_html)
        if not (had_verbatim or had_html):
            return False

        # On fige des longueurs, pas le contenu : le registre décrit ce qui a
        # disparu sans en garder une copie, ce qui reconstituerait le verbatim
        # dans un modèle qu'on ne peut plus ni modifier ni supprimer.
        chars = len(self.verbatim or "") or len(self.verbatim_html or "")
        subject_count = len(self.attendance_ids.mapped("partner_id")
                            | self.participant_ids)
        nc_path = self.source_nc_path or ""
        source_file = self.source_filename or ""

        self.write({"verbatim": False, "verbatim_html": False})

        try:
            self._privacy_register_purge(
                chars=chars,
                had_verbatim=had_verbatim,
                had_html=had_html,
                subject_count=subject_count,
                nc_path=nc_path,
                source_file=source_file,
                reason=reason,
            )
        except Exception:  # noqa: BLE001 — consigner ne doit jamais annuler la purge
            _logger.exception(
                "bf_meeting_privacy: inscription au registre en échec pour "
                "le compte rendu %s (la purge, elle, a eu lieu)", self.name,
            )
        return True

    def _privacy_register_purge(self, chars=0, had_verbatim=False, had_html=False,
                                subject_count=0, nc_path="", source_file="",
                                reason=None):
        """Écrit au registre immuable ce que la purge vient d'effacer."""
        self.ensure_one()
        Register = self.env["privacy.destruction.register"].sudo()
        user = self.env.user

        effaced = []
        if had_verbatim:
            effaced.append(_("transcription brute"))
        if had_html:
            effaced.append(_("transcription mise en forme"))

        description = _(
            "Compte rendu « %(meeting)s » (%(date)s) — %(what)s effacée(s) "
            "du compte rendu, soit environ %(chars)s caractères.\n"
            "Le compte rendu lui-même — résumé, sujets, décisions, présences — "
            "est CONSERVÉ : il documente le mandat et relève de la règle "
            "RENC-001.",
            meeting=self.name or "",
            date=self.date and fields.Date.to_string(self.date.date()) or _("sans date"),
            what=" et ".join(effaced),
            chars=chars,
        )
        if reason:
            description += _("\nMotif : %s", reason)
        if nc_path or source_file:
            description += _(
                "\n⚠️ Matière encore détenue HORS d'Odoo, non visée par cette "
                "entrée : fichier « %(file)s » au chemin Nextcloud "
                "« %(path)s ». Sa destruction se fait sur le stockage, pas "
                "ici, et n'est pas certifiée par la présente.",
                file=source_file or _("(inconnu)"),
                path=nc_path or _("(non renseigné)"),
            )

        vals = {
            "destruction_date": fields.Datetime.now(),
            "destroyed_by_id": user.id,
            "approved_by_id": user.id,
            "res_model": self._name,
            "res_id": self.id,
            "res_name": self.name,
            "document_description": description,
            "pi_categories": _(
                "Propos tenus en rencontre, attribués à des personnes nommées "
                "(identification). Contenu non classifié : une transcription "
                "porte ce que les personnes ont choisi de dire."
            ),
            "subject_count": subject_count,
            # `delete` et non `secure_wipe` : on écrase un champ dans PostgreSQL.
            # Les blocs de la table, ses copies de sauvegarde et le journal
            # d'écriture gardent la valeur d'avant. Déclarer un effacement
            # sécurisé serait un surclaim.
            "destruction_method": "delete",
            # 🔴 Portée PARTIELLE — c'est TOUT l'objet de la règle RENC-002 :
            # la matière première s'en va, le compte rendu reste debout. Le
            # socle n'avait aucune place pour dire cela avant la 18.0.5.0.0, et
            # sa garde aurait pris cette entrée pour un faux positif : le
            # verbatim aurait été détruit et l'entrée perdue en silence, cet
            # appel étant enveloppé d'un try/except qui ne relève pas.
            "destruction_scope": "partial",
            "res_field": ", ".join(
                f for f in ("verbatim" if had_verbatim else "",
                            "verbatim_html" if had_html else "") if f
            ),
            "legal_basis": _(
                "Art. 23 LPRPSP : le renseignement personnel est détruit "
                "lorsque la fin pour laquelle il a été recueilli est accomplie. "
                "La fin de la transcription brute est accomplie lorsque le "
                "compte rendu est rédigé et transmis."
            ),
            "company_id": self.company_id.id or self.env.company.id,
        }
        calendar = self.env.ref(
            "bf_meeting_privacy.retention_meeting_raw_material",
            raise_if_not_found=False,
        )
        if calendar:
            vals["retention_calendar_id"] = calendar.id
        return Register.create(vals)
