import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Business logic lives in the API; the web app must not grow server actions or route handlers.
      "no-restricted-syntax": [
        "error",
        { selector: "ExpressionStatement[directive='use server']", message: "No server actions: call the API instead." },
      ],
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
