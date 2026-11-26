# bf_cx_subscription: recurring revenue at risk

Auto-installs when both `bf_cx_dashboard` and `bf_subscription` are
installed. Adds to the dashboard's Customer Experience tile the sum of
monthly-equivalent costs (`monthly_equivalent`) of active managed
subscriptions (`managed_for_id`) belonging to clients who have feedback
awaiting a callback or an open complaint, shown in red once it rises
above zero. Read-only, nothing is sent to the client.

The figure is computed only for holders of the subscription register (assigned
since `bf_subscription` 18.0.1.10.0, v18.0.1.1.1); for anyone else it stays at
zero and the tile does not show it.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English.
