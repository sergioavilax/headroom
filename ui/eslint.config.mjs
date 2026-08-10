import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

/**
 * Next's own flat configs, imported directly rather than through `FlatCompat`.
 *
 * `eslint-config-next@16` ships native flat arrays; running them back through the
 * eslintrc compatibility layer fails outright (`Converting circular structure to JSON`),
 * which is a good failure — the compat shim exists for configs that have not been ported,
 * and these have.
 *
 * Two rules are raised above Next's defaults, and both are correctness rather than style
 * in a codebase made of polling hooks: a stale closure over a filter value is how a
 * dashboard shows yesterday's slice while claiming to be live, and an unused variable in
 * a `useMemo` dependency list is usually the visible half of one.
 */
const config = [
  ...nextCoreWebVitals,
  ...nextTypeScript,
  {
    ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**"],
  },
  {
    rules: {
      "react-hooks/exhaustive-deps": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
];

export default config;
