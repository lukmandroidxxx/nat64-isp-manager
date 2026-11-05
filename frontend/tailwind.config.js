/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef8ff",
          100: "#d8efff",
          200: "#b9e3ff",
          300: "#89d2ff",
          400: "#52b8ff",
          500: "#2a9bff",
          600: "#147cf5",
          700: "#1163e1",
          800: "#1550b6",
          900: "#17458f"
        }
      }
    }
  },
  plugins: []
};
