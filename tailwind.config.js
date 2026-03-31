module.exports = {
  content: [
    "./report/templates/**/*.html",
    "./report/static/app.js",
  ],
  safelist: [
    "badge-error",
    "badge-warning",
    "badge-success",
    "badge-ghost",
    "badge-info",
    "progress-error",
    "progress-warning",
    "progress-success",
    "progress-info",
    "bg-error/10",
    "bg-warning/10",
    "bg-success/10",
    "text-error",
    "text-warning",
    "text-success",
    "text-amber-600",
    "text-gray-400",
    "text-orange-700",
    "text-red-500",
    "text-green-600"
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "system-ui",
          "-apple-system",
          '"Segoe UI"',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          '"Noto Sans CJK SC"',
          '"Source Han Sans SC"',
          "sans-serif",
        ],
        mono: [
          '"SFMono-Regular"',
          '"Cascadia Mono"',
          '"Sarasa Mono SC"',
          '"JetBrains Mono"',
          "Menlo",
          "Monaco",
          "Consolas",
          '"Liberation Mono"',
          "monospace",
        ],
      },
      colors: {
        beige: {
          50: "#faf7f4",
          100: "#f0ebe4",
          200: "#e2dace",
          300: "#cdc3b5",
        },
      },
    },
  },
  plugins: [require("daisyui")],
  daisyui: {
    themes: ["corporate"],
  },
};