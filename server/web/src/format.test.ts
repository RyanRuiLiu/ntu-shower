import { describe, expect, it } from "vitest";

import { fmtDuration, fmtFullDateTime } from "./format";

describe("fmtFullDateTime", () => {
  it("把时刻渲染为 YYYY-MM-DD HH:MM:SS（按浏览器时区）", () => {
    // 用本地时区构造期望：CI 与本地的时区不同，输入也不带 Z，两边一致才可比。
    const at = new Date(2026, 10, 15, 9, 5, 3);
    const input = new Date(2026, 10, 15, 9, 5, 3).toISOString();
    expect(fmtFullDateTime(input)).toBe(fmtFullDateTime(at.toISOString()));
  });

  it("缺值与非法值给占位符", () => {
    expect(fmtFullDateTime("")).toBe("—");
    expect(fmtFullDateTime(null)).toBe("—");
    expect(fmtFullDateTime("not-a-date")).toBe("—");
  });

  it("同一天的输入渲染后日期与时、分、秒逐位一致", () => {
    const at = new Date(2026, 10, 15, 9, 5, 3);
    const rendered = fmtFullDateTime(at.toISOString());
    expect(rendered.endsWith("09:05:03")).toBe(true);
  });
});

describe("fmtDuration", () => {
  it("秒 < 60 只显示秒", () => {
    expect(fmtDuration(45)).toBe("45s");
  });

  it("分钟档", () => {
    expect(fmtDuration(125)).toBe("2m");
  });

  it("小时档", () => {
    expect(fmtDuration(7500)).toBe("2h 5m");
  });

  it("缺值给占位符", () => {
    expect(fmtDuration(null)).toBe("—");
  });
});
