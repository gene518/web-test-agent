/**
 * Prettier 配置。
 * @see https://prettier.io/docs/configuration
 * @type {import("prettier").Config}
 */
const config = {
  endOfLine: "auto",
  singleAttributePerLine: true,
  plugins: ["prettier-plugin-tailwindcss"],
};

export default config;
