/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Aus dem App-Logo
        brand: {
          DEFAULT: '#4880B4',
          dark:    '#2D5A80',
          darker:  '#1A3A5C',
          red:     '#D64C45',
          yellow:  '#F9B01E',
        },
        // Aus der SAP-CATS-Oberflaeche
        cats: {
          bg:       '#F0F4F8',   // Seitenhintergrund
          panel:    '#FFFFFF',   // Panelflaeche
          header:   '#C6D9F0',   // Panelkopf
          colhead:  '#B8CCE4',   // Tabellen-Spaltenkopf
          row:      '#FFFFFF',
          rowalt:   '#F5F8FC',
          rowhover: '#E4EDF7',
          border:   '#A0B4C8',
          field:    '#FFFFFF',
          fieldkey: '#FFFFCC',   // hervorgehobenes Eingabefeld
          text:     '#1A2733',
          muted:    '#5C7085',
        },
      },
      fontFamily: {
        // SAP CATS setzt Labels serifenlos und Datenfelder dicktengleich.
        sans: ['Arial', 'Helvetica Neue', 'Helvetica', 'sans-serif'],
        mono: ['Consolas', 'Menlo', 'DejaVu Sans Mono', 'Courier New', 'monospace'],
      },
      fontSize: {
        ui: ['13px', '18px'],
      },
    },
  },
  plugins: [],
}
