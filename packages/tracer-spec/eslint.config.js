import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(js.configs.recommended, ...tseslint.configs.recommended, {
  rules: {
    // Shared with React Native: forbid DOM globals and framework imports.
    "no-restricted-globals": ["error", "window", "document", "navigator", "HTMLElement", "HTMLCanvasElement"],
    "no-restricted-imports": ["error", { patterns: ["react", "react-dom", "next", "next/*"] }],
  },
});
