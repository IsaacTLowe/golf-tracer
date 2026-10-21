import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone server output for the Docker image.
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../.."),
  // Shared workspace packages ship TypeScript source.
  transpilePackages: ["@golf-tracer/tracer-spec", "@golf-tracer/api-client"],
  // The UI is client-rendered against the API (the mobile app reuses the same API), and the
  // root layout reads runtime configuration per request, so prerender caching isn't used.
  cacheComponents: false,
  poweredByHeader: false,
  turbopack: {
    root: path.join(__dirname, "../.."),
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
