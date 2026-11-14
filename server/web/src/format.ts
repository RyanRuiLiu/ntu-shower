/** 展示格式化：全站唯一的时刻/时长渲染出处。 */

const pad2 = (value: number) => String(value).padStart(2, "0");

/** RFC 3339 → `YYYY-MM-DD HH:MM:SS`；缺值或解析失败给占位符。 */
export function fmtFullDateTime(value?: string | null): string {
  if (!value) return "—";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "—";
  return (
    `${at.getFullYear()}-${pad2(at.getMonth() + 1)}-${pad2(at.getDate())} ` +
    `${pad2(at.getHours())}:${pad2(at.getMinutes())}:${pad2(at.getSeconds())}`
  );
}

/** 秒数 → 人读时长（`2h 5m` / `45s`）。 */
export function fmtDuration(seconds?: number | null): string {
  if (seconds === undefined || seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
