import config from "@martinrun/frontend-config/eslint";

export default [
  ...config(),
  {
    // TanStack Router file-based routes always export both `Route` and the
    // page component from the same file — that's the convention, not a bug.
    files: ["src/routes/**"],
    rules: { "react-refresh/only-export-components": "off" },
  },
];
