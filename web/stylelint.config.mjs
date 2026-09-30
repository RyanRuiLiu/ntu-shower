/** @type {import('stylelint').Config} */
export default {
  // SCSS 版而非 CSS 版：后者不认 `//` 注释与 `$变量`，会把每个 .scss 文件
  // 都报成 CssSyntaxError。本配置链自带 postcss-scss 语法解析。
  extends: 'stylelint-config-standard-scss',
  rules: {
    // 设计 token 用 `$color-free` 这类名字，与 SCSS 内置函数（如 `map-get`）无冲突，
    // 但 kebab-case 检查会把 BEM 的 `door--free` 判为非法，故关掉
    'selector-class-pattern': null,
    // `page` 是小程序的内置根标签（Taro 编译为 wxml 时使用），CSS 规范里不存在
    'selector-type-no-unknown': [true, { ignoreTypes: ['page'] }],
  },
}
