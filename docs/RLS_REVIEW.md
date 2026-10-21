# Row Level Security review

Reviewed against `supabase/migrations/`, as of Oct 20, 2026. Each rule below is exercised by a test in `services/api/tests/test_api.py` (look for `test_rls_*`) or `python/tracer_core/tests/test_jobs.py`.

## Model

- Clients (web, mobile) use the **anon key + the user's JWT**. With that, they can read their own rows directly and upload into their own folder in Storage. Every write and every cross-row operation goes through the API.
- The **API and worker** connect to Postgres directly as the database owner, and to Storage with the service-role key. They bypass RLS, so **every API query filters by the authenticated user id** (`service.get_clip(conn, user_id, clip_id)`). They never take a `user_id` from the request.

## Tables

| Table | RLS | Client select | Client insert/update/delete | Notes |
| --- | --- | --- | --- | --- |
| `profiles` | on | own row (`id = auth.uid()`) | revoked | Created by trigger on `auth.users` insert |
| `upload_batches` | on | own rows | revoked | |
| `clips` | on | own rows | revoked | In the `supabase_realtime` publication; Realtime applies the select policy, so users only receive their own changes |
| `tracers` | on | rows whose clip is theirs (`exists … clips.user_id = auth.uid()`) | revoked | |
| `jobs` | on | **none** (no policies, all privileges revoked) | revoked | Internal queue |

Revoking client write privileges is belt and braces: even if a permissive write policy were added by mistake, `anon`/`authenticated` lack the table privilege.

## Storage

| Bucket | Public | Client access |
| --- | --- | --- |
| `raw` | no | insert/select/update only under `raw/{auth.uid()}/…` (TUS create, resume, upsert) |
| `media` | no | none; worker writes, clients read via short-lived signed URLs from the API |
| `renders` | no | none; worker writes, clients download via signed URLs |

Bucket limits: 500 MB per object; `raw` accepts `video/mp4`, `video/quicktime`, `video/webm` only.

## Functions

- `handle_new_user` is `security definer` with `search_path = ''` and fully qualified names, so a caller can't redirect it to their own objects.
- `touch_updated_at` and `notify_job_queued` run as the invoker; neither reads user data.

## Checked scenarios

- User B can't list, read, delete, approve, re-trace, edit, render or download user A's clips through the API: every one returns 404. (`test_users_cannot_see_or_touch_each_others_clips`)
- With a user JWT, direct SQL sees only the user's own clips, tracers and profile. (`test_rls_users_read_only_their_own_rows`)
- With a user JWT, these are all refused: selecting jobs, updating clip status, inserting tracers, inserting batches. (`test_rls_clients_cannot_write_or_see_jobs`)
- Uploading into another user's `raw` folder, or into `media`/`renders`, is refused. (`test_rls_storage_upload_only_into_own_raw_folder`)
- Expired tokens, wrong-audience tokens and tokens signed by a foreign key are rejected with 401.

## Follow-ups

- Run Supabase's Security Advisor on the hosted project before launch.
- If the mobile app ever writes directly (for example offline drafts), add narrowly scoped insert policies rather than restoring table privileges.
