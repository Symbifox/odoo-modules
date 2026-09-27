"""Ce qu'un entretien cédulé rapporte au carnet.

Le pont pose ailleurs une règle stricte : la citation ne déplace aucune donnée,
parce que les deux lectures du bien sont disjointes et qu'aucune n'est de trop.
Deux écritures y font exception, et il faut dire pourquoi plutôt que de les
laisser passer pour un oubli.

Ce ne sont pas des recopies de champs, ce sont **trois faits datés** que le
règlement fait porter au carnet et que seule l'exploitation constate :

- **r. 8.01, art. 2 al. 2, par. 2°** : la date de réalisation des travaux
  d'entretien requis. Le carnet a le champ (`last_maintenance_date`) ; c'est le
  concierge qui sait quand la chaudière a été inspectée.
- **r. 8.01, art. 2 al. 2, par. 3°** : la date à laquelle une réparation
  courante a été effectuée (`last_repair_date`). Même geste, autre paragraphe.
- **r. 8.01, art. 4** : à la mise à jour annuelle, les travaux requis ou prévus
  qui n'ont PAS été effectués, et pourquoi. Sans ce retour, le module produirait
  un carnet qui ment par omission — le défaut de classe corrigé ailleurs dans la
  suite.

⚠️ **Le par. 3° n'a pas de cédule, et il n'en veut pas.** Le par. 2° énonce une
fréquence, et c'est elle qui fait du cédule la preuve que le travail fermé est
bien celui que le carnet annonce. Le par. 3° n'énonce aucune fréquence : une
réparation courante arrive quand elle arrive. Ce qui tient lieu de preuve, c'est
la nature déclarée sur le travail (`bf_repair_scope`), parce que « correctif »
couvre aussi bien le joint qui fuit que la réfection de toiture — et cette
dernière relève de l'art. 3 al. 2, avec son coût, pas du par. 3°. Non qualifié, rien ne remonte.

⚠️ **Seul un carnet ÉTABLI se met à jour.** Un carnet remplacé est un document
historique daté : y écrire aujourd'hui falsifierait ce qu'il disait à sa date.
Un brouillon, lui, n'est pas encore un carnet — c'est le professionnel qui le
compose, et le module ne compose pas à sa place.

⚠️ **La date n'avance jamais à reculons.** Un travail fermé en retard, ou repris
en février pour une tournée de janvier, ne doit pas remplacer un entretien plus
récent déjà noté au carnet.

🔴 **Et une écriture dans un document réglementaire laisse une trace.**
Sans elle, un concierge qui ne peut ni lire ni
écrire le carnet y faisait entrer une date par le `sudo` de ce pont, et il n'en
restait rien. `bf.property.maintenance.item` n'est pas un `mail.thread` et ne
porte aucun champ suivi ; le fil du carnet parent ne bougeait pas. Seul
`write_uid` gardait quelque chose : le DERNIER qui a écrit, sur aucun écran,
écrasé à la saisie suivante, et sans lien vers le billet qui a produit la date.
Le pont raisonne partout sur l'intégrité d'un document daté ; la seule exception
qu'il s'accorde ne pouvait pas se relire.

Chaque écriture poste donc au fil du carnet — c'est là qu'un auditeur regarde,
et le carnet EST déjà un `mail.thread`. Le bien, lui, n'en devient pas un : un
carnet de deux cents biens ferait deux cents fils que personne n'ouvre.

⚠️ **Une raison écrite par une personne ne s'écrase pas.** Le conseil peut avoir
déjà motivé, pour sa mise à jour annuelle, un travail que l'exploitation vient
de déclarer sauté. Le module ne remplace pas ce texte : il écrit là où le carnet
se taisait, et laisse voir toutes les occurrences sautées par le compte que
porte le bien du carnet, pour que rien ne se perde de ce qu'il n'a pas écrit.
"""
from markupsafe import Markup

from odoo import _, models


class MaintenanceRequest(models.Model):
    _inherit = "maintenance.request"

    def _bf_cited_items(self):
        """Les biens du carnet qui citent l'équipement de ces travaux.

        sudo : un concierge ferme des travaux sans avoir la moindre lecture du
        carnet d'entretien, qui vit derrière les droits de la copropriété. Le
        retour d'information ne doit pas se muer en refus d'accès sur un billet
        qu'il a le droit de fermer.
        """
        equipment = self.mapped("equipment_id")
        if not equipment:
            return self.env["bf.property.maintenance.item"]
        return (
            self.env["bf.property.maintenance.item"]
            .sudo()
            .search(
                [
                    ("equipment_id", "in", equipment.ids),
                    ("log_id.state", "=", "established"),
                ]
            )
        )

    def _bf_log_writeback(self, item, body):
        """Écrire au fil du carnet ce que le pont vient d'y porter.

        ⚠️ `sudo` sur le POST, mais l'auteur est la personne réelle : un fil qui
        nomme le compte technique ne dit pas qui a fermé le travail, et c'est la
        seule chose qu'un auditeur cherche. Le concierge n'a aucun droit sur le
        carnet — c'est tout l'objet de ce pont — donc le message ne peut pas
        partir sous ses droits à lui.

        ⚠️ Note interne (`mail.mt_note`) : le carnet a des abonnés, et une
        écriture d'exploitation n'est pas une communication au conseil.
        """
        self.ensure_one()
        log = item.sudo().log_id
        if not log:
            return
        log.sudo().message_post(
            body=body,
            author_id=self.env.user.partner_id.id,
            subtype_xmlid="mail.mt_note",
        )

    def _bf_preventive_done(self):
        """La date de réalisation remonte au carnet (art. 2 al. 2, par. 2°)."""
        super()._bf_preventive_done()
        items = self._bf_cited_items()
        for work in self:
            if not work.close_date:
                continue
            for item in items.filtered(lambda i: i.equipment_id == work.equipment_id):
                if (
                    item.last_maintenance_date
                    and item.last_maintenance_date >= work.close_date
                ):
                    continue
                previous = item.last_maintenance_date
                item.last_maintenance_date = work.close_date
                work._bf_log_writeback(
                    item,
                    Markup(_(
                        "<p>Entretien requis daté au carnet depuis "
                        "l'exploitation (r. 8.01, art. 2 al. 2, par. 2°).</p>"
                        "<ul><li>Bien : %(item)s</li>"
                        "<li>Dernier entretien : %(date)s"
                        "%(previous)s</li>"
                        "<li>Travail : %(work)s</li></ul>"
                    )) % {
                        "item": item.display_name,
                        "date": work.close_date,
                        "previous": (
                            _(" (remplace %s)", previous) if previous else ""
                        ),
                        "work": work.display_name,
                    },
                )

    def _bf_corrective_done(self):
        """La date de la réparation courante remonte au carnet (par. 3°).

        Mêmes trois refus que la date d'entretien, et pour les mêmes raisons :
        seul un carnet ÉTABLI se met à jour, la date n'avance jamais à
        reculons, et le fil du carnet garde ce qui a été remplacé — le champ du
        carnet ne porte que la DERNIÈRE réparation courante, alors que le
        par. 3° en veut l'historique, et c'est le fil qui le tient.
        """
        super()._bf_corrective_done()
        items = self._bf_cited_items()
        for work in self:
            if not work.close_date:
                continue
            for item in items.filtered(lambda i: i.equipment_id == work.equipment_id):
                if (
                    item.last_repair_date
                    and item.last_repair_date >= work.close_date
                ):
                    continue
                previous = item.last_repair_date
                item.last_repair_date = work.close_date
                work._bf_log_writeback(
                    item,
                    Markup(_(
                        "<p>Réparation courante datée au carnet depuis "
                        "l'exploitation (r. 8.01, art. 2 al. 2, par. 3°).</p>"
                        "<ul><li>Bien : %(item)s</li>"
                        "<li>Dernière réparation courante : %(date)s"
                        "%(previous)s</li>"
                        "<li>Travail : %(work)s</li></ul>"
                    )) % {
                        "item": item.display_name,
                        "date": work.close_date,
                        "previous": (
                            _(" (remplace %s)", previous) if previous else ""
                        ),
                        "work": work.display_name,
                    },
                )

    def _bf_preventive_not_done(self):
        """La raison du travail sauté remonte au carnet (art. 4)."""
        super()._bf_preventive_not_done()
        items = self._bf_cited_items()
        for work in self:
            if not work.bf_not_done_reason:
                continue
            reason = _(
                "%(date)s, %(work)s : %(reason)s (constaté à l'exploitation)",
                date=work.bf_not_done_date or work.request_date,
                work=work.name or "",
                reason=work.bf_not_done_reason,
            )
            for item in items.filtered(lambda i: i.equipment_id == work.equipment_id):
                # Ne remplace pas ce qu'une personne a écrit : voir l'en-tête.
                # ⚠️ Le fil reçoit l'occurrence dans les DEUX cas. Celle qui ne
                # s'écrit pas est justement celle que le champ d'une seule ligne
                # perdrait, et l'art. 4 en veut la mention.
                if item.not_done_reason:
                    work._bf_log_writeback(
                        item,
                        Markup(_(
                            "<p>Entretien prévu non effectué, constaté à "
                            "l'exploitation (r. 8.01, art. 4). Le carnet porte "
                            "déjà une raison écrite : celle-ci ne l'écrase "
                            "pas.</p><ul><li>Bien : %(item)s</li>"
                            "<li>Occurrence : %(reason)s</li>"
                            "<li>Travail : %(work)s</li></ul>"
                        )) % {
                            "item": item.display_name,
                            "reason": reason,
                            "work": work.display_name,
                        },
                    )
                    continue
                item.not_done_reason = reason
                work._bf_log_writeback(
                    item,
                    Markup(_(
                        "<p>Entretien prévu non effectué, porté au carnet "
                        "depuis l'exploitation (r. 8.01, art. 4).</p>"
                        "<ul><li>Bien : %(item)s</li>"
                        "<li>Raison : %(reason)s</li>"
                        "<li>Travail : %(work)s</li></ul>"
                    )) % {
                        "item": item.display_name,
                        "reason": reason,
                        "work": work.display_name,
                    },
                )
