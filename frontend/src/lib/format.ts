function parseDate(value: string): Date {
  let text = value.trim().replace(" ", "T");
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(text)) text = `${text}Z`;
  return new Date(text);
}

export function formatDate(value: string | null | undefined, opts: Intl.DateTimeFormatOptions = {}): string {
  if (!value) return "—";
  const date = parseDate(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    ...opts,
  }).format(date);
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return "never";
  const date = parseDate(value);
  if (Number.isNaN(date.getTime())) return "never";
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  const abs = Math.abs(seconds);
  const suffix = seconds >= 0 ? "ago" : "from now";
  if (abs < 45) return seconds >= 0 ? "just now" : "in a moment";
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [3600, "minute"],
    [86400, "hour"],
    [2592000, "day"],
    [31536000, "month"],
  ];
  let divisor = 1;
  let unit: Intl.RelativeTimeFormatUnit = "second";
  for (const [limit, name] of units) {
    if (abs < limit) break;
    divisor = limit;
    unit = name === "second" ? "minute" : name === "minute" ? "hour" : name === "hour" ? "day" : name === "day" ? "month" : "year";
  }
  const amount = Math.round(seconds / divisor);
  return `${Math.abs(amount)} ${unit}${Math.abs(amount) === 1 ? "" : "s"} ${suffix}`;
}

export function formatNumber(value: number | null | undefined, fractionDigits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: fractionDigits,
    minimumFractionDigits: fractionDigits,
  }).format(value);
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${formatNumber(amount, amount >= 100 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${formatNumber(value, 1)}%`;
}

export function formatBps(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1000) return `${formatNumber(value, 0)} bps`;
  if (value < 1_000_000) return `${formatNumber(value / 1000, 1)} kbps`;
  if (value < 1_000_000_000) return `${formatNumber(value / 1_000_000, 1)} Mbps`;
  return `${formatNumber(value / 1_000_000_000, 2)} Gbps`;
}

export function statusTone(status: string | null | undefined): string {
  switch ((status ?? "").toLowerCase()) {
    case "online":
    case "ok":
    case "active":
    case "applied":
    case "enabled":
    case "success":
    case "accepted":
    case "synchronized":
      return "green";
    case "pending":
    case "pending_approval":
    case "approved":
    case "warning":
    case "configured":
    case "draft":
    case "rotated":
      return "amber";
    case "dry_run_failed":
    case "failed":
    case "error":
    case "exhausted":
    case "critical":
      return "red";
    case "unsupported":
      return "violet";
    case "disabled":
    case "expired":
    case "stale":
    case "unknown":
    case "cancelled":
    case "rejected":
      return "slate";
    default:
      return "slate";
  }
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function truncate(value: string, max = 48): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max - 1)}…`;
}

export function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

export function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
