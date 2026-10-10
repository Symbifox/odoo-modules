# -*- coding: utf-8 -*-
"""Une proposition de GenFox, rangée à côté de l'article et jamais à sa place.

Le module parent affiche en tête de son README qu'il ne réécrit rien. Brancher
une IA dessus ne change pas cette règle : elle la rend plus nécessaire. Une
proposition vit donc dans son propre enregistrement, se relit, et ne touche le
billet que si quelqu'un le demande.

⚠️ L'application écrit dans ``blog_post.content``, un champ ``html_translate``
stocké en jsonb. Un ``write`` ORM en contexte de langue étrangère y écrase le
créneau source. L'écriture passe donc par du SQL ``jsonb_set``, créneau par
créneau, et jamais par l'ORM.
"""

import hashlib
import json
import logging
import threading
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_ai_bridge.tools import transport

_logger = logging.getLogger(__name__)

#: Le point de terminaison du pont. Un seul, la nature du travail voyage dans
#: la charge utile plutôt que dans le chemin.
ENDPOINT = "/editorial-assist"

#: Au-delà, le pont a de toute façon rendu la main.
DEFAULT_TIMEOUT = 600

#: Au-delà de ce délai sans signal, une passe « en cours » est réputée perdue.
#: Le pont plafonne à 540 s et le fil détaché à 600 s : vingt minutes laissent
#: une marge confortable. Surchargeable par `bf_editorial_genfox.stale_minutes`.
STALE_MINUTES = 20


#: Repli quand une entrée n'a pas encore de créneaux de langue. Ce sont les
#: langues du blogue de Blue Fox, mais le module ne les impose pas : dès qu'une
#: entrée porte ses créneaux, ce sont eux qui font foi.
FALLBACK_SOURCE = "fr_CA"
FALLBACK_TARGET = "en_CA"

#: Le créneau que le site ne sert jamais. Il porte la source sur une instance
#: dont la langue d'installation est l'anglais.
SHADOW_SLOT = "en_US"


def digest(html):
    """Empreinte d'un contenu, pour refuser d'appliquer sur du texte qui a bougé."""
    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()


def lang_codes(entry):
    """Les codes de langue source et cible d'une entrée.

    Coder « fr_CA » en dur marchait sur le locataire de Blue Fox et nulle part
    ailleurs : sur une base où cette langue n'est pas active, la lecture lève
    « Invalid language code » et le message n'apprend rien à personne.
    """
    source = target = False
    for version in entry.version_ids if entry else []:
        code = version.lang_id.code
        if not code or code == SHADOW_SLOT:
            continue
        if version.is_source:
            source = code
        elif not target:
            target = code
    return source or FALLBACK_SOURCE, target or FALLBACK_TARGET


class EditorialSuggestion(models.Model):
    _name = "bf.editorial.suggestion"
    _description = "Gen suggestion"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(string="Title", compute="_compute_name", store=True)
    kind = fields.Selection(
        [
            ("propose", "Next article"),
            ("full", "Gen review"),
            # Conservés pour les enregistrements déjà en base (avant la
            # fusion) : « full » reprend les deux, et c'est le seul que le
            # bouton de l'entrée lance désormais.
            ("review", "Editorial review (legacy)"),
            ("expand", "Expand and align (legacy)"),
        ],
        string="Kind", required=True, index=True,
    )
    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entry", ondelete="cascade", index=True,
    )
    calendar_id = fields.Many2one(
        "bf.editorial.calendar", string="Calendar", ondelete="cascade",
        index=True,
    )
    state = fields.Selection(
        [
            ("queued", "In progress"),
            ("done", "Delivered"),
            ("error", "Failed"),
        ],
        string="Status", default="queued", required=True, index=True, tracking=True,
    )
    message = fields.Char(string="Bridge message", readonly=True)
    triggered_by = fields.Many2one(
        "res.users", string="Requested by", default=lambda self: self.env.user,
        readonly=True,
    )

    body_html = fields.Html(
        string="Gen's answer", sanitize=False, readonly=True,
        help="The reading, the findings or the recommendation. This text "
             "never goes into the article on its own.",
    )
    proposed_fr = fields.Html(
        string="Suggested text (French)", sanitize=False, readonly=True,
    )
    proposed_en = fields.Html(
        string="Suggested text (English)", sanitize=False, readonly=True,
    )
    has_proposal = fields.Boolean(
        string="Has text", compute="_compute_has_proposal",
    )
    pending_decision = fields.Boolean(
        string="Pending decision", compute="_compute_pending_decision",
        help="A delivered suggestion that carries text and that nobody "
             "has applied or discarded yet. Discarding deletes it, so "
             "this field only has to check \"delivered, has text, not "
             "applied\".",
    )

    source_digest = fields.Char(
        string="Source checksum", readonly=True,
        help="Checksum of the French slot when the suggestion was "
             "computed. If the article has changed since, the suggestion "
             "no longer applies: it would erase the work done in the "
             "meantime.",
    )
    backup_json = fields.Text(
        string="Slots before applying", readonly=True,
        help="The three slots as they were just before writing. This is "
             "the rollback.",
    )
    in_progress = fields.Boolean(
        string="Run in progress", compute="_compute_in_progress",
        help="True only in the window during which the run can still "
             "return a result.",
    )
    stalled = fields.Boolean(
        string="Stalled", compute="_compute_in_progress",
        help="Started, never came back. The bridge was restarted, the "
             "time limit passed, or the container was recreated.",
    )

    applied = fields.Boolean(string="Applied", readonly=True, copy=False)
    applied_by = fields.Many2one("res.users", string="Applied by", readonly=True)
    applied_date = fields.Datetime(string="Applied on", readonly=True)

    @api.depends("kind", "entry_id.name", "calendar_id.name", "create_date")
    def _compute_name(self):
        # Les libellés traduits, pas ceux de la source (anglaise) : le nom stocké
        # se lit dans la langue de qui crée la proposition.
        labels = dict(self._fields["kind"]._description_selection(self.env))
        for rec in self:
            cible = rec.entry_id.name or rec.calendar_id.name or _("no target")
            rec.name = "%s — %s" % (labels.get(rec.kind, rec.kind), cible)

    @api.depends("state", "create_date")
    def _compute_in_progress(self):
        """« En cours » ne vaut que dans la fenêtre utile.

        Sans cette borne, une passe tuée en vol (redémarrage du pont, plafond
        de temps, recréation du conteneur) laisserait l'état à « En cours »
        pour toujours, et les boutons cachés avec elle : l'entrée deviendrait
        impossible à relancer sans passer par la base. Vécu le 2026-08-28, le
        pont a été redémarré pendant la mise en service.
        """
        try:
            plafond = int(self.env["ir.config_parameter"].sudo().get_param(
                "bf_editorial_genfox.stale_minutes", STALE_MINUTES))
        except (TypeError, ValueError):
            plafond = STALE_MINUTES
        limite = fields.Datetime.now() - timedelta(minutes=plafond)
        for rec in self:
            en_cours = rec.state == "queued"
            frais = bool(rec.create_date and rec.create_date > limite)
            rec.in_progress = en_cours and frais
            rec.stalled = en_cours and not frais

    @api.depends("proposed_fr", "proposed_en")
    def _compute_has_proposal(self):
        for rec in self:
            rec.has_proposal = bool(rec.proposed_fr or rec.proposed_en)

    @api.depends("state", "has_proposal", "applied")
    def _compute_pending_decision(self):
        for rec in self:
            rec.pending_decision = (
                rec.state == "done" and rec.has_proposal and not rec.applied
            )

    # ── Lancement ────────────────────────────────────────────────────────
    @api.model
    def launch(self, kind, entry=None, calendar=None):
        """Créer la proposition et lancer le travail en arrière-plan.

        Le pont lance ``claude -p --dangerously-skip-permissions`` : le geste
        est réservé à la direction éditoriale, comme la publication.
        """
        if not self.env.user.has_group("bf_editorial.group_editorial_manager"):
            raise UserError(_(
                "Asking Gen is reserved to the \"Editorial management\" group."
            ))
        self.env["bf.ai.bridge"].check_available(_(
            "Gen therefore cannot be asked."
        ))
        if not calendar and entry:
            calendar = entry.calendar_id

        source = ""
        if entry and entry.post_id:
            source = entry._genfox_source_content()

        suggestion = self.create({
            "kind": kind,
            "entry_id": entry.id if entry else False,
            "calendar_id": calendar.id if calendar else False,
            "source_digest": digest(source) if source else False,
        })
        suggestion._dispatch()
        return suggestion

    def _dispatch(self):
        """Appeler le pont dans un fil détaché, comme le pré-remplissage d'OdJ.

        Une passe dure des minutes. La tenir dans la requête bloquerait un des
        deux travailleurs de la production pendant tout ce temps.
        """
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        timeout = int(ICP.get_param("bf_editorial_genfox.timeout", DEFAULT_TIMEOUT))
        # Capturé ici : le fil détaché survit à ce curseur.
        socket_path = self.env["bf.ai.bridge"].socket_path()
        payload = {
            "suggestion_id": self.id,
            "kind": self.kind,
            "entry_id": self.entry_id.id or 0,
            "calendar_id": self.calendar_id.id or 0,
            "tenant": "bf",
            "triggered_by": self.env.user.login,
        }
        db_name = self.env.cr.dbname
        uid = self.env.user.id
        suggestion_id = self.id

        def _run():
            from odoo import api as _api, registry as _registry
            try:
                resp = transport.post(socket_path, ENDPOINT, payload, timeout)
                status = resp.get("status", "?")
                msg = resp.get("message", "")
            except Exception as exc:  # noqa: BLE001 — le motif est reporté tel quel
                status, msg = "error", "%s: %s" % (type(exc).__name__, exc)

            with _registry(db_name).cursor() as cr:
                env = _api.Environment(cr, uid, {})
                rec = env["bf.editorial.suggestion"].browse(suggestion_id).exists()
                if not rec:
                    return
                # Le skill écrit lui-même body_html et l'état quand il aboutit.
                # On ne repasse ici que ce que le pont sait : l'échec, et le
                # cas où le skill n'a rien écrit du tout.
                if status != "ok":
                    rec.write({"state": "error", "message": (msg or status)[:250]})
                elif rec.state == "queued":
                    rec.write({
                        "state": "error",
                        "message": _("The bridge answered \"ok\" without "
                                     "delivering anything."),
                    })

        threading.Thread(target=_run, daemon=True).start()

    # ── Application ──────────────────────────────────────────────────────
    def action_apply(self):
        """Poser le texte proposé dans le billet, si rien n'a bougé depuis.

        L'écriture est du SQL ``jsonb_set`` créneau par créneau. Écrire les
        trois clés d'un bloc effacerait ce qu'un humain aurait corrigé entre le
        calcul et l'application, et un ``write`` ORM en langue étrangère
        écraserait le créneau source.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_editorial.group_editorial_manager"):
            raise UserError(_(
                "Applying a suggestion is reserved to the \"Editorial "
                "management\" group."
            ))
        if self.applied:
            raise UserError(_("This suggestion has already been applied."))
        if not self.has_proposal:
            raise UserError(_("This suggestion carries no text."))
        post = self.entry_id.post_id
        if not post:
            raise UserError(_(
                "The entry is not linked to any post: there is nowhere to "
                "write."
            ))

        code_source, code_cible = lang_codes(self.entry_id)
        actuel = self.entry_id._genfox_source_content()
        if self.source_digest and digest(actuel) != self.source_digest:
            raise UserError(_(
                "The article has changed since the suggestion was "
                "computed. Applying it would erase that work. Run "
                "\"Expand and align\" again on the current version."
            ))

        slots = self._read_slots(post.id)
        # Le retour arrière, pris avant la première écriture.
        self.write({"backup_json": json.dumps(slots, ensure_ascii=False)})

        ecrits = []
        if self.proposed_fr:
            self._set_slot(post.id, code_source, self.proposed_fr)
            ecrits.append(code_source)
            # Quand la source vit dans en_US (billet créé par XML-RPC dans ce
            # contexte), corriger le créneau source sans y toucher laisserait
            # la vraie source en arrière, et une traduction ultérieure
            # remapperait l'ancien terme.
            if code_source not in slots:
                self._set_slot(post.id, SHADOW_SLOT, self.proposed_fr)
                ecrits.append(SHADOW_SLOT)
        if self.proposed_en:
            self._set_slot(post.id, code_cible, self.proposed_en)
            ecrits.append(code_cible)

        post.invalidate_recordset(["content"])
        self.write({
            "applied": True,
            "applied_by": self.env.user.id,
            "applied_date": fields.Datetime.now(),
        })
        # Une QA verte d'avant l'écriture ne dit plus rien du texte actuel.
        # L'écriture étant en SQL, le crochet ORM de bf_editorial ne la voit pas.
        self.entry_id.write({"qa_state": "todo"})
        self.entry_id.action_sync_from_post()
        traduits = self._mark_written_versions_translated(ecrits)
        self.entry_id.message_post(body=Markup(_(
            "<p>Gen suggestion applied by %s. Slots written: "
            "%s.</p><p>The editorial QA must be run again. %s</p>"
        )) % (
            escape(self.env.user.name), escape(", ".join(ecrits)),
            _("Slot(s) moved to \"Translated\": %s. Human proofreading is "
              "still to be done: Gen wrote the text, nobody has read it "
              "yet.", ", ".join(traduits))
            if traduits else "",
        ))
        return True

    def _mark_written_versions_translated(self, lang_codes_written):
        """Faire dire l'état vrai : un créneau que GenFox vient d'écrire n'est
        plus « à traduire », qu'il ait été relu ou non.

        ⚠️ Volontairement PAS « Relue » : ça resterait un pas humain, et le
        confondre avec l'écriture du texte referait exactement le trou que la
        garde de pré-vol vient de fermer (18.0.1.5.0) — un créneau qui se dit
        prêt sans que personne ne l'ait lu. Un créneau déjà relu que GenFox
        réécrit retombe ici aussi : la relecture d'avant ne portait pas sur ce
        texte-là.

        Le créneau fantôme (``en_US``) n'a pas de fiche ``bf.editorial.version``
        et n'a donc rien à mettre à jour.
        """
        self.ensure_one()
        codes = {c for c in lang_codes_written if c != SHADOW_SLOT}
        if not codes:
            return []
        versions = self.entry_id.version_ids.filtered(
            lambda v: v.lang_code in codes
        )
        if not versions:
            return []
        versions.write({
            "state": "translated", "translated_date": fields.Date.today(),
        })
        return versions.mapped("lang_id.name")

    def _read_slots(self, post_id):
        """Les créneaux de langue du billet, tels qu'ils sont en base."""
        self.env.cr.execute(
            "SELECT content FROM blog_post WHERE id = %s", (post_id,))
        row = self.env.cr.fetchone()
        return dict(row[0] or {}) if row else {}

    def _set_slot(self, post_id, lang, html):
        """Poser un seul créneau, sans toucher aux autres clés du jsonb."""
        self.env.cr.execute(
            "UPDATE blog_post SET content = jsonb_set("
            "  COALESCE(content, '{}'::jsonb), %s, to_jsonb(%s::text), true"
            ") WHERE id = %s",
            ("{%s}" % lang, html, post_id),
        )

    def action_discard(self):
        """Écarter une proposition sans l'appliquer."""
        for rec in self:
            if rec.entry_id:
                rec.entry_id.message_post(body=_(
                    "Gen suggestion \"%s\" discarded.", rec.name,
                ))
        return self.unlink()

    def action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.editorial.suggestion",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
