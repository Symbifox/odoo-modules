-- Une copie de la base (staging, banc) ne relit pas les agendas des usagers.
UPDATE bf_calendar_subscription SET active = false;
