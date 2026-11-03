# Relances de factures (`bf_invoice_followup`)

Un suivi de relance **par facture** sur `account.move`, là où les relances
d'Odoo travaillent au niveau du client. Chaque facture porte son palier, son
historique d'activités et son bouton d'envoi — et rien ne part jamais tout seul.

## Ce qu'il fait

- **Un palier par facture** : Aucune, 1er rappel, 2e rappel, Mise en demeure.
  Modifiable d'un clic depuis l'en-tête de la facture.
- **Une activité à chaque palier.** Changer le palier à la main planifie une
  activité de relance, avec la facture, le client, le solde et les jours de
  retard dans la note.
- **Un cron aux deux semaines** qui crée une activité de relance une fois
  l'échéance passée du nombre de jours réglé (7 par défaut), pour le vendeur de
  la facture, sinon son créateur, sinon le responsable de repli configuré. La
  quinzaine est voulue : une relance que vous venez de fermer ne revient pas le
  lendemain. Il est idempotent
  — une facture qui porte déjà une relance ouverte est sautée — et **ne touche
  jamais au palier** : c'est vous qui le faites évoluer.
- **Un bouton « Envoyer relance »** qui ouvre le compositeur de courriel
  d'Odoo, pré-chargé avec le gabarit de relance, **modifiable**, et qui n'envoie
  qu'au clic sur Envoyer. Il n'existe aucun envoi automatique dans ce module.
- **Clôture automatique** des activités de relance d'une facture réglée : un
  balayage quotidien les referme. ⚠️ Ce n'est pas instantané, et c'est mesuré :
  au moment où un paiement est enregistré, l'état de paiement de la facture n'a
  pas encore changé pour le code qui l'observe.
- **Un récapitulatif** des factures en retard, par courriel, à la fréquence que
  chaque destinataire choisit (7, 14 ou 30 jours). Réservé aux personnes du
  groupe de facturation : il porte les noms de clients et les soldes dus.
  ⚠️ En multi-société, ce récapitulatif est rendu **une fois pour tous les
  abonnés**, sur les sociétés visibles par l'usager qui exécute la tâche
  planifiée. Avec le pont vers le digest quotidien, la section est au contraire
  rendue par destinataire, dans sa société.
- **Une vue « Factures en retard »** : paliers d'âge, regroupement par vendeur,
  filtres et décorations.
- **Les jours de retard** comme champ sur la facture, affichés dans la liste et
  porteurs des décorations (ambre après l'échéance, rouge passé 30 jours). Le
  champ est calculé et **non stocké** : il ne sert donc ni au tri ni aux
  filtres, qui passent par la date d'échéance.

## Le gabarit de relance

Le module **livre son propre gabarit** (`Relance facture en retard`) : une base
neuve a donc un courriel prêt, en français, qui nomme la facture, son échéance
et son solde. C'est lui qui est retenu à l'installation. Deux noms de repli
(`BF | Relance facture en retard`, `Avis de retard sur facture`) ne sont
consultés que si le gabarit livré a été supprimé de la base. Le choix est
épinglé dans le paramètre système `bf_invoice_followup.relance_template_id` et
se change là — y compris pour pointer un gabarit à vous.

Le gabarit livré est posé en `noupdate` : vos retouches lui appartiennent et
survivent aux montées de version du module.

## Réglages (paramètres système)

| Paramètre | Défaut | Rôle |
|---|---|---|
| `bf_invoice_followup.overdue_days` | 7 | Jours après l'échéance avant qu'une activité de relance soit créée. |
| `bf_invoice_followup.relance_template_id` | résolu à l'installation | Le gabarit que le bouton pré-charge. |
| `bf_invoice_followup.default_user_id` | absent | Qui reçoit la relance quand la facture n'a ni vendeur ni créateur actif. |
| `bf_invoice_followup.ntfy_url` | absent | Adresse d'un serveur [ntfy](https://ntfy.sh) pour recevoir le récapitulatif en poussée. **Sans ce réglage, le module ne fait aucun appel sortant.** |
| `bf_invoice_followup.ntfy_token` | absent | Jeton d'authentification, si votre serveur ntfy en exige un. |
| `bf_invoice_followup.ntfy_priority` | `default` | Priorité de la poussée. |

## Ce qui sort de votre base

Rien, sauf si vous réglez `ntfy_url`. Les courriels partent par votre serveur
sortant Odoo habituel, et le bouton de relance passe par le compositeur, donc
sous votre identité et après votre relecture.

## Réversible

Module additif : il ne touche à aucune donnée d'Odoo et ne dépend d'aucun
module de relance tiers. Il dépend du cœur `account`, de `mail` et de `base`.
Une seule migration existe, de la 18.0.1.1.0 vers la 18.0.1.2.0, et elle ne fait
que reporter l'ancien abonnement unique au récapitulatif vers le réglage par
usager.
