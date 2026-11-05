export type Permission =
  | "read"
  | "pools.write"
  | "prefixes.write"
  | "clients.write"
  | "sessions.export"
  | "changes.request"
  | "changes.approve"
  | "changes.execute"
  | "users.write"
  | "backups.create"
  | "backups.restore"
  | "backups.delete"
  | "settings.write"
  | "dns64.write"
  | "firewall.write"
  | "network.write"
  | "jool.write"
  | "audit.read"
  | "*";

export interface User {
  id: number;
  username: string;
  email: string | null;
  full_name: string | null;
  role: string;
  is_active: boolean;
  is_superuser: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  last_login_ip: string | null;
  permissions: Permission[];
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  refresh_expires_in: number;
  user: User;
}

export interface ServiceStatus {
  status: string;
  message?: string | null;
  [key: string]: unknown;
}

export interface Dashboard {
  generated_at: string;
  services: {
    host: { mode: string; available: boolean; reason: string | null; platform: string };
    jool: { status: string; instances?: unknown[]; message?: string; capabilities?: Record<string, unknown> };
    dns64: { status: string; enabled: boolean; prefix: string; last_checked_at: string | null; last_error: string | null };
    firewall: { status: string; enabled: boolean; applied_at: string | null; last_error: string | null };
    network: { status: string; pending_confirmation: boolean; last_applied_at: string | null };
    database: { dialect: string };
    collector: { enabled: boolean; interval_seconds: number };
  };
  totals: {
    sessions_active: number;
    sessions_tcp: number;
    sessions_udp: number;
    sessions_icmp: number;
    clients_active: number;
    clients_known: number;
    pending_changes: number;
    expired_retained: number;
  };
  pool: PoolSummary;
  system: {
    cpu_percent: number | null;
    memory_percent: number | null;
    memory_used_bytes: number | null;
    memory_total_bytes: number | null;
    load1: number | null;
    net_rx_bps: number | null;
    net_tx_bps: number | null;
    captured_at: string | null;
  };
  traffic: { per_client_bytes: null; note: string };
  allocation: { mode: string; note: string; utilization_basis: string };
  top_clients: Client[];
  limitations: string[];
}

export interface PoolSummary {
  pool_count: number;
  address_count: number;
  enabled_address_count: number;
  active_address_count: number;
  transport_capacity: number;
  observed_ports_used: number;
  utilization_percent: number;
  usage_basis: string;
  default_port_range: [number, number];
}

export interface IPv4Address {
  id: number;
  pool_id: number;
  address: string;
  port_min: number;
  port_max: number;
  port_capacity: number;
  enabled: boolean;
  label: string | null;
  status: string;
  error_message: string | null;
  last_synced_at: string | null;
  last_session_at: string | null;
  tcp: number;
  udp: number;
  icmp: number;
  bindings: number;
  sessions: number;
  ports_used: number;
  ports_total: number;
  usage_percent: number;
  usage_basis: string;
}

export interface IPv4Pool {
  id: number;
  name: string;
  description: string | null;
  enabled: boolean;
  jool_instance_id: number | null;
  jool_instance_name: string | null;
  color: string | null;
  created_at: string;
  address_count: number;
  enabled_address_count: number;
  addresses: IPv4Address[];
  total_capacity: number;
  total_used: number;
  usage_percent: number;
  sessions_total: number;
}

export interface IPv6Prefix {
  id: number;
  prefix: string;
  name: string | null;
  interface: string | null;
  vlan_id: number | null;
  is_client_prefix: boolean;
  enabled: boolean;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface Client {
  id: number;
  address: string;
  hostname: string | null;
  mac_address: string | null;
  interface: string | null;
  vlan_id: number | null;
  prefix_id: number | null;
  prefix: string | null;
  active_session_count: number;
  is_blocked: boolean;
  first_seen_at: string | null;
  last_seen_at: string | null;
  notes: string | null;
  identity_note: string;
}

export interface Session {
  id: number;
  instance_id: number;
  instance_name: string | null;
  protocol: string;
  client_ipv6: string;
  client_port: number;
  translated_ipv4: string;
  translated_port: number;
  remote_ipv4: string;
  remote_port: number;
  expires_in_seconds: number;
  jool_state: string | null;
  status: string;
  first_seen_at: string | null;
  last_seen_at: string | null;
  expired_at: string | null;
  bytes_up: null;
  bytes_down: null;
  traffic_note: string;
}

export interface JoolInstance {
  id: number;
  name: string;
  display_name: string | null;
  framework: string;
  namespace: string | null;
  pool6: string;
  observed_pool6: string | null;
  enabled: boolean;
  paused: boolean;
  status: string;
  jool_version: string | null;
  last_seen_at: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  config_version: number;
  active_sessions: number;
  pool_count: number;
  created_at: string;
  updated_at: string;
  observed_pool4?: unknown;
  observed_stats?: unknown;
}

export interface Plan {
  action: string;
  summary: string;
  risk: string;
  target: string | null;
  warnings: string[];
  errors: string[];
  commands: string[][];
  details: Record<string, unknown>;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  reversible: boolean;
  requires_maintenance: boolean;
  host_available: boolean | null;
  valid: boolean;
}

export interface ChangeRequest {
  id: number;
  action: string;
  target: string | null;
  summary: string | null;
  risk: string;
  status: string;
  requested_by: string;
  approved_by: string | null;
  rejected_by: string | null;
  requested_at: string;
  approved_at: string | null;
  executed_at: string | null;
  expires_at: string | null;
  plan: Plan | null;
  result: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
  payload: Record<string, unknown>;
}

export interface DNS64Settings {
  id: number;
  enabled: boolean;
  prefix: string;
  upstreams: string[];
  listen_address: string;
  service_name: string;
  managed_config_path: string;
  test_domain: string;
  exclude_prefixes: string[];
  forward_only: boolean;
  dnssec_validation: boolean;
  last_status: string;
  last_checked_at: string | null;
  last_error: string | null;
  last_synthesis_ok: boolean | null;
  last_synthesis_at: string | null;
  config_version: number;
  last_render_hash: string | null;
  applied_at: string | null;
  preview: string;
  preview_error: string | null;
  managed_file_note: string;
}

export interface FirewallRule {
  id: number;
  name: string;
  description: string | null;
  enabled: boolean;
  priority: number;
  family: string;
  direction: string;
  action: string;
  protocol: string | null;
  source_cidr: string | null;
  destination_cidr: string | null;
  source_port: number | null;
  destination_port: number | null;
  icmp_type: string | null;
  log: boolean;
  last_applied_at: string | null;
}

export interface FirewallSettings {
  id: number;
  enabled: boolean;
  default_forward_policy: string;
  default_input_policy: string;
  new_connection_rate: number;
  rate_burst: number;
  log_dropped: boolean;
  management_allowlist: string[];
  management_ports: number[];
  protect_control_plane: boolean;
  syn_flood_protection: boolean;
  invalid_packet_protection: boolean;
  mss_clamping: boolean;
  config_version: number;
  last_render_hash: string | null;
  applied_at: string | null;
  last_error: string | null;
}

export interface NetworkSettings {
  id: number;
  wan4_interface: string | null;
  wan6_interface: string | null;
  lan6_interface: string | null;
  client_prefix: string | null;
  nat64_prefix: string;
  dns_server: string | null;
  default_gateway4: string | null;
  default_gateway6: string | null;
  mtu: number;
  ipv4_forwarding: boolean;
  ipv6_forwarding: boolean;
  manage_routes: boolean;
  manage_mtu: boolean;
  notes: string | null;
  config_version: number;
  last_render_hash: string | null;
  last_snapshot: string | null;
  pending_confirmation: boolean;
  last_applied_at: string | null;
  rollout_confirmed_at: string | null;
}

export interface NetworkInterface {
  id: number;
  name: string;
  mac_address: string | null;
  role: string | null;
  addresses?: { family: number; address: string; prefixlen: number; scope?: string }[];
  state: string | null;
  mtu: number | null;
  speed_mbps: number | null;
  driver: string | null;
  is_up: boolean;
  last_seen_at: string | null;
}

export interface Backup {
  id: number;
  name: string;
  kind: string;
  checksum: string;
  size_bytes: number;
  created_at: string;
  note: string | null;
  created_by: string | null;
  is_automatic: boolean;
  file_path?: string | null;
  counts?: {
    pools: number;
    prefixes: number;
    instances: number;
    firewall_rules: number;
  };
  content?: Record<string, unknown>;
  contents?: Record<string, unknown>;
}

export interface AuditEntry {
  id: number;
  username: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  success: boolean;
  detail: Record<string, unknown>;
  ip_address: string | null;
  user_agent: string | null;
  request_id: string | null;
  created_at: string;
}

export interface Paginated<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface HaSettings {
  id: number;
  enabled: boolean;
  mode: string;
  virtual_ip: string | null;
  vrrp_interface: string | null;
  vrrp_router_id: number;
  priority: number;
  peer_address: string | null;
  session_sync_enabled: boolean;
  session_sync_multicast: string | null;
  session_sync_interface: string | null;
  last_failover_at: string | null;
  last_error: string | null;
  auth_secret_set: boolean;
  node_name: string;
  role: string;
  heartbeat_seconds: number;
  warnings: string[];
}

export interface HaNode {
  id: number;
  name: string;
  address: string | null;
  api_url: string | null;
  role: string;
  enabled: boolean;
  status: string;
  last_seen_at: string | null;
  config_version: number;
  config_hash: string | null;
  jool_version: string | null;
  notes: string | null;
}

export interface WizardStepDefinition {
  id: number;
  key: string;
  title: string;
  required: boolean;
  description: string;
}

export interface WizardCheck {
  id?: number;
  step: number;
  key?: string;
  title?: string;
  status: string;
  message: string;
  detail?: Record<string, unknown>;
  required?: boolean;
  checked_at?: string;
}

export interface WizardCheckSummary {
  checks?: WizardCheck[];
  current_step?: number;
  complete?: boolean;
  required_failed?: number;
  checked_at?: string;
}

export interface WizardState {
  current_step: number;
  completed: boolean;
  started_at: string | null;
  completed_at: string | null;
  steps: Record<string, Record<string, unknown>>;
  last_check: WizardCheckSummary;
  definitions: WizardStepDefinition[];
  host_mode: string;
  host_available: boolean;
}

export interface Role {
  id: number;
  name: string;
  description: string | null;
  permissions: string[];
}

export interface AuthSession {
  id: number;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  ip_address: string | null;
  user_agent: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
}

export interface ChangeAction {
  name: string;
  title: string;
  permission: string | null;
}

export interface SystemMetric {
  cpu_percent: number | null;
  memory_percent: number | null;
  load1: number | null;
  net_rx_bps: number | null;
  net_tx_bps: number | null;
  captured_at: string;
  [key: string]: unknown;
}

export interface SettingItem {
  key: string;
  value: unknown;
  description: string | null;
  updated_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}
