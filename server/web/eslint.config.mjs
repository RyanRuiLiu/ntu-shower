/**
 * ESLint 9 平面配置：网页端唯一 JS 门禁。
 *
 * 为什么要有它：no-undef 在门禁期抓"调用了未导入的符号"——2026-11-10 生产事故
 * （refreshSession 漏导入，整个管理台脚本初始化即崩）正是这个类别；node --check
 * 只查语法，查不出引用错误。
 *
 * globals 手工枚举浏览器全局（MDN Window/WorkerGlobalScope 常用集），不引依赖包
 * ——零构建前端，npx 直跑。
 */
export default [
  {
    // 构建产物与依赖里的第三方 JS 不在门禁范围（JaCoCo/coverage 的报告资源等）。
    // ignores 相对本配置文件所在目录解析。
    ignores: [
      "../.venv/**",
      "../../../android/app/build/**",
      "**/node_modules/**",
    ],
  },
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        localStorage: "readonly",
        location: "readonly",
        navigator: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        requestAnimationFrame: "readonly",
        cancelAnimationFrame: "readonly",
        fetch: "readonly",
        WebSocket: "readonly",
        Event: "readonly",
        CustomEvent: "readonly",
        URLSearchParams: "readonly",
        FormData: "readonly",
        AbortController: "readonly",
        crypto: "readonly",
        history: "readonly",
        screen: "readonly",
        alert: "readonly",
        CSS: "readonly",
        DOMParser: "readonly",
        Image: "readonly",
        HTMLElement: "readonly",
        performance: "readonly",
        ResizeObserver: "readonly",
        Audio: "readonly",
      },
    },
    rules: {
      // 引用错误类别：2026-11-10 事故的根
      "no-undef": "error",
      "no-unused-vars": ["error", { args: "none", caughtErrors: "none" }],
      // JS 内联样式纪律（自 test_web_styles.py 迁入）：
      // .style.<prop> 直赋值禁（读也禁），setProperty 仅限自定义属性 --*
      "no-restricted-syntax": [
        "error",
        {
          selector: "MemberExpression[object.property.name='style'][property.name!=/setProperty/]",
          message: "内联样式只能经 .style.setProperty('--*', …) 写自定义属性（样式规则唯一出处是共享 CSS）",
        },
        {
          selector:
            "CallExpression[callee.property.name='setProperty'][callee.object.property.name='style'] > Literal[value=/^(?!--)/]",
          message: "setProperty 只允许写 --* 自定义属性",
        },
        {
          selector: "TemplateElement[value.raw=/\\bstyle\\s*=\\s*\"/]",
          message: "模板字面量不得内联 style=\"…\"（样式规则唯一出处是共享 CSS）",
        },
      ],
    },
  },
]
