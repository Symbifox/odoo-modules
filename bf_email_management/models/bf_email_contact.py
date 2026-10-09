"""L'historique des courriels d'une fiche contact.

Avant, le bouton « Courriels » de la fiche ne montrait que les lignes dont le
contact est le ``partner_id``, c'est-à-dire l'expéditeur d'un reçu ou le
destinataire principal d'un envoi. Un contact seulement en copie, ou rangé
derrière un autre destinataire, n'y paraissait pas, et une fiche dont aucun
courriel ne la portait en ``partner_id`` n'avait pas de bouton du tout.

Une seule définition de « les courriels de cette fiche » sert désormais le
bouton, la boîte (opérateur ``contact:``) et le panneau ouvert depuis la
fiche : la fiche est le ``partner_id``, **ou** son adresse exacte figure en De,
À ou Cc. Une entreprise compte aussi ses personnes rattachées.

Pourquoi une table plutôt qu'un ``ilike`` sur les trois champs : un ``ilike``
parcourt toute la boîte à chaque ouverture de fiche, plusieurs fois pour une
entreprise, et le temps grandit avec la boîte (sans `pg_trgm`, aucun index ne
l'aide). Et un ``ilike`` ment : une adresse d'une lettre ramasse tous les
courriels dont une adresse la contient. La table garde les adresses ENTIÈRES,
normalisées, indexées.
"""
from odoo import api, fields, models
from odoo.tools import email_normalize_all

ROLES = (("from", "De"), ("to", "À"), ("cc", "Cc"))
CHAMPS_ADRESSES = {"email_from": "from", "email_to": "to", "email_cc": "cc"}
# Borne de l'opérateur `contact:` tapé en texte libre : « contact:marie » ne
# doit pas déplier la moitié du carnet d'adresses en une liste d'adresses.
CONTACTS_PAR_MOT = 20


class BfEmailParticipant(models.Model):
    """Une adresse d'un courriel, avec son rôle. Donnée dérivée de
    ``email_from``, ``email_to`` et ``email_cc`` : jamais écrite à la main."""

    _name = "bf.email.participant"
    _description = "Adresse d'un courriel"
    _log_access = False

    email_id = fields.Many2one(
        "bf.email", required=True, ondelete="cascade", index=True)
    address = fields.Char(required=True, index=True)
    role = fields.Selection(ROLES, required=True)

    _sql_constraints = [
        ("bf_email_participant_unique", "unique(email_id, address, role)",
         "Une adresse ne figure qu'une fois par rôle dans un courriel."),
    ]


class BfEmail(models.Model):
    _inherit = "bf.email"

    participant_ids = fields.One2many(
        "bf.email.participant", "email_id", string="Adresses", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._bf_sync_participants()
        return records

    def write(self, vals):
        res = super().write(vals)
        if CHAMPS_ADRESSES.keys() & vals.keys():
            self._bf_sync_participants()
        return res

    def _bf_sync_participants(self):
        """Réécrit les adresses de ces lignes depuis De, À et Cc."""
        if not self.ids:
            return
        self.flush_recordset(list(CHAMPS_ADRESSES))
        self.env.cr.execute(
            "SELECT id, email_from, email_to, email_cc FROM bf_email WHERE id IN %s",
            [tuple(self.ids)],
        )
        self._bf_ecrire_participants(self.env.cr.fetchall())

    @api.model
    def _bf_ecrire_participants(self, lignes):
        """``lignes`` = ``(id, de, à, cc)``. Aussi appelé par la migration, qui
        rétro-remplit la table sans passer par l'ORM ligne à ligne."""
        ids = [ligne[0] for ligne in lignes]
        if not ids:
            return
        valeurs = []
        for email_id, de, a, cc in lignes:
            for role, texte in (("from", de), ("to", a), ("cc", cc)):
                for adresse in dict.fromkeys(email_normalize_all(texte or "")):
                    valeurs.append((email_id, adresse, role))
        cr = self.env.cr
        cr.execute("DELETE FROM bf_email_participant WHERE email_id IN %s",
                   [tuple(ids)])
        for debut in range(0, len(valeurs), 1000):
            lot = valeurs[debut:debut + 1000]
            cr.execute(
                "INSERT INTO bf_email_participant (email_id, address, role) VALUES "
                + ", ".join(["(%s, %s, %s)"] * len(lot))
                + " ON CONFLICT DO NOTHING",
                [v for triple in lot for v in triple],
            )
        self.env["bf.email.participant"].invalidate_model()
        self.browse(ids).invalidate_recordset(["participant_ids"])

    # ------------------------------------------------------------------
    # « Les courriels de cette fiche »
    # ------------------------------------------------------------------
    @api.model
    def _contact_partners(self, partners):
        """Les fiches dont on veut les courriels : chacune, et pour une
        entreprise, ses personnes rattachées (à toute profondeur).

        ⚠️ La fiche de l'usager lui-même ne vient pas avec son entreprise :
        tout ce qu'il envoie la porte en `partner_id` ou en adresse, et la
        fiche de sa propre entreprise montrerait toute sa boîte. Ouverte
        directement, sa fiche garde ses courriels.
        """
        entreprises = partners.filtered("is_company")
        if not entreprises:
            return partners
        personnes = self.env["res.partner"].search(
            [("id", "child_of", entreprises.ids)])
        # Ses AUTRES fiches aussi : une fiche « personnel »
        # qui porte un alias du compte, rangée sous l'entreprise, ramenait par
        # `partner_id` les courriels de cet alias.
        miennes = self._contact_self_addresses()
        a_lui = personnes.filtered(
            lambda p: p == self.env.user.partner_id
            or set(email_normalize_all(p.email or "")) & miennes)
        return partners | (personnes - a_lui)

    @api.model
    def _contact_self_addresses(self):
        """Les adresses de l'usager, normalisées comme celles de la table :
        `_get_self_addresses` rend les champs tels quels, et une adresse de
        la forme « Nom <x@y> » ne serait jamais écartée."""
        return {
            normalisee
            for brute in self._get_self_addresses()
            for normalisee in (email_normalize_all(brute) or [brute])
        }

    @api.model
    def _contact_domain(self, partners):
        """Le domaine des courriels de ``partners``, sous les droits courants.

        ⚠️ Les adresses de l'usager lui-même sont écartées : sans ça, la fiche
        de sa propre entreprise (qui le compte parmi ses personnes) ramènerait
        toute sa boîte. Sa propre fiche garde ses liens par ``partner_id``.
        """
        partners = self._contact_partners(partners)
        if not partners:
            return [("id", "=", False)]
        miennes = self._contact_self_addresses()
        adresses = sorted({
            adresse
            for partner in partners
            for adresse in email_normalize_all(partner.email or "")
        } - miennes)
        domaine = [("partner_id", "in", partners.ids)]
        if adresses:
            domaine = ["|"] + domaine + [
                ("participant_ids.address", "in", adresses)]
        return domaine

    @api.model
    def _contact_partners_from_query(self, valeur):
        """Les fiches que désigne la valeur de l'opérateur ``contact:``.

        ``#42`` désigne une fiche précise (c'est ce que pose le panneau ouvert
        depuis la fiche) ; sinon le texte cherche le nom ou l'adresse.
        """
        Partner = self.env["res.partner"]
        valeur = (valeur or "").strip()
        if valeur.startswith("#") and valeur[1:].isdigit():
            return Partner.browse(int(valeur[1:])).exists()
        if not valeur:
            return Partner
        return Partner.search(
            ["|", ("name", "ilike", valeur), ("email", "ilike", valeur)],
            limit=CONTACTS_PAR_MOT,
        )
