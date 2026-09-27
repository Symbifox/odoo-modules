"""Qui décide, par opposition à qui voit.

🔴 **Une règle d'enregistrement borne QUI VOIT QUOI, pas CE QU'ON PEUT FAIRE.**
Toute méthode sans souligné initial est appelable par RPC dès qu'on a l'accès au
modèle : la vue n'est pas une barrière, et un résident n'a pas besoin d'un bouton
à l'écran pour appeler la méthode. Les `UserError` d'une transition sont des
gardes d'ÉTAT (« n'est plus active »), pas des gardes de DROIT.

Constat vérifié par sonde avant correction : un résident
pouvait appeler `action_confirm` sur SA réservation et s'auto-approuver, en
restant dans son périmètre de lecture, tout en court-circuitant le réglage
« confirmation du syndicat requise ». L'art. 1070 C.c.Q. impose au syndicat de
tenir un registre fidèle ; un flux d'approbation que le demandeur clôt lui-même
ne l'est pas.

⚠️ **La garde vit ICI, en un seul endroit, parce qu'elle a été recopiée trois
fois.** Une sonde d'autorité a trouvé un quatrième point d'entrée
sans garde — l'envoi d'un avis urgent par texto — et il manquait précisément
parce que la garde était un patron à recopier plutôt qu'un socle à hériter.
Un texto était déjà parti chez le fournisseur quand l'`AccessError` de
l'écriture a annulé la transaction : les droits avaient sauvé la base de
données, pas le réseau téléphonique.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError, ValidationError

from .mail_template import bf_mail_layout


class BfPropertyOrganisationAuthority(models.AbstractModel):
    _name = "bf.property.organisation.authority"
    _description = "Ce qui relève du syndicat et non de l'occupant"

    def _ensure_organisation_decides(self, what):
        """Lève si l'appelant n'est pas le syndicat. À appeler AVANT tout effet.

        ⚠️ Avant, pas après : une garde placée sous un envoi laisse partir
        l'envoi. Ce qui sort du système (un texto, un courriel, un fichier chez
        un tiers) ne se rappelle pas par un `rollback`.
        """
        if self.env.su or self.env.user.has_group(
            "bf_property_core.group_bf_property_manager"
        ):
            return
        raise AccessError(
            _("%s relève du syndicat, pas de l'occupant.") % what
        )

    # ── Ce qui part vers une personne hors du bureau ──

    def _bf_notify_partners(self, partners, subject, body):
        """Poste au fil ET envoie par courriel aux personnes nommées.

        🔴 Sans ceci, quatre gestes de la suite disaient avoir
        avisé quelqu'un et n'envoyaient rien (le préavis de l'art. 1069 al. 2,
        la transmission de l'art. 1068.2 al. 2, la décision sur une demande
        d'entretien). Une note au fil n'avise personne hors du bureau : elle ne
        part pas.

        Rend `(joints, non_joints)`. Une personne sans adresse n'empêche pas
        les autres d'être avisées, mais l'appelant doit le DIRE au fil : c'est
        au syndicat de l'aviser autrement.

        ⚠️ Mise en page légère et sans bouton : le destinataire n'a pas de
        compte, et un bouton le mènerait à une page de connexion.
        """
        self.ensure_one()
        reached = partners.filtered("email")
        if reached:
            self.message_post(
                body=body,
                subject=subject,
                partner_ids=reached.ids,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
                email_layout_xmlid=bf_mail_layout(self.env),
            )
        return reached, partners - reached

    # ── Ce qu'un occupant écrit sur SA fiche ──
    #
    # 🔴 Une transition gardée ne garde que la transition. Sans cette liste :
    # `action_confirm` portait la garde d'autorité, et un résident écrivait
    # `state = "confirmed"` directement, s'auto-approuvant sans jamais passer
    # par la méthode. Le registre des visiteurs se réécrivait de la même façon,
    # `date_arrived` comprise, alors que le champ est déclaré `readonly` — ce
    # qui ne vaut que pour l'écran.
    #
    # ⚠️ **Liste blanche, pas liste noire.** Un champ ajouté demain est refusé
    # par défaut plutôt qu'ouvert par oubli, et c'est le seul sens qui résiste
    # au temps.
    #
    # ⚠️ **Pas de drapeau de contexte.** La tentation est de marquer les
    # écritures qui viennent des méthodes du modèle et de laisser passer
    # celles-là. Le contexte est un paramètre d'appel : l'appelant le fabrique.
    # Ce qui borne ici, ce sont les VALEURS permises, que personne ne peut
    # contrefaire.

    _portal_writable_fields = ()
    # Ce qu'un occupant peut DÉPOSER : le reste naît de ses défauts.
    _portal_creatable_fields = ()
    _portal_state_field = "state"
    # {état actuel: (états permis,)} — ce que l'occupant peut faire lui-même.
    _portal_state_allowed = {}

    @api.model
    def _ensure_portal_create_scope(self, vals_list):
        """Lève si un dépôt d'occupant porte ce que le syndicat constate.

        🔴 La garde d'écriture ne jouait qu'au `write`, et le portail a le droit
        de CRÉER. Un `create` par RPC posait donc d'emblée `state = confirmed`
        sur une salle à confirmation requise, un visiteur déjà « arrivé » aux
        heures de son choix, ou une demande « réglée » et antidatée : tout ce
        que la liste blanche du `write` refuse, par l'autre porte.

        Seul l'usager du portail est borné. Le formulaire interne envoie ses
        défauts avec le reste, et l'interne a sa propre garde d'autorité.
        """
        if self.env.su or self.env.user._is_internal():
            return
        allowed = set(self._portal_creatable_fields)
        # 🔴 Et le CONTEXTE, pas seulement les valeurs. `create` complète les
        # valeurs par `default_<champ>` pris au contexte, APRÈS cette garde, et
        # le contexte d'un appel RPC est fourni par l'appelant : des valeurs
        # honnêtes et un `default_state` forgé rouvraient les trois portes.
        forged = sorted(
            key[len("default_"):] for key in self.env.context
            if key.startswith("default_")
            and key[len("default_"):] in self._fields
            and key[len("default_"):] not in allowed
        )
        if forged:
            raise AccessError(
                _(
                    "Ces renseignements relèvent du syndicat, pas de "
                    "l'occupant : %s."
                )
                % ", ".join(str(self._fields[name].string) for name in forged)
            )
        for vals in vals_list:
            refused = sorted(set(vals) - allowed)
            if refused:
                raise AccessError(
                    _(
                        "Ces renseignements relèvent du syndicat, pas de "
                        "l'occupant : %s."
                    )
                    % ", ".join(
                        str(self._fields[name].string)
                        if name in self._fields else name
                        for name in refused
                    )
                )

    def _ensure_portal_write_scope(self, vals):
        """Lève si l'écriture sort de ce que l'occupant peut toucher."""
        if self.env.su or self.env.user.has_group(
            "bf_property_core.group_bf_property_manager"
        ):
            return
        refused = sorted(set(vals) - set(self._portal_writable_fields))
        if refused:
            raise AccessError(
                _(
                    "Ces renseignements relèvent du syndicat, pas de "
                    "l'occupant : %s."
                )
                % ", ".join(
                    str(self._fields[name].string)
                    if name in self._fields else name
                    for name in refused
                )
            )
        field = self._portal_state_field
        if field in vals:
            target = vals[field]
            for record in self:
                if target not in self._portal_state_allowed.get(record[field], ()):
                    raise AccessError(
                        _(
                            "« %(record)s » ne peut pas passer à cet état de "
                            "votre propre autorité."
                        )
                        % {"record": record.display_name}
                    )


class BfPropertySyndicatRegime(models.AbstractModel):
    """Ce qui n'a de sens que sous le régime de la copropriété divise.

    ⚠️ Posé à la neutralisation du socle. Tant que le produit ne servait
    qu'un seul segment, la question ne se posait pas : tout était un syndicat.
    Le locatif entré au périmètre, une assemblée générale, un fonds de
    prévoyance ou une attestation de l'art. 1068.1 chez un bailleur seraient un
    non-sens juridique, et rien n'empêchait de les y créer.

    ⚠️ **Une contrainte, pas une garde de droit.** Ce n'est pas une question de
    permission — le gestionnaire a bien le droit — mais de cohérence : ces
    objets n'existent pas dans ce régime. D'où `ValidationError` et non
    `AccessError`.
    """

    _name = "bf.property.syndicat.regime"
    _description = "Objet propre au régime de la copropriété divise"

    @api.constrains("organisation_id")
    def _check_syndicat_regime(self):
        for record in self:
            organisation = record.organisation_id
            if organisation and not organisation.is_syndicat:
                raise ValidationError(
                    _(
                        "« %(what)s » relève du régime de la copropriété "
                        "divise, et %(who)s est de nature « %(kind)s ». Ce "
                        "n'est pas une question de permission : cet objet "
                        "n'existe pas dans ce régime."
                    )
                    % {
                        "what": record._description,
                        "who": organisation.display_name,
                        "kind": dict(
                            organisation._fields["kind"]._description_selection(
                                self.env
                            )
                        ).get(organisation.kind, organisation.kind),
                    }
                )
