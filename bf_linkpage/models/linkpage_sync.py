"""La réconciliation périodique : créer les pages manquantes, rafraîchir les autres.

Pourquoi une passe périodique plutôt qu'une surcharge de `hr.employee.create` :

- Le module ne dépend d'AUCUN fournisseur. Il vérifie le registre et se tait
  quand l'autre module est absent, exactement comme `_sources()`. Dépendre de
  `hr` pour un déclencheur ferait perdre cette propriété au module entier.
- Une passe rattrape les employés créés AVANT l'activation du réglage, ceux
  arrivés par import, et ceux dont la fiche a été complétée après coup. Un
  crochet à la création ne voit qu'un instant.
- Le rafraîchissement et la création sont le même geste : lire l'écart entre
  ce qui devrait être et ce qui est. Deux mécanismes distincts dériveraient.

Le prix, assumé : la page d'un nouvel employé apparaît au prochain passage, pas
à la seconde. Pour une page de liens destinée à une signature courriel, la
seconde n'a pas d'importance.
"""

import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)


class BfLinkpageSync(models.Model):
    _inherit = "bf.linkpage"

    # -- lecture des réglages ------------------------------------------------

    @api.model
    def _sync_settings(self):
        """Les réglages de la passe, tous avec un repli sûr.

        `get_param` rend False quand la clé est absente, ce qui se lit très mal
        en booléen implicite : une clé jamais posée doit valoir le défaut du
        module, pas « désactivé ».
        """
        get = self.env["ir.config_parameter"].sudo().get_param
        raw_state = get("bf_linkpage.autocreate_state") or "published"
        return {
            "autocreate": get("bf_linkpage.autocreate") in ("True", "true", "1"),
            "refresh": get("bf_linkpage.autorefresh") in ("True", "true", "1"),
            # Un état inconnu en base ne doit pas faire échouer la passe ni,
            # surtout, publier par accident quelque chose qu'on voulait cacher.
            "state": raw_state if raw_state in ("draft", "published") else "draft",
        }

    # -- qui a droit à une page ----------------------------------------------

    @api.model
    def _sync_employees(self):
        """Les employés qui méritent une page, ou False si `hr` est absent.

        On exige un contact ou un compte : sans l'un des deux, aucune source
        personnelle ne résoudrait et la page naîtrait vide.
        """
        Employee = self.env.get("hr.employee")
        if Employee is None:
            return False
        return self.env["hr.employee"].sudo().search([
            ("active", "=", True),
            "|", ("work_contact_id", "!=", False), ("user_id", "!=", False),
        ])

    @api.model
    def _sync_page_values(self, employee, settings):
        """Les valeurs de la page d'un employé."""
        partner = employee.work_contact_id or employee.user_id.partner_id
        template = self.env["bf.linkpage.template"]._for_user(employee.user_id) \
            if employee.user_id else self.env["bf.linkpage.template"].search(
                [("is_default", "=", True)], limit=1)
        return {
            "name": employee.name,
            # Le titre d'emploi est déjà saisi sur la fiche : le recopier à la
            # main sur chaque page serait une deuxième vérité à tenir à jour.
            "headline": employee.job_title or False,
            "slug": self._generate_slug(employee.name),
            "kind": "owner",
            "user_id": employee.user_id.id or False,
            "partner_id": partner.id or False,
            "template_id": template.id or False,
            "state": settings["state"],
        }

    # -- la passe ------------------------------------------------------------

    @api.model
    def _cron_sync_employees(self):
        """Créer les pages manquantes, rafraîchir celles qui suivent un gabarit.

        Idempotente par construction : une page est « manquante » quand aucune
        page rattachée à une personne ne pointe déjà l'employé, par son compte
        ou par son contact. Repasser ne crée donc jamais de doublon.
        """
        settings = self._sync_settings()
        if not settings["autocreate"] and not settings["refresh"]:
            return 0

        cree = rafraichi = 0

        if settings["autocreate"]:
            employees = self._sync_employees()
            if employees is False:
                _logger.info(
                    "bf_linkpage: création automatique demandée, mais hr n'est "
                    "pas installé ; rien à faire."
                )
            else:
                for employee in employees:
                    partner = employee.work_contact_id or employee.user_id.partner_id
                    domaine = [("kind", "=", "owner")]
                    if employee.user_id and partner:
                        domaine += ["|", ("user_id", "=", employee.user_id.id),
                                    ("partner_id", "=", partner.id)]
                    elif employee.user_id:
                        domaine += [("user_id", "=", employee.user_id.id)]
                    else:
                        domaine += [("partner_id", "=", partner.id)]
                    if self.with_context(active_test=False).search_count(domaine):
                        continue
                    page = self.create(self._sync_page_values(employee, settings))
                    cree += 1
                    _logger.info(
                        "bf_linkpage: page %s créée pour %s (état %s).",
                        page.slug, employee.name, page.state,
                    )

        if settings["refresh"]:
            # `_apply_template` remplace les liens VENUS d'un gabarit et laisse
            # ceux ajoutés à la main. C'est ce qui rend un rafraîchissement
            # périodique acceptable : il ne peut pas effacer le travail de
            # quelqu'un sur sa propre page.
            pages = self.search([("template_id", "!=", False)])
            pages._apply_template()
            rafraichi = len(pages)

        _logger.info(
            "bf_linkpage: passe terminée, %s page(s) créée(s), %s rafraîchie(s).",
            cree, rafraichi,
        )
        return cree + rafraichi

    @api.model
    def action_sync_now(self):
        """Le bouton des réglages : la même passe, déclenchée à la main."""
        total = self._cron_sync_employees()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if total else "warning",
                "sticky": False,
                "message": _(
                    "Passe terminée : %s page(s) touchée(s).", total
                ) if total else _(
                    "Rien à faire. Vérifiez que la création ou le "
                    "rafraîchissement est activé ci-dessus."
                ),
            },
        }
