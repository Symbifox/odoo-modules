"""Le pont : la page du client nomme le bénéficiaire du mandat.

`bf_stepbystep_clients` nomme le client du projet, c'est-à-dire le payeur. Quand
le mandat est payé par un tiers, ce n'est pas lui qui lit la page : une
association paie un volet que l'un de ses membres reçoit, un centre paie une
heure de formation donnée à son client. Ce pont remplace les deux endroits où
ça se décide, et l'adresse où part le code d'accès.

Il hérite du contrôleur au lieu de le réécrire : les routes, les gardes et les
en-têtes restent ceux du module porteur, et une correction là-bas vaut ici.
"""
from odoo.addons.bf_stepbystep_clients.controllers.portal import ProgressionPortail


class ProgressionPortailBeneficiaire(ProgressionPortail):

    @classmethod
    def _progression_valeurs(cls, projet):
        """Le client que la page nomme devient le bénéficiaire du mandat.

        `bf_beneficiary_effective_id` rend déjà le bénéficiaire quand il est
        nommé et le donneur d'ordre sinon, résolu au partenaire commercial.
        Rien à redécider ici : sans bénéficiaire, la valeur reste celle que le
        module porteur aurait posée.

        🔴 « Reste », et non « est réécrite à l'identique » : sans bénéficiaire,
        le pont ne touche PAS à `portal_client`. Le réécrire avec le donneur
        d'ordre rendait la même valeur en production, mais effaçait celle d'un
        autre pont (ou d'un essai du module porteur qui en simule un) : le code
        d'accès partait alors au payeur à la place du lecteur.
        """
        valeurs = super()._progression_valeurs(projet)
        if projet.bf_beneficiary_id:
            valeurs["portal_client"] = projet.bf_beneficiary_effective_id
        return valeurs

    @classmethod
    def _progression_destinataire_du_code(cls, projet, nomme=None):
        """Un bénéficiaire saisi comme CONTACT reçoit le code à son adresse quand
        sa société n'en a pas.

        Le module porteur envoie le code à la partie nommée, qui est le
        partenaire commercial ; sans adresse sur la société, pas de code. Pour
        « Jean (Bêta inc.) » saisi avec son adresse, sous une Bêta inc. sans
        adresse, le lecteur restait dehors. Le repli reste CHEZ LE BÉNÉFICIAIRE,
        jamais chez le payeur, et ne joue que si la page nomme bien la société
        de ce contact et que ce contact est actif (archivé, il a quitté le
        client).

        Il passe aussi devant le contact du donneur d'ordre quand les deux sont
        de la même société sans adresse : le porteur envoyait alors le code au
        contact qui paie, et non à celui qu'on a nommé bénéficiaire.

        Le lecteur de ce choix est aussi celui que compare la purge du modèle
        (`_bf_lecteur_du_code`) : les deux doivent changer ensemble.
        """
        destinataire = super()._progression_destinataire_du_code(projet, nomme=nomme)
        beneficiaire = projet.sudo().bf_beneficiary_id
        if not beneficiaire:
            return destinataire
        if nomme is None:
            # Comme le porteur : la partie que la PAGE nomme, qu'un autre pont a
            # pu remplacer, et non le calcul du bénéficiaire lu directement.
            nomme = cls._progression_valeurs(projet).get("portal_client")
        societe = beneficiaire.commercial_partner_id
        if nomme != societe or societe.email:
            return destinataire
        if beneficiaire.active and beneficiaire.email:
            return beneficiaire.email
        return destinataire

    @classmethod
    def _progression_langue_du_lecteur(cls, projet):
        """Celui qui lit décide de la langue, pas celui qui paie.

        Le bénéficiaire quand il en a une, sinon on laisse le module porteur
        décider : un bénéficiaire sans langue ne doit pas effacer celle du
        donneur d'ordre.

        ⚠️ Le repli ne joue que sur une langue VIDE, pas sur une langue posée
        mais désactivée sur la base : dans ce cas le porteur ne bascule vers
        rien, et la page sort dans la langue par défaut du site plutôt que dans
        celle du donneur d'ordre. Sans effet sur une base qui n'a qu'une
        langue active.
        """
        return (projet.bf_beneficiary_id.lang
                or super()._progression_langue_du_lecteur(projet))
