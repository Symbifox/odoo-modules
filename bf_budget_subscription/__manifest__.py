{
    "name": "Budgets opérationnels — engagements récurrents",
    # 18.0.1.4.0: le registre ne s'ouvre qu'à qui peut le lire — les deux
    #   champs qui portent des abonnements et `action_view_subscriptions`
    #   demandent `bf_subscription.group_subscription_user`, le `groups` étant
    #   AU CHAMP (un champ caché de l'écran répond quand même à un `read` par
    #   RPC, et le bassin est relevé en sudo). Les sommes et les compteurs
    #   restent libres : le chiffre est stable par rôle. ⚠️ Personne n'est
    #   refusé aujourd'hui, et c'est mesuré : `base.group_user` IMPLIQUE
    #   `bf_subscription.group_subscription_user`, donc aucun usager interne
    #   ne perd rien et aucun écran n'explosait. Le garde est là pour le jour
    #   où l'accès au registre se resserre, et pour que la forme du module
    #   soit celle de son jumeau achats.
    # 18.0.1.4.0: une ligne RESTREINTE à des comptes analytiques ne porte
    #   plus rien du registre. L'abonnement s'accroche au poste et ne sait rien
    #   de l'axe analytique : l'attribuer en entier à chaque ligne restreinte
    #   comptait le même renouvellement plusieurs fois, et son réalisé arrive
    #   désormais pondéré (la part distribuée, socle 18.0.1.5.0) — l'engagé
    #   aurait sauté au geste de comptabilisation. La ligne nue du poste porte
    #   tout, l'ambiguïté ne se devine pas. ET ELLE SE DÉCLARE : à zéro le bloc
    #   des engagements récurrents passe `invisible`, donc la ligne restreinte
    #   dit combien d'abonnements son poste porte et qu'elle n'en porte aucun —
    #   un signal, jamais une part devinée (⛔ pas
    #   `has_subscription_without_calendar`, qui dit autre chose).
    # 18.0.1.3.0: le théorique de calendrier n'est plus calculé ici : le
    #   satellite déclare son couple (dû à ce jour, total de la période) par
    #   `_calendar_commitment_components` et le socle (bf_budget 18.0.1.4.0,
    #   requis) combine ; avec un second calendrier (bons de commande), le
    #   reliquat cesse d'être compté deux fois. Deux valeurs se raffinent au
    #   passage : le reliquat suit la ventilation saisie (plus un prorata
    #   linéaire de l'exercice), et un calendrier qui ne contribue rien dans
    #   la fenêtre (ponctuel passé résilié) ne bascule plus la base en
    #   « calendar ».
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.1.2.0: le cycle aux 4 semaines de bf_subscription 18.0.1.9.0 donne
    #   13 échéances par année au calendrier d'engagements.
    "version": "18.0.1.4.0",
    "category": "Accounting/Accounting",
    "summary": "Les abonnements deviennent un calendrier d'engagements datés, et le théorique s'y appuie",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": ["bf_budget", "bf_subscription"],
    "description": """
Budgets opérationnels — engagements récurrents
==============================================

Rattache le registre des abonnements aux postes budgétaires, et s'en sert pour
deux choses que le socle ne peut pas faire seul.

**L'engagé cesse d'attendre la facture**
  Un renouvellement à venir dans la période budgétaire est déjà engagé : il est
  connu, daté et contractuel. Le socle ne comptait que le comptabilisé.

**Le théorique suit le calendrier, pas le chronomètre**
  Une dépense annuelle ne se lisse pas sur douze mois. Quand les engagements d'un
  poste sont datés, le théorique devient « ce qui était dû à ce jour » au lieu
  d'une fraction du temps écoulé. La part du plan qui n'est adossée à aucun
  engagement daté reste au prorata, et la ligne affiche laquelle des deux bases
  elle a utilisée.

⚠️ **Un abonnement à la demande n'a pas de calendrier**
  Il dépense pourtant. Le module ne le compte pas comme un engagement nul en
  silence : il l'affiche, parce qu'un calendrier partiel pris pour complet
  sous-estime le théorique et fabrique de fausses alertes.
""",
    "data": [
        "security/ir.model.access.csv",
        "wizard/bf_budget_subscription_link_wizard_views.xml",
        "views/subscription_subscription_views.xml",
        "views/bf_budget_line_views.xml",
    ],
}
