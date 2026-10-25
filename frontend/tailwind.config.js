/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1c1915",
        paper: "#f3efe6",
        card: "#fffdf8",
        line: "#e4dccb",
        brass: "#9a7340",
        sidebar: "#121820",
      },
      fontFamily: {
        sans: ["Outfit", "Segoe UI", "sans-serif"],
        display: ["Fraunces", "Georgia", "serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(28, 25, 21, 0.04), 0 12px 32px rgba(28, 25, 21, 0.05)",
      },
    },
  },
  plugins: [],
};
