# Mobile spike (Expo SDK 58)

Proves the native app can reuse the API, the typed client (`@golf-tracer/api-client`) and the tracer math (`@golf-tracer/tracer-spec`): sign in, pick and upload clips (TUS, same `POST /v1/clips` → upload → `complete` sequence), and play a clip with its tracer.

```bash
cd apps/mobile
EXPO_PUBLIC_SUPABASE_URL=http://<your-lan-ip>:54321 \
EXPO_PUBLIC_SUPABASE_ANON_KEY=<anon key> \
EXPO_PUBLIC_API_URL=http://<your-lan-ip>:8000 \
pnpm start          # then press i (iOS) or a (Android), or scan with a development build
```

Use your machine's LAN IP, not localhost, so the phone can reach the stack, and add the device origin to the API's `CORS_ORIGINS` if you use Expo web.

Status: typechecks and bundles for iOS and Android (`expo export`), but hasn't been run on a device yet. The tracer overlay draws the shared frame plan with `react-native-svg` and omits the glow.
