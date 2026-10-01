"""AI enrichment of feedback verbatims through the AI bridge.

Sentiment, a short summary and themes are extracted from the comment by
the claude-chatbot-bridge service, reached over its local Unix socket; the
comment text is sent to the AI model behind the bridge. The transport lives in bf_ai_bridge,
which owns the single socket parameter. Every entry point is wrapped so
a bridge outage can never break the host flow: the record simply gets a
polite internal note.
"""
import json
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models

from odoo.addons.bf_cx.models.bf_cx_feedback import param_is_true

_logger = logging.getLogger(__name__)

_DEFAULT_BRIDGE_TIMEOUT = 120

# Tolerant mapping from whatever the model answers to our selection keys.
_SENTIMENT_MAP = {
    "positif": "positif",
    "positive": "positif",
    "neutre": "neutre",
    "neutral": "neutre",
    "negatif": "negatif",
    "négatif": "negatif",
    "negative": "negatif",
}



class BfCxFeedback(models.Model):
    _inherit = "bf.cx.feedback"

    sentiment = fields.Selection(
        [
            ("positif", "Positif"),
            ("neutre", "Neutre"),
            ("negatif", "Négatif"),
        ],
        string="Sentiment (IA)",
        readonly=True,
        copy=False,
        index=True,
        help="Sentiment du commentaire, déterminé par l'analyse IA. Le texte du "
             "commentaire est transmis au modèle d'IA par le pont du serveur.",
    )
    ai_summary = fields.Char(
        string="Résumé (IA)",
        readonly=True,
        copy=False,
        help="Résumé du commentaire en une phrase, produit par l'analyse IA.",
    )
    ai_analyzed = fields.Boolean(
        string="Analysé par l'IA",
        readonly=True,
        copy=False,
        help="Coché après une tentative d'analyse aboutie : empêche le "
             "traitement automatique de repasser sur le même feedback.",
    )

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_ai_analyze(self):
        """Manual analysis from the form button. Never raises: failures
        land as an internal chatter note plus a warning notification."""
        done = 0
        failed = 0
        for rec in self:
            if not (rec.comment or "").strip():
                continue
            try:
                ok = rec._bf_cx_ai_analyze_one()
            except Exception:  # noqa: BLE001 - never block the host flow
                _logger.exception(
                    "bf_cx_ai: analysis failed for feedback %s", rec.id
                )
                ok = False
            if ok:
                done += 1
            else:
                failed += 1
        if failed:
            message = _(
                "%(done)s analyse(s) réussie(s), %(failed)s en échec "
                "(détails au chatter).",
                done=done,
                failed=failed,
            )
            notif_type = "warning"
        else:
            message = _("%s analyse(s) réussie(s).") % done
            notif_type = "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": notif_type,
                "title": _("Analyse IA"),
                "message": message,
                "sticky": False,
            },
        }

    # ── Core ─────────────────────────────────────────────────────────────────

    def _bf_cx_ai_analyze_one(self):
        """Analyze this record's comment through the AI bridge.

        Returns True on success. A bridge outage or an unusable reply is
        reported as a polite internal note, never as an exception.
        """
        self.ensure_one()
        comment = (self.comment or "").strip()
        if not comment:
            return False
        icp = self.env["ir.config_parameter"].sudo()
        try:
            timeout = max(10, int(icp.get_param(
                "bf_cx.ai_bridge_timeout", str(_DEFAULT_BRIDGE_TIMEOUT)
            )))
        except (TypeError, ValueError):
            timeout = _DEFAULT_BRIDGE_TIMEOUT
        if not self.env["bf.ai.bridge"].available():
            self._bf_cx_ai_post_note(_(
                "Analyse IA non effectuée : le pont IA est "
                "indisponible (socket introuvable). Réessayer plus tard ou "
                "vérifier le service claude-chatbot-bridge."
            ))
            return False
        # Point dédié, sans outil (18.0.1.2.0) : /chat, qui a des outils
        # actifs, n'est plus jamais emprunté pour un verbatim client.
        payload = {
            "commentaire": comment[:4000],
            "themes": self._bf_cx_ai_theme_vocabulary(),
        }
        try:
            resp = self.env["bf.ai.bridge"].call("/cx/analyze", payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - bridge down must not block
            _logger.warning(
                "bf_cx_ai: bridge call failed for feedback %s: %s",
                self.id, exc,
            )
            self._bf_cx_ai_post_note(_(
                "Analyse IA non effectuée : le pont IA n'a pas "
                "répondu (%s)."
            ) % type(exc).__name__)
            return False
        data = self._bf_cx_ai_normalize(resp)
        # The attempt is consumed either way: the daily cron must not loop
        # forever on a reply the parser cannot use.
        vals = {"ai_analyzed": True}
        if not data or not (
            data["sentiment"] or data["summary"] or data["themes"]
        ):
            self.write(vals)
            self._bf_cx_ai_post_note(_(
                "Analyse IA : réponse illisible du pont IA, aucun "
                "champ mis à jour."
            ))
            return False
        if data["sentiment"]:
            vals["sentiment"] = data["sentiment"]
        if data["summary"]:
            vals["ai_summary"] = data["summary"][:250]
        self.write(vals)
        try:
            self._bf_cx_ai_link_themes(data["themes"])
        except Exception:  # noqa: BLE001 - themes are best-effort
            _logger.exception(
                "bf_cx_ai: theme linking failed for feedback %s", self.id
            )
        parts = []
        if data["sentiment"]:
            label = dict(self._fields["sentiment"].selection).get(
                data["sentiment"], data["sentiment"]
            )
            parts.append(_("sentiment : %s") % label)
        if data["themes"]:
            parts.append(_("thèmes : %s") % ", ".join(data["themes"]))
        if data["summary"]:
            parts.append(_("résumé : %s") % data["summary"][:250])
        self._bf_cx_ai_post_note(
            Markup("<p>%s</p>")
            % (_("Analyse IA terminée (%s).") % " ; ".join(parts))
        )
        return True

    @api.model
    def _bf_cx_ai_theme_vocabulary(self):
        """Thèmes connus, partagés avec l'assistance quand elle est installée.

        Les rétroactions et les billets parlent alors le même vocabulaire :
        « Délais » dans un sondage et « Délais » dans un billet se comptent
        ensemble, sans que l'un des modules dépende de l'autre.
        """
        names = []
        for model in ("bf.cx.theme", "helpdesk.theme"):
            if model in self.env:
                names += self.env[model].sudo().search([]).mapped("name")
        return sorted(set(names))[:200]

    @api.model
    def _bf_cx_ai_normalize(self, resp):
        """Réponse de /cx/analyze → dict normalisé, ou None.

        Le pont a déjà borné les valeurs ; on revérifie la forme, et on garde
        l'ancien analyseur de texte pour une réponse brute.
        """
        if not isinstance(resp, dict) or resp.get("error"):
            return None
        data = resp.get("data")
        if not isinstance(data, dict):
            return self._bf_cx_ai_parse(resp.get("response") or "")
        sentiment = _SENTIMENT_MAP.get(str(data.get("sentiment") or "").strip().lower())
        themes = [str(t).strip()[:60] for t in (data.get("themes") or []) if str(t).strip()][:5]
        return {"sentiment": sentiment, "themes": themes,
                "summary": str(data.get("summary") or "").strip()[:250]}

    @api.model
    def _bf_cx_ai_parse(self, text):
        """Defensively extract the strict-JSON analysis from a raw reply.

        Returns None when nothing JSON-shaped is found, otherwise a dict
        with normalized keys: sentiment (selection key or None), summary
        (str, possibly empty), themes (list of short str).
        """
        text = (text or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        sentiment = _SENTIMENT_MAP.get(
            str(data.get("sentiment") or "").strip().lower()
        )
        summary = data.get("summary")
        summary = summary.strip() if isinstance(summary, str) else ""
        themes = []
        raw_themes = data.get("themes")
        if isinstance(raw_themes, (list, tuple)):
            for item in raw_themes:
                if isinstance(item, str) and item.strip():
                    themes.append(item.strip()[:80])
        return {
            "sentiment": sentiment,
            "summary": summary,
            "themes": themes[:5],
        }

    def _bf_cx_ai_link_themes(self, names):
        """Attach returned themes when the core theme field exists.

        An older bf_cx has neither the theme_ids field nor the bf.cx.theme
        model: both are feature-detected, and the themes are skipped when
        they are absent.
        """
        self.ensure_one()
        if not names:
            return
        if "theme_ids" not in self._fields or "bf.cx.theme" not in self.env:
            return
        Theme = self.env["bf.cx.theme"].sudo()
        theme_ids = []
        for name in names:
            theme = Theme.search([("name", "=ilike", name)], limit=1)
            if not theme:
                theme = Theme.create({"name": name})
            theme_ids.append(theme.id)
        if theme_ids:
            self.write({"theme_ids": [(4, tid) for tid in theme_ids]})

    def _bf_cx_ai_post_note(self, body):
        """Internal chatter note: never an outgoing email, never raises."""
        try:
            self.message_post(
                body=body,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        except Exception:  # noqa: BLE001 - a note failure must not block
            _logger.exception(
                "bf_cx_ai: chatter note failed for feedback %s", self.ids
            )

    # ── Cron ─────────────────────────────────────────────────────────────────

    @api.model
    def _cron_bf_cx_ai_auto_analyze(self):
        """Daily opt-in batch: analyze recent unanalyzed verbatims.

        Capped at 20 records per run (bf_cx.ai_auto_analyze gates the
        whole thing, default OFF). The batch stops as soon as the bridge
        looks unreachable so unprocessed records are retried tomorrow.
        """
        if not param_is_true(self.env, "bf_cx.ai_auto_analyze", default=False):
            return
        since = fields.Datetime.now() - timedelta(days=30)
        records = self.search(
            [
                ("comment", "!=", False),
                ("ai_analyzed", "=", False),
                ("create_date", ">=", since),
            ],
            order="create_date desc",
            limit=20,
        )
        for rec in records:
            if not (rec.comment or "").strip():
                # Whitespace-only comment: nothing to analyze, close it so
                # the batch never stalls on it.
                rec.write({"ai_analyzed": True})
                continue
            try:
                rec._bf_cx_ai_analyze_one()
            except Exception:  # noqa: BLE001 - never break the cron
                _logger.exception(
                    "bf_cx_ai: cron analysis failed for feedback %s", rec.id
                )
            if not rec.ai_analyzed:
                # Still unmarked after the attempt: the bridge itself was
                # unreachable. Stop here, tomorrow's run will retry.
                _logger.info(
                    "bf_cx_ai: bridge unavailable, cron batch stopped at "
                    "feedback %s", rec.id,
                )
                break
