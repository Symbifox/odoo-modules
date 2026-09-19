# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""The things that are still wrong the morning after a migration.

The import tool prints a list of next steps and then exits, which means the
list survives exactly as long as the terminal scrollback. These are the same
checks, run against the live instance, each one answering for itself.

A check reports three outcomes and they are not the same thing. `ok` means it
looked and found nothing to do. `warn` means it looked and found something.
`na` means it could not look -- the module it needs is not installed, the
table is not there -- which is worth seeing rather than counting as a pass.
"""

import logging
import os

import pytz

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

#: Tables whose id sequence is worth checking. A sequence left behind its own
#: max(id) is silent until the next insert, which then fails on a duplicate
#: key -- often days later, in front of a user.
SEQUENCE_TABLES = (
    "res_partner", "res_users", "account_move", "account_move_line",
    "account_journal", "account_account", "crm_lead", "project_project",
    "project_task", "sale_order", "sale_order_line", "account_analytic_line",
    "mail_message", "ir_attachment", "product_template", "product_product",
)


class BfOe2ocCheck(models.Model):
    _name = "bf.oe2oc.check"
    _description = "Contrôle d'après-migration"
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True, translate=True)
    key = fields.Char(required=True, index=True,
                      help="Identifiant du contrôle automatique, le cas échéant.")
    sequence = fields.Integer(default=10)
    category = fields.Selection(
        [("data", "Données"), ("config", "Configuration"),
         ("mail", "Courriel"), ("manual", "À faire à la main")],
        default="config", required=True, string="Catégorie")
    description = fields.Text(translate=True, string="Ce qu'il vérifie")
    automatic = fields.Boolean(
        default=True, string="Automatique",
        help="Décoché, le contrôle ne se coche qu'à la main.")

    state = fields.Selection(
        [("todo", "À vérifier"), ("ok", "Rien à faire"), ("warn", "À corriger"),
         ("na", "Non vérifiable"), ("done", "Réglé")],
        default="todo", required=True, string="État")
    result = fields.Text(string="Constat", readonly=True)
    checked_on = fields.Datetime(string="Vérifié le", readonly=True)

    _sql_constraints = [("key_uniq", "unique(key)",
                         "Deux contrôles ne peuvent pas porter la même clé.")]

    # ── Exécution ─────────────────────────────────────────────────────────

    def action_run(self):
        for check in self:
            check._run()
        return True

    @api.model
    def action_run_all(self):
        checks = self.search([("automatic", "=", True)])
        checks.action_run()
        return {
            "type": "ir.actions.act_window",
            "name": _("Contrôles d'après-migration"),
            "res_model": "bf.oe2oc.check",
            "view_mode": "list,form",
        }

    def action_mark_done(self):
        self.write({"state": "done", "checked_on": fields.Datetime.now()})
        return True

    @api.private
    def _run(self):
        self.ensure_one()
        if not self.automatic:
            return
        runner = getattr(self, f"_check_{self.key}", None)
        if runner is None:
            self.write({"state": "na", "checked_on": fields.Datetime.now(),
                        "result": _("Aucun contrôle automatique sous cette clé.")})
            return
        # Chaque contrôle dans son propre point de reprise. Une erreur SQL
        # avorte le curseur : sans savepoint, un contrôle qui tombe emporte
        # tous les suivants, et l'écriture du constat elle-même.
        try:
            with self.env.cr.savepoint():
                state, result = runner()
        except Exception as exc:  # noqa: BLE001 - surfaced, never silent
            _logger.warning("bf_oe2oc: contrôle %s en erreur : %s", self.key, exc)
            state, result = "na", _("Le contrôle a levé : %s",
                                    str(exc).splitlines()[0][:300])
        self.write({"state": state, "result": result,
                    "checked_on": fields.Datetime.now()})

    # ── Les contrôles ─────────────────────────────────────────────────────

    @api.private
    def _check_languages(self):
        """A language used by the imported data but left inactive shows raw keys."""
        langs = self.env["res.lang"].with_context(active_test=False).search([])
        # `res.users.lang` n'est pas une colonne : elle est héritée du
        # partenaire. Grouper sur res.partner couvre donc aussi les comptes,
        # et passe par l'ORM plutôt que par un nom de table supposé.
        groups = self.env["res.partner"].with_context(active_test=False)._read_group(
            [("lang", "!=", False)], groupby=["lang"])
        used = {row[0] for row in groups if row and row[0]}
        missing = sorted(used - set(langs.filtered("active").mapped("code")))
        if not missing:
            return "ok", _("Toutes les langues utilisées par les contacts et les "
                           "comptes sont actives.")
        return "warn", _(
            "Langues employées par des fiches mais inactives : %(codes)s. "
            "Activez-les dans Paramètres › Traductions › Langues, sinon les "
            "champs traduits s'affichent dans la langue de repli.",
            codes=", ".join(missing))

    @api.private
    def _check_invalid_locales(self):
        """A code that is not merely inactive but *invalid* breaks everything.

        An inactive language costs a fallback translation. A language or
        timezone code that does not exist at all makes Odoo raise on any call
        that resolves it -- `Invalid language code`, or a KeyError out of
        pytz -- and the trace names the code, not the record carrying it. It
        survives a migration whenever the source had a value the target does
        not know.
        """
        known_langs = set(self.env["res.lang"].with_context(
            active_test=False).search([]).mapped("code"))
        bad = []
        # `lang` et `tz` vivent sur le partenaire ; res.users les hérite et
        # n'a pas de colonne à lire. Interroger le partenaire couvre les deux.
        for model, field, valid in (
                ("res.partner", "lang", known_langs),
                ("res.partner", "tz", set(pytz.all_timezones)),
        ):
            if model not in self.env or field not in self.env[model]._fields:
                continue
            try:
                groups = self.env[model].sudo().with_context(
                    active_test=False)._read_group(
                        [(field, "!=", False)], groupby=[field])
            except Exception:  # noqa: BLE001 - champ non stocké, rien à lire
                continue
            offenders = sorted(
                {row[0] for row in groups if row and row[0] and row[0] not in valid})
            if offenders:
                shown = ", ".join(offenders[:5])
                more = f" (+{len(offenders) - 5})" if len(offenders) > 5 else ""
                bad.append(f"{model}.{field} : {shown}{more}")
        if not bad:
            return "ok", _("Les codes de langue et de fuseau portés par les "
                           "fiches existent tous.")
        return "warn", _(
            "Codes inexistants trouvés : %(list)s. Ce n'est pas cosmétique : "
            "Odoo lève sur tout appel qui les résout, et la trace nomme le "
            "code, pas la fiche. À vider ou à corriger avant la mise en "
            "service.", list=" ; ".join(bad))

    @api.private
    def _check_base_url(self):
        """The tool neutralizes web.base.url; nobody remembers to set it back."""
        param = self.env["ir.config_parameter"].sudo()
        url = param.get_param("web.base.url") or ""
        if not url:
            return "warn", _("web.base.url n'est pas défini.")
        if "localhost" in url or "127.0.0.1" in url:
            return "warn", _(
                "web.base.url vaut encore « %(url)s », la valeur de "
                "neutralisation. Tout lien envoyé par courriel pointera vers "
                "la machine du destinataire.", url=url)
        frozen = param.get_param("web.base.url.freeze")
        if frozen in ("True", "true", "1"):
            return "warn", _(
                "web.base.url vaut « %(url)s » mais il est gelé "
                "(web.base.url.freeze). Il ne suivra pas un changement de "
                "domaine.", url=url)
        return "ok", _("web.base.url vaut « %s ».", url)

    @api.private
    def _check_crons(self):
        """Neutralization stops the schedulers. Restarting them is a decision."""
        crons = self.env["ir.cron"].with_context(active_test=False).search([])
        if not crons:
            return "na", _("Aucune action planifiée dans cette base.")
        off = crons.filtered(lambda c: not c.active)
        if not off:
            if len(crons) == 1:
                return "ok", _("L'unique action planifiée est active.")
            return "ok", _("Les %s actions planifiées sont actives.", len(crons))
        return "warn", _(
            "%(off)s actions planifiées sur %(total)s sont arrêtées. C'est "
            "l'état voulu juste après une migration ; ce ne l'est plus une "
            "fois la base en service.", off=len(off), total=len(crons))

    @api.private
    def _check_sequences(self):
        """A sequence behind its own max(id) fails on the next insert."""
        behind = []
        for table in SEQUENCE_TABLES:
            self.env.cr.execute(
                "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
            if not self.env.cr.fetchone()[0]:
                continue
            self.env.cr.execute(
                "SELECT pg_get_serial_sequence(%s, 'id')", (f"public.{table}",))
            seq = self.env.cr.fetchone()[0]
            if not seq:
                continue
            self.env.cr.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"')
            max_id = self.env.cr.fetchone()[0]
            self.env.cr.execute("SELECT last_value, is_called FROM %s" % seq)
            last_value, is_called = self.env.cr.fetchone()
            current = last_value if is_called else last_value - 1
            if current < max_id:
                behind.append(f"{table} ({current} < {max_id})")
        if not behind:
            return "ok", _("Les séquences contrôlées sont au-delà du plus grand "
                           "identifiant de leur table.")
        return "warn", _(
            "Séquences en retard sur leur table : %(list)s. La prochaine "
            "création y lèvera une erreur de clé dupliquée.",
            list=", ".join(behind))

    @api.private
    def _check_attachments(self):
        """An attachment whose file never made the trip reads as a broken link."""
        Attachment = self.env["ir.attachment"].sudo()
        sample = Attachment.search(
            [("store_fname", "!=", False)], limit=50, order="id desc")
        if not sample:
            return "na", _("Aucune pièce jointe stockée sur disque à contrôler.")
        # Regarder le fichier plutôt que de lire `datas` : la lecture lève, et
        # Odoo journalise la trace complète à chaque pièce manquante. Un
        # contrôle qui remplit le journal de traces pour dire « il en manque
        # onze » se fait couper par la personne qui le lit.
        missing = 0
        for attachment in sample:
            try:
                path = Attachment._full_path(attachment.store_fname)
            except Exception:  # noqa: BLE001 - chemin illisible = pièce perdue
                missing += 1
                continue
            if not os.path.exists(path):
                missing += 1
        if not missing:
            if len(sample) == 1:
                return "ok", _("La pièce jointe échantillonnée est lisible.")
            return "ok", _("Les %s pièces jointes échantillonnées sont lisibles.",
                           len(sample))
        return "warn", _(
            "%(missing)s des %(total)s pièces jointes échantillonnées sont "
            "introuvables sur disque. Le filestore n'a probablement pas été "
            "copié au bon endroit : il va sous "
            "<data_dir>/filestore/<nom_de_la_base>.",
            missing=missing, total=len(sample))

    @api.private
    def _check_mail_servers(self):
        """Nothing should leave a freshly migrated database by accident."""
        servers = self.env["ir.mail_server"].sudo().with_context(
            active_test=False).search([])
        if not servers:
            return "ok", _("Aucun serveur d'envoi n'est configuré : rien ne "
                           "peut sortir de cette base.")
        active = servers.filtered("active")
        if not active:
            # Un msgid par forme : « Les 1 serveurs » se lit dans un produit
            # vendu en français, et aucun essai ne le voit.
            if len(servers) == 1:
                return "ok", _("Le serveur d'envoi est désactivé.")
            return "ok", _("Les %s serveurs d'envoi sont désactivés.", len(servers))
        if len(active) == 1:
            return "warn", _(
                "Un serveur d'envoi est actif. Sur une base fraîchement "
                "migrée, les files de courriels reprises peuvent partir vers "
                "de vrais destinataires dès le premier passage du "
                "planificateur.")
        return "warn", _(
            "%(n)s serveurs d'envoi sont actifs. Sur une base fraîchement "
            "migrée, les files de courriels reprises peuvent partir vers de "
            "vrais destinataires dès le premier passage du planificateur.",
            n=len(active))

    @api.private
    def _check_leftovers(self):
        """Did anyone actually deal with the Enterprise data set aside?"""
        Bundle = self.env["bf.oe2oc.bundle"]
        bundles = Bundle.search([])
        if not bundles:
            return "warn", _(
                "Aucun fichier de reprise n'a été déposé. Si la migration a "
                "laissé des données Enterprise de côté, elles sont dans "
                "« enterprise-leftovers.json », à côté du dump.")
        pending = sum(
            1 for b in bundles for t in b.table_ids
            if t.target_model and t.state != "rehomed")
        if pending:
            if pending == 1:
                return "warn", _(
                    "Une table reprise a une correspondance mais n'a pas "
                    "encore été relogée.")
            return "warn", _(
                "%s tables reprises ont une correspondance mais n'ont pas "
                "encore été relogées.", pending)
        kept = sum(1 for b in bundles for t in b.table_ids if not t.target_model)
        if kept:
            if kept == 1:
                return "ok", _(
                    "Tout ce qui pouvait être relogé l'a été. Une table reste "
                    "conservée telle quelle, faute de modèle d'arrivée.")
            return "ok", _(
                "Tout ce qui pouvait être relogé l'a été. %s tables restent "
                "conservées telles quelles, faute de modèle d'arrivée.", kept)
        return "ok", _("Toutes les données reprises ont été relogées.")
