import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Filter,
  GitPullRequest,
  RefreshCw,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import { api, errorText } from "../lib/api";
import { formatDate, formatRelative, titleCase } from "../lib/format";
import type { ChangeAction, ChangeRequest, Paginated } from "../lib/types";
import { usePolling } from "../hooks/usePolling";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  Field,
  PageHeader,
  Pagination,
  Select,
  Spinner,
  StatCard,
} from "../components/ui";
import { DataTable } from "../components/DataTable";
import { ChangeDialog, useChangeWorkflow } from "../components/ChangeWorkflow";

interface ChangesResponse extends Paginated<ChangeRequest> {
  pending: number;
}

const STATUSES = [
  "pending_approval",
  "approved",
  "applied",
  "dry_run_failed",
  "failed",
  "cancelled",
  "rejected",
];

const PAGE_SIZES = [25, 50, 100, 200];

function riskTone(risk: string): "green" | "amber" | "red" | "slate" | "violet" {
  if (risk === "critical") return "red";
  if (risk === "high") return "amber";
  if (risk === "medium") return "violet";
  if (risk === "low") return "green";
  return "slate";
}

function statusIcon(status: string) {
  if (status === "applied") return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
  if (status === "failed" || status === "dry_run_failed") return <XCircle className="h-4 w-4 text-rose-500" />;
  if (status === "pending_approval" || status === "approved") return <Clock className="h-4 w-4 text-amber-500" />;
  return <GitPullRequest className="h-4 w-4 text-slate-400" />;
}

export default function Changes() {
  const toast = useToast();
  const { can } = useAuth();
  const workflow = useChangeWorkflow();

  const [status, setStatus] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState("50");
  const [opening, setOpening] = useState<number | null>(null);

  const changes = usePolling<ChangesResponse>(
    "/changes",
    {
      status: status || undefined,
      action: action || undefined,
      page,
      page_size: Number(pageSize),
    },
    { interval: 5000 },
  );
  const catalog = usePolling<{ items: ChangeAction[] }>("/changes/actions", undefined, { interval: 0 });

  const rows = changes.data?.items ?? [];
  const total = changes.data?.total ?? 0;
  const pages = changes.data?.pages ?? 0;
  const pending = changes.data?.pending ?? 0;

  const actions = useMemo(() => catalog.data?.items ?? [], [catalog.data]);
  const appliedThisPage = rows.filter((row) => row.status === "applied").length;
  const failedThisPage = rows.filter((row) => ["failed", "dry_run_failed"].includes(row.status)).length;
  const riskyThisPage = rows.filter((row) => ["critical", "high"].includes(row.risk)).length;
  const activeFilters = [status, action].filter(Boolean).length;

  const applyFilters = () => {
    setPage(1);
    void changes.refresh();
  };

  const clearFilters = () => {
    setStatus("");
    setAction("");
    setPage(1);
  };

  const openChange = async (row: ChangeRequest) => {
    setOpening(row.id);
    try {
      const fresh = await api.get<ChangeRequest>(`/changes/${row.id}`);
      workflow.show(fresh);
    } catch (error) {
      toast.error("Could not load change request", errorText(error));
    } finally {
      setOpening(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Change Requests"
        description="Every host-mutating action becomes a typed change request: dry run, independent approval, then execution. The browser never supplies a shell command."
        breadcrumb="Overview"
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void changes.refresh();
              void catalog.refresh();
            }}
            loading={changes.loading}
          >
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
        }
      />

      {changes.error ? (
        <Alert tone="error" title="Change requests unavailable">
          {changes.error}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Pending review"
          value={pending}
          hint="Awaiting approval or execution"
          icon={<Clock className="h-5 w-5" />}
          tone={pending > 0 ? "amber" : "green"}
        />
        <StatCard
          label="Applied on this page"
          value={appliedThisPage}
          hint={`${rows.length} request(s) in the current page`}
          icon={<CheckCircle2 className="h-5 w-5" />}
          tone="green"
        />
        <StatCard
          label="Failed on this page"
          value={failedThisPage}
          hint="Dry run or execution failures"
          icon={<XCircle className="h-5 w-5" />}
          tone={failedThisPage > 0 ? "red" : "slate"}
        />
        <StatCard
          label="High risk on this page"
          value={riskyThisPage}
          hint="Critical or high-risk plans"
          icon={<AlertTriangle className="h-5 w-5" />}
          tone={riskyThisPage > 0 ? "amber" : "slate"}
        />
      </div>

      <Card>
        <CardHeader
          title="Filters"
          description={
            activeFilters
              ? `${activeFilters} filter${activeFilters === 1 ? "" : "s"} applied.`
              : "Newest requests first. Select a row to review its plan and act on it."
          }
          icon={<Filter className="h-4 w-4" />}
          actions={
            activeFilters ? (
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                <X className="h-4 w-4" /> Clear
              </Button>
            ) : undefined
          }
        />
        <div className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4">
          <Field label="Status">
            <Select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
            >
              <option value="">All statuses</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Action">
            <Select
              value={action}
              onChange={(event) => {
                setAction(event.target.value);
                setPage(1);
              }}
            >
              <option value="">All actions</option>
              {actions.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.title} ({item.name})
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Rows per page">
            <Select
              value={pageSize}
              onChange={(event) => {
                setPageSize(event.target.value);
                setPage(1);
              }}
            >
              {PAGE_SIZES.map((size) => (
                <option key={size} value={String(size)}>
                  {size}
                </option>
              ))}
            </Select>
          </Field>
          <div className="flex items-end">
            <Button variant="primary" size="sm" onClick={applyFilters} loading={changes.loading}>
              <Filter className="h-4 w-4" /> Apply filters
            </Button>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Change requests"
          description="Approval and execution permissions are enforced by the API. Critical requests require a different administrator unless the requester is a superadmin."
          icon={<GitPullRequest className="h-4 w-4" />}
        />
        {changes.loading && !changes.data ? <Spinner label="Loading change requests" /> : null}
        <DataTable
          rows={rows}
          rowKey={(row) => row.id}
          loading={changes.loading}
          empty="No change requests match these filters."
          columns={[
            {
              key: "id",
              header: "#",
              render: (row) => <span className="font-mono text-xs">{row.id}</span>,
            },
            {
              key: "action",
              header: "Action",
              render: (row) => (
                <div className="flex items-start gap-2">
                  {statusIcon(row.status)}
                  <div>
                    <code className="text-xs font-semibold">{row.action}</code>
                    <p className="text-[11px] text-slate-400">{row.target ?? "no target"}</p>
                  </div>
                </div>
              ),
            },
            {
              key: "status",
              header: "Status",
              render: (row) => (
                <Badge status={row.status} dot>
                  {titleCase(row.status)}
                </Badge>
              ),
            },
            {
              key: "risk",
              header: "Risk",
              render: (row) => <Badge tone={riskTone(row.risk)}>{titleCase(row.risk)}</Badge>,
            },
            {
              key: "requested",
              header: "Requested",
              render: (row) => (
                <div>
                  <p className="text-xs">{formatRelative(row.requested_at)}</p>
                  <p className="text-[11px] text-slate-400">by {row.requested_by}</p>
                </div>
              ),
            },
            {
              key: "approval",
              header: "Approval",
              render: (row) => (
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  {row.approved_by ? (
                    <span className="inline-flex items-center gap-1">
                      <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" /> {row.approved_by}
                    </span>
                  ) : row.rejected_by ? (
                    <span className="inline-flex items-center gap-1">
                      <XCircle className="h-3.5 w-3.5 text-rose-500" /> {row.rejected_by}
                    </span>
                  ) : (
                    "Not approved"
                  )}
                </div>
              ),
            },
            {
              key: "expires",
              header: "Expires",
              render: (row) => (
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {row.expires_at ? formatDate(row.expires_at) : "—"}
                </span>
              ),
            },
            {
              key: "actions",
              header: "",
              headerClassName: "text-right",
              className: "text-right",
              render: (row) => (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void openChange(row)}
                  loading={opening === row.id}
                >
                  Review
                </Button>
              ),
            },
          ]}
        />
        {changes.data ? (
          <Pagination page={page} pages={pages} total={total} pageSize={Number(pageSize)} onPage={setPage} />
        ) : null}
      </Card>

      <Alert tone="info" title="How a change flows">
        A page creates a change request with a typed payload. The backend renders a plan without touching the
        host. An administrator with <code>changes.approve</code> approves it. Only then can an account with{" "}
        <code>changes.execute</code> execute it, which first writes an automatic configuration backup. Failed
        or rejected requests are retained for the audit trail.
        {can("changes.request") ? null : " Your current role cannot create change requests."}
      </Alert>

      <ChangeDialog
        workflow={workflow}
        title="Change request"
        onUpdated={() => {
          void changes.refresh();
        }}
      />
    </div>
  );
}
