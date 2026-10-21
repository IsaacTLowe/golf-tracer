// Expo spike (milestone 7): sign in, upload clips, and play a clip with its tracer, using the
// same API, typed client and tracer-spec math as the web app. Not a finished app.
import {
  CLIP_RULES,
  clipContentType,
  clipRejection,
  createApiClient,
  unwrap,
  type Clip,
  type ClipDetail,
} from "@golf-tracer/api-client";
import { framePlan, type Tracer } from "@golf-tracer/tracer-spec";
import { createClient, type Session } from "@supabase/supabase-js";
import { useEventListener } from "expo";
import * as ImagePicker from "expo-image-picker";
import * as SecureStore from "expo-secure-store";
import { StatusBar } from "expo-status-bar";
import { useVideoPlayer, VideoView } from "expo-video";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Button, FlatList, Pressable, SafeAreaView, StyleSheet, Text, TextInput, View } from "react-native";
import Svg, { Line } from "react-native-svg";
import * as tus from "tus-js-client";

const SUPABASE_URL = process.env.EXPO_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";
const SUPABASE_ANON_KEY = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? "";
const API_URL = process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000";

// Sessions live in the device keychain/keystore.
const secureStorage = {
  getItem: (k: string) => SecureStore.getItemAsync(k),
  setItem: (k: string, v: string) => SecureStore.setItemAsync(k, v),
  removeItem: (k: string) => SecureStore.deleteItemAsync(k),
};

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: { storage: secureStorage, persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
});

async function accessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

const api = createApiClient({ baseUrl: API_URL, getAccessToken: accessToken });

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [openClip, setOpenClip] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);

  if (!ready) return <ActivityIndicator style={{ flex: 1 }} />;
  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="auto" />
      {!session ? <SignIn /> : openClip ? <Player clipId={openClip} onBack={() => setOpenClip(null)} /> : <Library onOpen={setOpenClip} />}
    </SafeAreaView>
  );
}

function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const go = async (signUp: boolean) => {
    setError(null);
    const { error } = signUp
      ? await supabase.auth.signUp({ email, password })
      : await supabase.auth.signInWithPassword({ email, password });
    if (error) setError(error.message);
  };
  return (
    <View style={styles.pad}>
      <Text style={styles.h1}>Golf Tracer</Text>
      <TextInput style={styles.input} placeholder="Email" autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} />
      <TextInput style={styles.input} placeholder="Password" secureTextEntry value={password} onChangeText={setPassword} />
      {error && <Text style={styles.error}>{error}</Text>}
      <Button title="Sign in" onPress={() => go(false)} />
      <Button title="Create account" onPress={() => go(true)} />
    </View>
  );
}

function Library({ onOpen }: { onOpen: (id: string) => void }) {
  const [clips, setClips] = useState<Clip[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const page = await unwrap(api.GET("/v1/clips", { params: { query: { limit: 50 } } }));
    setClips(page.items as Clip[]);
  }, []);

  useEffect(() => {
    void load();
    // Live status: same Realtime channel shape as the web library.
    const ch = supabase
      .channel("mobile-clips")
      .on("postgres_changes", { event: "*", schema: "public", table: "clips" }, () => void load())
      .subscribe();
    return () => {
      void supabase.removeChannel(ch);
    };
  }, [load]);

  const pickAndUpload = async () => {
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["videos"],
      allowsMultipleSelection: true,
      selectionLimit: CLIP_RULES.maxBatchSize,
    });
    if (res.canceled) return;
    const assets = res.assets;
    const batch = assets.length > 1 ? await unwrap(api.POST("/v1/batches", { body: { clip_count: assets.length } })) : null;
    for (const a of assets) {
      const name = a.fileName ?? `clip-${Date.now()}.mp4`;
      const info = {
        name,
        type: a.mimeType ?? "",
        size: a.fileSize ?? 0,
        durationS: a.duration != null ? a.duration / 1000 : null,
        width: a.width,
        height: a.height,
      };
      const reason = clipRejection(info);
      if (reason) {
        setBusy(`${name}: ${reason}`);
        continue;
      }
      setBusy(`Uploading ${name}…`);
      const created = await unwrap(
        api.POST("/v1/clips", {
          body: {
            filename: name,
            size_bytes: info.size || 1,
            content_type: clipContentType(info),
            duration_s: info.durationS,
            width: a.width,
            height: a.height,
            batch_id: batch?.id ?? null,
          },
        }),
      );
      const target = created.upload;
      await new Promise<void>((resolve, reject) => {
        // tus-js-client reads React Native files from { uri, name, type }.
        const upload = new tus.Upload({ uri: a.uri, name, type: clipContentType(info) } as unknown as Blob, {
          endpoint: target.tus_endpoint,
          chunkSize: target.chunk_size,
          retryDelays: [1000, 2000, 4000],
          uploadDataDuringCreation: true,
          headers: { "x-upsert": "true", apikey: SUPABASE_ANON_KEY },
          metadata: { bucketName: target.bucket, objectName: target.object_path, contentType: target.content_type, cacheControl: "3600" },
          onBeforeRequest: async (req) => {
            const t = await accessToken();
            if (t) req.setHeader("authorization", `Bearer ${t}`);
          },
          onSuccess: () => resolve(),
          onError: reject,
        });
        upload.start();
      });
      await unwrap(api.POST("/v1/clips/{clip_id}/complete", { params: { path: { clip_id: created.clip.id } } }));
    }
    setBusy(null);
    void load();
  };

  return (
    <View style={{ flex: 1 }}>
      <View style={styles.row}>
        <Text style={styles.h1}>Library</Text>
        <Button title="Upload" onPress={() => void pickAndUpload()} />
        <Button title="Sign out" onPress={() => void supabase.auth.signOut()} />
      </View>
      {busy && <Text style={styles.pad}>{busy}</Text>}
      <FlatList
        data={clips}
        keyExtractor={(c) => c.id}
        renderItem={({ item }) => (
          <Pressable style={styles.item} onPress={() => onOpen(item.id)}>
            <Text style={{ fontWeight: "600" }}>{item.original_filename}</Text>
            <Text>{item.status}</Text>
          </Pressable>
        )}
      />
    </View>
  );
}

function Player({ clipId, onBack }: { clipId: string; onBack: () => void }) {
  const [clip, setClip] = useState<ClipDetail | null>(null);
  useEffect(() => {
    void unwrap(api.GET("/v1/clips/{clip_id}", { params: { path: { clip_id: clipId } } })).then((d) => setClip(d as ClipDetail));
  }, [clipId]);
  if (!clip?.playback_url || !clip.width || !clip.height) {
    return (
      <View style={styles.pad}>
        <Button title="Back" onPress={onBack} />
        <Text>{clip ? `Status: ${clip.status}` : "Loading…"}</Text>
      </View>
    );
  }
  return <TracedVideo clip={clip} onBack={onBack} />;
}

function TracedVideo({ clip, onBack }: { clip: ClipDetail; onBack: () => void }) {
  const player = useVideoPlayer(clip.playback_url!, (p) => {
    p.timeUpdateEventInterval = 1 / 30;
    p.loop = true;
    p.play();
  });
  const [t, setT] = useState(0);
  useEventListener(player, "timeUpdate", (e) => setT(e.currentTime));
  const [box, setBox] = useState({ w: 0, h: 0 });
  const tracer = (clip.tracer?.data ?? null) as unknown as Tracer | null;
  // Same frame plan as the web canvas and the server render; glow is omitted in this spike.
  const plan = useMemo(() => (tracer ? framePlan(tracer, t) : null), [tracer, t]);
  const aspect = clip.width! / clip.height!;
  return (
    <View style={{ flex: 1 }}>
      <Button title="Back" onPress={onBack} />
      <View style={{ width: "100%", aspectRatio: aspect }} onLayout={(e) => setBox({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}>
        <VideoView player={player} style={StyleSheet.absoluteFill} contentFit="fill" nativeControls={false} />
        {plan && box.w > 0 && (
          <Svg style={StyleSheet.absoluteFill} width={box.w} height={box.h} pointerEvents="none">
            {plan.points.slice(1).map((p, i) => {
              const a = plan.points[i]!;
              return (
                <Line
                  key={i}
                  x1={a[0] * box.w}
                  y1={a[1] * box.h}
                  x2={p[0] * box.w}
                  y2={p[1] * box.h}
                  stroke={tracer!.style.color}
                  strokeOpacity={plan.alpha}
                  strokeWidth={0.5 * (plan.widths[i]! + plan.widths[i + 1]!) * box.h}
                  strokeLinecap="round"
                />
              );
            })}
          </Svg>
        )}
      </View>
      <Text style={styles.pad}>
        {clip.original_filename} · {clip.status} · t={t.toFixed(2)}s
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#fff" },
  pad: { padding: 16, gap: 12 },
  row: { flexDirection: "row", alignItems: "center", gap: 8, padding: 16 },
  h1: { fontSize: 24, fontWeight: "700", flex: 1 },
  input: { borderWidth: 1, borderColor: "#ccc", borderRadius: 8, padding: 12, minHeight: 44 },
  error: { color: "#c4302b" },
  item: { padding: 16, borderBottomWidth: StyleSheet.hairlineWidth, borderColor: "#ddd", minHeight: 44 },
});
