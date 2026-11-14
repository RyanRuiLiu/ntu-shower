import { describe, expect, it } from "vitest";

import { fmtDuration, fmtFullDateTime } from "./format";

describe("fmtFullDateTime", () => {
  it("把 RFC 3339 渲染为 YYYY-MM-DD HH:MM:SS", () => {
    expect(fmtFullDateTime("2026-11-15T09:05:03Z")).toBe("2026-11-15 17:05:03");
  });

  it("缺值与非法值给占位符", () => {
    expect(fmtFullDateTime("")).toBe("—");
    expect(fmtFullDateTime(null)).toBe("—");
    expect(fmtFullDateTime("not-a-date")).toBe("—");
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
