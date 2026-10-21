-- What auto-tracing detected (ball at address, impact time, horizon, confidence), kept even
-- when tracing routes the clip to needs_input so the manual wizard can start from it.
alter table public.clips add column detection jsonb;
