import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "./ui";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T, index: number) => ReactNode;
  className?: string;
  headerClassName?: string;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  empty,
  className,
  dense = false,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string | number;
  loading?: boolean;
  empty?: ReactNode;
  className?: string;
  dense?: boolean;
}) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="table-base">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={cn(dense && "py-2", column.headerClassName)}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading && rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>
                <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500 dark:text-slate-400">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                </div>
              </td>
            </tr>
          ) : null}
          {!loading && rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">
                {empty ?? "No records found."}
              </td>
            </tr>
          ) : null}
          {rows.map((row, index) => (
            <tr key={rowKey(row, index)}>
              {columns.map((column) => (
                <td key={column.key} className={cn(dense && "py-2", column.className)}>
                  {column.render(row, index)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
