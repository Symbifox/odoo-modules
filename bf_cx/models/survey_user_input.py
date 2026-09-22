"""Survey ingestion.

survey.user_input._mark_done() is the canonical completion hook (called by
the public /survey/submit controller). It can fire more than once for the
same answer (time limit + one-page + live sessions), so ingestion takes a
row lock and checks for an existing feedback before creating one - same
serialization pattern as bf_survey_upload.

Runs as the public user on website submissions: everything goes through
sudo() and never raises back into the response flow.

Le renvoi d'invitation (action_bf_cx_resend_invite) vit ici et non sur la
vague : l'objet à viser est UNE réponse, donc UN destinataire. Les deux
boutons de la vague travaillent par lot et ne savent pas viser quelqu'un.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    bf_cx_wave_id = fields.Many2one(
        "bf.cx.wave",
        string="Vague d'expérience",
        ondelete="set null",
        copy=False,
        index=True,
    )

    # ── Renvoi d'invitation à UN destinataire ────────────────────────────

    def action_bf_cx_resend_invite(self):
        """Renvoyer l'invitation à ce destinataire-là, sur le MÊME lien.

        Le cas courant, et jusqu'ici sans porte d'entrée dans l'interface :
        quelqu'un a supprimé le courriel par erreur et redemande son lien.
        Les deux boutons de la vague travaillent par lot et ne savent pas
        viser une personne - « Envoyer un rappel » écrit à TOUS les
        non-répondants, et « Inviter les nouveaux destinataires » saute
        justement quiconque a déjà une réponse. Le geste manquant est ici,
        sur la ligne de la personne.

        **Le jeton ne change pas.** Créer une seconde réponse compterait
        l'invitation deux fois (``invited_count``), diluerait le taux de
        réponse de la vague et laisserait deux liens vivants pour la même
        personne - dont un qu'elle pourrait remplir deux fois. On rejoue
        donc le gabarit d'invitation sur la réponse existante.

        Ce qui n'est délibérément PAS touché :

        - ``bf_cx_last_solicited`` : un renvoi n'est pas une nouvelle
          sollicitation, c'est la même, re-livrée. La marquer repousserait
          la quarantaine de la personne pour un courriel qu'elle a
          elle-même redemandé.
        - ``wave.reminder_date`` : c'est le marqueur du rappel COLLECTIF.
          Le poser ici ferait sauter au cron le vrai rappel des autres
          non-répondants.
        - l'état de la vague, ses destinataires et ses compteurs.
        """
        now = fields.Datetime.now()
        sent = self.browse()
        for answer in self:
            wave = answer.bf_cx_wave_id
            if not wave:
                raise UserError(
                    _("Cette réponse n'appartient à aucune vague : il n'y a "
                      "pas d'invitation à renvoyer.")
                )
            # L'accès en écriture à la vague EST le droit de renvoyer. Les
            # droits sur survey.user_input sont ceux du module Sondages,
            # bien plus larges que ceux de l'Expérience client : sans cette
            # garde, un utilisateur de sondage sans aucun droit CX pourrait
            # déclencher un envoi client par `call_kw`.
            wave.check_access("write")
            if wave.state == "closed":
                raise UserError(
                    _("La vague « %s » est fermée. La rouvrir d'abord si le "
                      "sondage doit encore accepter des réponses.") % wave.name
                )
            template = wave.program_id.invite_template_id
            if not template:
                raise UserError(
                    _("Le programme « %s » n'a pas de gabarit d'invitation.")
                    % wave.program_id.name
                )
            if answer.state == "done":
                raise UserError(
                    _("%s a déjà répondu : renvoyer l'invitation ne ferait "
                      "que redemander un avis déjà donné.")
                    % (answer.partner_id.display_name or answer.email)
                )
            # Le sondage refuse les réponses passé la date limite : renvoyer
            # ici, c'est expédier un lien mort en promettant le contraire.
            deadline = answer.deadline or wave.deadline
            if deadline and deadline < now:
                raise UserError(
                    _("La date limite de réponse (%s) est passée : le lien "
                      "ne s'ouvrirait plus. Reporter la date limite de la "
                      "vague avant de renvoyer.")
                    % fields.Datetime.to_string(deadline)
                )
            partner = answer.partner_id
            if not partner or not partner.email:
                raise UserError(
                    _("Aucune adresse courriel sur ce destinataire.")
                )
            if not answer.test_entry:
                # Cadence volontairement hors jeu (``days=0``) : c'est la
                # même sollicitation qu'on re-livre, pas une deuxième.
                # Liste noire, « ne pas contacter » et recouvrement, eux,
                # restent opposables - ils disent « pas de courriel », pas
                # « pas trop souvent ».
                allowed, _blocked = partner._bf_cx_split_solicitable(days=0)
                if not allowed:
                    raise UserError(
                        _("Le renvoi à %s est bloqué par les garde-fous "
                          "d'envoi : compte exclu des sondages, liste noire "
                          "de courriel, « ne pas contacter », ou recouvrement "
                          "en cours. La cadence "
                          "anti-sursollicitation, elle, n'est pas en cause : "
                          "un renvoi ne compte pas comme une nouvelle "
                          "sollicitation.") % partner.display_name
                    )
            # force_send : sur un geste manuel, on veut l'échec SMTP tout de
            # suite plutôt qu'un courriel qui dort dans la file. Ciblé sur ce
            # seul courriel - jamais un vidage de la file, qui interbloque le
            # cron d'envoi.
            # `send_mail` rend un ID, pas un enregistrement - et sur un
            # envoi réussi le gabarit `auto_delete` l'a déjà supprimé.
            mail = self.env["mail.mail"].sudo().browse(
                template.send_mail(answer.id, force_send=True)
            )
            # `send()` n'ÉCHOUE pas quand la livraison échoue : il pose
            # `state='exception'` et rend la main. Sans ce contrôle, le
            # bouton annoncerait « renvoyée » en vert sur un courriel qui
            # n'est jamais parti - et l'opérateur cesserait de chercher.
            # Le gabarit porte `auto_delete` : une livraison réussie efface
            # le mail.mail, donc « il ne reste rien » vaut succès.
            if mail.exists() and mail.state != "sent":
                raise UserError(
                    _("Le courriel n'est pas parti (état « %(state)s »). "
                      "Rien n'a été écrit : %(reason)s",
                      state=mail.state,
                      reason=mail.failure_reason or _("aucune raison rapportée "
                                                      "par le serveur."))
                )
            wave.message_post(
                body=_(
                    "Invitation renvoyée à %(contact)s (%(email)s)%(test)s. "
                    "Même lien de réponse qu'à l'envoi initial ; ni la "
                    "quarantaine anti-sursollicitation, ni le rappel "
                    "collectif, ni les compteurs de la vague ne bougent.",
                    contact=partner.display_name,
                    email=partner.email,
                    test=(
                        _(" - entrée de test, elle ne compte dans aucune "
                          "statistique")
                        if answer.test_entry
                        else ""
                    ),
                )
            )
            sent |= answer
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Invitation renvoyée"),
                "message": _(
                    "%(count)d invitation(s) renvoyée(s) : %(who)s.",
                    count=len(sent),
                    who=", ".join(sent.partner_id.mapped("display_name")),
                ),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _bf_cx_self_submitted(self):
        """Did the invited person submit this answer through their own link?

        Three conditions, all needed for a ticked box to stand as consent:
        the answer belongs to a wave, its contact is one of that wave's
        recipients, and it was submitted either from the public token link
        (no session) or by that very contact's own user. Anything else - an
        answer created for a client from the back office - proves nothing
        about the client.
        """
        self.ensure_one()
        wave = self.bf_cx_wave_id
        if not wave or not self.partner_id or self.test_entry:
            return False
        if self.partner_id not in wave.sudo().partner_ids:
            return False
        user = self.env.user
        return user._is_public() or user.partner_id == self.partner_id

    def _mark_done(self):
        res = super()._mark_done()
        for user_input in self:
            try:
                user_input._bf_cx_ingest()
            except Exception:  # noqa: BLE001 - never break survey submission
                _logger.exception(
                    "bf_cx: ingestion failed for survey.user_input %s",
                    user_input.id,
                )
        return res

    def _bf_cx_resolve_program(self):
        """Program of this answer: explicit wave first, then survey match."""
        self.ensure_one()
        program = self.bf_cx_wave_id.program_id
        if program:
            return program
        return (
            self.env["bf.cx.program"]
            .sudo()
            .search([("survey_id", "=", self.survey_id.id)], limit=1)
        )

    def _bf_cx_ingest(self):
        self.ensure_one()
        if self.test_entry:
            return
        program = self._bf_cx_resolve_program()
        if not program:
            return

        Feedback = self.env["bf.cx.feedback"].sudo()
        # Serialize concurrent _mark_done calls on the same answer, then
        # dedupe: one feedback per survey answer.
        self.env.cr.execute(
            "SELECT id FROM survey_user_input WHERE id = %s FOR NO KEY UPDATE",
            (self.id,),
        )
        if Feedback.search_count([("survey_user_input_id", "=", self.id)]):
            return

        lines = self.user_input_line_ids.filtered(lambda l: not l.skipped)

        # Score: designated scale question, else first scale answer.
        scale_lines = lines.filtered(lambda l: l.answer_type == "scale")
        if program.score_question_id:
            scale_lines = scale_lines.filtered(
                lambda l: l.question_id == program.score_question_id
            )
        score_line = scale_lines[:1]

        # Comment: designated text question, else first free-text answer.
        text_lines = lines.filtered(
            lambda l: l.answer_type in ("text_box", "char_box")
        )
        if program.comment_question_id:
            designated = text_lines.filtered(
                lambda l: l.question_id == program.comment_question_id
            )
            text_lines = designated or text_lines
        comment_line = text_lines[:1]
        comment = ""
        if comment_line:
            comment = (
                comment_line.value_text_box
                if comment_line.answer_type == "text_box"
                else comment_line.value_char_box
            ) or ""

        # Testimonial opt-in: designated simple-choice answer selected.
        # Two "yes": contact me first, or quote me without contacting me.
        chosen = lines.filtered(
            lambda l: l.answer_type == "suggestion"
        ).suggested_answer_id
        direct_ticked = bool(
            program.testimonial_direct_answer_id
            and program.testimonial_direct_answer_id in chosen
        )
        testimonial_candidate = direct_ticked or bool(
            program.testimonial_answer_id
            and program.testimonial_answer_id in chosen
        )
        # The ticked box is only a consent if it came from the person it
        # names: an answer created for someone by an internal user, or
        # filled in from a back-office session, is at most a candidate.
        testimonial_direct = direct_ticked and self._bf_cx_self_submitted()

        kind = program._feedback_kind()
        if kind in ("nps", "csat") and not score_line:
            kind = "verbatim" if kind != "internal" else kind

        # Internal 360: the entry is filed under the person REVIEWED, and
        # the respondent is left out of the registry when the program asks
        # for it - lists, grouped views and exports are where a name
        # actually leaks, not the raw survey answer.
        is_internal = kind == "internal"
        hide_respondent = is_internal and program.hide_respondent
        vals = {
            "partner_id": False if hide_respondent else self.partner_id.id,
            "subject_user_id": (
                self.bf_cx_wave_id.subject_user_id.id
                if is_internal
                else False
            ),
            "date": fields.Date.context_today(self),
            "kind": kind,
            "source": "survey",
            "comment": comment,
            "program_id": program.id,
            "wave_id": self.bf_cx_wave_id.id,
            "survey_user_input_id": self.id,
            "company_id": program.company_id.id,
            "is_testimonial_candidate": testimonial_candidate,
            "testimonial_consent_direct": testimonial_direct,
        }
        if score_line:
            question = score_line.question_id
            vals["score"] = float(score_line.value_scale or 0)
            vals["score_max"] = float(question.scale_max or 10)
            # NPS buckets only make sense on 0-10 (enforced by constraint):
            # a program wired to a shorter scale degrades to CSAT.
            if vals["kind"] == "nps" and vals["score_max"] != 10:
                vals["kind"] = "csat"
        feedback = Feedback.create(vals)
        feedback._run_closed_loop()
        feedback._run_testimonial_candidate_loop()
        # Après la boucle fermée, pour que l'avis puisse dire qu'une activité
        # a été créée. Fermé par défaut : voir _notify_new_response.
        feedback._notify_new_response()
