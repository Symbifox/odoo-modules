"""Ce que le procès-verbal imprimé doit dire, pour l'assemblée et le conseil.

Deux organes, deux articles, un seul document. L'art. 1102.1 C.c.Q. fait
transmettre aux copropriétaires le procès-verbal de l'ASSEMBLÉE dans les
30 jours ; l'art. 1086.1 fait la même chose pour celui des réunions du CONSEIL,
avec le même délai et un article distinct.

⚠️ **Les deux articles exigent la TRANSMISSION, pas la signature.** Le document
existe donc pour être transmis et versé au registre de l'art. 1070, et non pour
porter une signature. Qu'un président et un secrétaire le signent est un usage
répandu, pas une obligation sourcée : le bloc de signature n'apparaît que si le
syndicat envoie le document à la signature, et le document se transmet très bien
sans lui.

⚠️ **Le document ne rédige rien.** Le procès-verbal est écrit par le syndicat,
au champ prévu ; le module l'habille, le date et l'identifie. Un logiciel qui
composerait le procès-verbal d'une assemblée à laquelle il n'a pas assisté
inventerait ce qui s'y est dit.
"""
from odoo import _, fields, models
from odoo.tools.misc import format_datetime


class BfPropertyMinutesMoment(models.AbstractModel):
    """L'heure d'une séance, telle qu'elle s'est tenue.

    🔴 **Un `Datetime` rendu tel quel sort en UTC.** Une assemblée tenue à 15 h
    à Montréal s'imprimait « 19:00:00 » sur le procès-verbal qui part aux
    copropriétaires : quatre heures d'écart sur une pièce du registre de
    l'art. 1070, et rien à l'écran ne le disait.

    ⚠️ **Le fuseau vient de la société avant de venir du lecteur.** Une séance a
    eu lieu à une heure, une seule, et elle ne doit pas changer selon qui
    imprime : deux administrateurs dans deux fuseaux tireraient sinon deux
    procès-verbaux qui se contredisent. Le fuseau de la société est donc
    consulté d'abord, celui de l'utilisateur ensuite, et UTC en dernier recours.
    """

    _name = "bf.property.minutes.moment"
    _description = "Heure locale d'une séance imprimée"

    def _report_minutes_local_moment(self):
        self.ensure_one()
        if not self.date:
            return ""
        tz = (
            self.company_id.partner_id.tz
            or self.env.user.tz
            or "UTC"
        )
        return format_datetime(self.env, self.date, tz=tz)


class BfPropertyAssembly(models.Model):
    _inherit = ["bf.property.assembly", "bf.property.minutes.moment"]
    _name = "bf.property.assembly"

    def _report_minutes_title(self):
        self.ensure_one()
        return _("Procès-verbal de l'assemblée des copropriétaires")

    def _report_minutes_legal(self):
        self.ensure_one()
        return _(
            "Article 1102.1 du Code civil du Québec : le conseil "
            "d'administration transmet le procès-verbal aux copropriétaires "
            "dans les 30 jours de l'assemblée. Le procès-verbal fait partie du "
            "registre de l'art. 1070."
        )

    def _report_minutes_rows(self):
        self.ensure_one()
        kinds = dict(self._fields["assembly_type"]._description_selection(self.env))
        rows = [
            (_("Syndicat"), self.organisation_id.display_name or ""),
            (_("Objet"), self.name or ""),
            (_("Nature"), kinds.get(self.assembly_type, "")),
            (_("Tenue le"), self._report_minutes_moment()),
            (_("Lieu"), self.location or _("Non consigné")),
        ]
        if self.is_reconvened and self.previous_assembly_id:
            rows.append(
                (
                    _("Reprise de"),
                    self.previous_assembly_id.display_name or "",
                )
            )
        rows.append((_("Quorum"), self.quorum_rule or ""))
        return rows

    def _report_minutes_moment(self):
        return self._report_minutes_local_moment()

    def _report_minutes_date(self):
        """La date de transmission, ou celle du jour tant que rien n'est transmis.

        ⚠️ Même règle que l'état des charges : la date qui compte est celle de
        la transmission, parce que c'est elle que l'art. 1102.1 borne. Tant que
        le procès-verbal n'est pas transmis, le document porte la date du jour
        et s'annonce comme un projet.
        """
        self.ensure_one()
        return self.minutes_sent_date or fields.Date.context_today(self)


class BfPropertyCouncilMeeting(models.Model):
    _inherit = ["bf.property.council.meeting", "bf.property.minutes.moment"]
    _name = "bf.property.council.meeting"

    def _report_minutes_title(self):
        self.ensure_one()
        return _("Procès-verbal de la réunion du conseil d'administration")

    def _report_minutes_legal(self):
        self.ensure_one()
        return _(
            "Article 1086.1 du Code civil du Québec : le conseil "
            "d'administration transmet le procès-verbal de ses réunions aux "
            "copropriétaires dans les 30 jours. Même délai que l'art. 1102.1 "
            "pour l'assemblée, mais deux articles distincts. Le procès-verbal "
            "fait partie du registre de l'art. 1070."
        )

    def _report_minutes_rows(self):
        self.ensure_one()
        return [
            (_("Syndicat"), self.organisation_id.display_name or ""),
            (_("Objet"), self.name or ""),
            (_("Tenue le"), self._report_minutes_moment()),
            (_("Lieu"), self.location or _("Non consigné")),
        ]

    def _report_minutes_moment(self):
        return self._report_minutes_local_moment()

    def _report_minutes_date(self):
        self.ensure_one()
        return self.minutes_sent_date or fields.Date.context_today(self)
