"""The second fixture repo, acme-admin.

It shares file paths and symbol names with acme-shop (A-3: tools address files
by path only, so two repos under one connector merge) and one byte-identical
file (A-7 across repos).
"""
from __future__ import annotations

from textwrap import dedent

from . import backend as shop
from .model import Repo

B = "backend/app"
DB = f"{B}/shared/db.py"
RETRY = f"{B}/shared/retry.py"
AUDIT = f"{B}/shared/audit.py"
MODELS = f"{B}/admin/models.py"
SERVICE = f"{B}/admin/service.py"
POLICIES = f"{B}/admin/policies.py"
ROUTES = f"{B}/admin/routes.py"
EXPORTS_PY = f"{B}/reports/exports.py"
SCHEDULE = f"{B}/reports/schedule.py"
MAIN = f"{B}/main.py"
FAKES = "backend/tests/fakes.py"

HTTP = "frontend/src/lib/http.ts"
FORMAT = "frontend/src/lib/format.ts"


def _src(text: str) -> str:
    return dedent(text).lstrip("\n")


def build(repo: Repo, step: int) -> None:
    _backend(repo)
    _frontend(repo)
    repo.add("README.md", "# acme-admin\n\nInternal admin console for Acme support staff.\n",
             lang=None, role="docs")


def _backend(repo: Repo) -> None:
    for pkg in (B, f"{B}/shared", f"{B}/admin", f"{B}/reports"):
        repo.add(f"{pkg}/__init__.py", "")
    repo.add("backend/pyproject.toml", '[project]\nname = "acme-admin"\nversion = "0.9.0"\n',
             role="config")

    (repo.add(DB, _src('''
        """Read-only replica access for the admin console."""
        import sqlite3


        class Database:
            def __init__(self, path: str) -> None:
                self.conn = sqlite3.connect(path)

            def query(self, sql: str, *params) -> list[tuple]:
                return self.conn.execute(sql, params).fetchall()

            def scalar(self, sql: str, *params):
                rows = self.query(sql, *params)
                return rows[0][0] if rows else None
    '''), tags=("A-3",))
        .sym("class:Database", "method:Database.__init__", "method:Database.query",
             "method:Database.scalar")
        .calls("method:Database.scalar", DB, "method:Database.query", "self-member"))

    (repo.add(RETRY, _shop_retry_source(), tags=("A-7", "A-3"))
        .sym("function:backoff_delay", "function:retry_with_backoff")
        .calls("function:retry_with_backoff", RETRY, "function:backoff_delay"))

    (repo.add(AUDIT, _src('''
        from dataclasses import dataclass, field
        import time


        @dataclass
        class AuditEntry:
            actor: str
            action: str
            target: str
            at: float = field(default_factory=time.time)


        class AuditLog:
            def __init__(self) -> None:
                self.entries: list[AuditEntry] = []

            def record(self, actor: str, action: str, target: str) -> AuditEntry:
                entry = AuditEntry(actor, action, target)
                self.entries.append(entry)
                return entry

            def by_actor(self, actor: str) -> list[AuditEntry]:
                return [e for e in self.entries if e.actor == actor]
    '''))
        .sym("class:AuditEntry", "class:AuditLog", "method:AuditLog.__init__",
             "method:AuditLog.record", "method:AuditLog.by_actor")
        .calls("method:AuditLog.record", AUDIT, "class:AuditEntry", "ctor"))

    (repo.add(MODELS, _src('''
        from dataclasses import dataclass


        @dataclass
        class StaffUser:
            id: str
            email: str
            role: str = "support"

            def can(self, permission: str) -> bool:
                return permission in ROLE_PERMISSIONS.get(self.role, ())


        ROLE_PERMISSIONS = {
            "support": ("read",),
            "admin": ("read", "refund", "suspend"),
        }
    '''))
        .sym("class:StaffUser", "method:StaffUser.can"))

    (repo.add(POLICIES, _src('''
        from app.admin.models import StaffUser


        class PermissionDenied(Exception):
            pass


        def require_permission(user: StaffUser, permission: str) -> None:
            if not user.can(permission):
                raise PermissionDenied(f"{user.email} cannot {permission}")
    '''))
        .sym("class:PermissionDenied", "function:require_permission")
        .imports(MODELS, "class:StaffUser")
        .calls("function:require_permission", MODELS, "method:StaffUser.can", "typed-member")
        .calls("function:require_permission", POLICIES, "class:PermissionDenied", "ctor"))

    (repo.add(SERVICE, _src('''
        from app.admin.models import StaffUser
        from app.admin.policies import require_permission
        from app.shared.audit import AuditLog
        from app.shared.db import Database
        from app.shared.retry import retry_with_backoff


        class AdminService:
            def __init__(self, db: Database, audit: AuditLog) -> None:
                self.db = db
                self.audit = audit

            def refund(self, user: StaffUser, order_id: str) -> bool:
                require_permission(user, "refund")
                self.audit.record(user.id, "refund", order_id)
                return retry_with_backoff(lambda: self._mark_refunded(order_id))

            def suspend(self, user: StaffUser, customer_id: str) -> None:
                require_permission(user, "suspend")
                self.audit.record(user.id, "suspend", customer_id)

            def order_count(self) -> int:
                return self.db.scalar("select count(*) from orders") or 0

            def _mark_refunded(self, order_id: str) -> bool:
                return bool(self.db.query("select 1 from orders where id = ?", order_id))
    '''))
        .sym("class:AdminService", "method:AdminService.__init__", "method:AdminService.refund",
             "method:AdminService.suspend", "method:AdminService.order_count",
             "method:AdminService._mark_refunded")
        .imports(MODELS, "class:StaffUser")
        .imports(POLICIES, "function:require_permission")
        .imports(AUDIT, "class:AuditLog")
        .imports(DB, "class:Database")
        .imports(RETRY, "function:retry_with_backoff")
        .calls("method:AdminService.refund", POLICIES, "function:require_permission")
        .calls("method:AdminService.refund", AUDIT, "method:AuditLog.record", "attr-member")
        .calls("method:AdminService.refund", RETRY, "function:retry_with_backoff")
        .calls("method:AdminService.refund", SERVICE, "method:AdminService._mark_refunded",
               "self-member")
        .calls("method:AdminService.suspend", POLICIES, "function:require_permission")
        .calls("method:AdminService.suspend", AUDIT, "method:AuditLog.record", "attr-member")
        .calls("method:AdminService.order_count", DB, "method:Database.scalar", "attr-member")
        .calls("method:AdminService._mark_refunded", DB, "method:Database.query", "attr-member"))

    (repo.add(ROUTES, _src('''
        from app.admin.models import StaffUser
        from app.admin.service import AdminService

        _service: AdminService | None = None


        def bind(service: AdminService) -> None:
            global _service
            _service = service


        def refund_order(user: StaffUser, order_id: str) -> dict:
            return {"refunded": _service.refund(user, order_id)}


        def dashboard() -> dict:
            return {"orders": _service.order_count()}
    '''))
        .sym("function:bind", "function:refund_order", "function:dashboard")
        .imports(MODELS, "class:StaffUser")
        .imports(SERVICE, "class:AdminService")
        .calls("function:refund_order", SERVICE, "method:AdminService.refund", "typed-member")
        .calls("function:dashboard", SERVICE, "method:AdminService.order_count", "typed-member"))

    (repo.add(EXPORTS_PY, _src('''
        import csv
        import io

        from app.shared.db import Database


        def export_refunds(db: Database) -> str:
            out = io.StringIO()
            writer = csv.writer(out)
            for row in db.query("select id, amount from refunds"):
                writer.writerow(row)
            return out.getvalue()
    '''))
        .sym("function:export_refunds")
        .imports(DB, "class:Database")
        .calls("function:export_refunds", DB, "method:Database.query", "typed-member"))
    (repo.add(SCHEDULE, _src('''
        from app.reports.exports import export_refunds
        from app.shared.db import Database
        from app.shared.retry import retry_with_backoff


        def nightly(db: Database) -> str:
            return retry_with_backoff(lambda: export_refunds(db))
    '''))
        .sym("function:nightly")
        .imports(EXPORTS_PY, "function:export_refunds")
        .imports(DB, "class:Database")
        .imports(RETRY, "function:retry_with_backoff")
        .calls("function:nightly", RETRY, "function:retry_with_backoff")
        .calls("function:nightly", EXPORTS_PY, "function:export_refunds"))

    (repo.add(MAIN, _src('''
        from app.admin import routes
        from app.admin.service import AdminService
        from app.shared.audit import AuditLog
        from app.shared.db import Database


        def create_app(replica_path: str = "/var/lib/acme/replica.db") -> None:
            routes.bind(AdminService(Database(replica_path), AuditLog()))
    '''), tags=("A-3",))
        .sym("function:create_app")
        .imports(ROUTES, cat="namespace")
        .imports(SERVICE, "class:AdminService")
        .imports(AUDIT, "class:AuditLog")
        .imports(DB, "class:Database")
        .calls("function:create_app", ROUTES, "function:bind", "namespace")
        .calls("function:create_app", SERVICE, "class:AdminService", "ctor")
        .calls("function:create_app", DB, "class:Database", "ctor")
        .calls("function:create_app", AUDIT, "class:AuditLog", "ctor"))

    repo.add("backend/tests/__init__.py", "", role="test")
    (repo.add(FAKES, _src('''
        from app.shared.db import Database


        class MemoryDatabase(Database):
            def __init__(self) -> None:
                super().__init__(":memory:")
    '''), role="test")
        .sym("class:MemoryDatabase", "method:MemoryDatabase.__init__")
        .imports(DB, "class:Database")
        .inherits("class:MemoryDatabase", DB, "class:Database")
        .overrides("method:MemoryDatabase.__init__", DB, "method:Database.__init__")
        .calls("method:MemoryDatabase.__init__", DB, "method:Database.__init__", "super"))
    (repo.add("backend/tests/test_admin_service.py", _src('''
        from app.admin.models import StaffUser
        from app.admin.service import AdminService
        from app.shared.audit import AuditLog
        from tests.fakes import MemoryDatabase


        def test_support_cannot_refund():
            service = AdminService(MemoryDatabase(), AuditLog())
            try:
                service.refund(StaffUser("u1", "s@acme.test"), "o1")
            except Exception as exc:
                assert "cannot refund" in str(exc)
    '''), role="test")
        .sym("function:test_support_cannot_refund")
        .imports(MODELS, "class:StaffUser")
        .imports(SERVICE, "class:AdminService")
        .imports(AUDIT, "class:AuditLog")
        .imports(FAKES, "class:MemoryDatabase")
        .calls("function:test_support_cannot_refund", SERVICE, "class:AdminService", "ctor")
        .calls("function:test_support_cannot_refund", FAKES, "class:MemoryDatabase", "ctor")
        .calls("function:test_support_cannot_refund", AUDIT, "class:AuditLog", "ctor")
        .calls("function:test_support_cannot_refund", SERVICE, "method:AdminService.refund",
               "typed-member")
        .calls("function:test_support_cannot_refund", MODELS, "class:StaffUser", "ctor"))

    repo.tool_case(tool="read_code", id="A-3-same-path-two-repos", tags=["A-3"],
                   args={"file_path": MAIN},
                   expect={"distinguishes_repos": True,
                           "repos": ["pipeshub-codegraph-fixture", "pipeshub-codegraph-fixture-admin"]})
    repo.tool_case(tool="query_code_graph", id="A-3-same-symbol-two-repos", tags=["A-3"],
                   args={"name": "Database", "file_path": DB},
                   expect={"distinguishes_repos": True})


def _shop_retry_source() -> str:
    probe = Repo("probe")
    shop._shared(probe, 0, "require")
    return probe.files[shop.RETRY].content.decode()


def _frontend(repo: Repo) -> None:
    repo.add("frontend/package.json", '{\n  "name": "acme-admin-web",\n  "private": true\n}\n',
             lang=None, role="config")
    repo.add("frontend/tsconfig.json", '{\n  "compilerOptions": {"jsx": "react-jsx", "strict": true}\n}\n',
             lang=None, role="config")

    (repo.add(HTTP, _src('''
        const BASE = "/admin-api";

        export async function getJson<T>(path: string): Promise<T> {
          const res = await fetch(BASE + path, { credentials: "include" });
          if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
          return (await res.json()) as T;
        }

        export async function postJson<T>(path: string, body: unknown): Promise<T> {
          const res = await fetch(BASE + path, {
            method: "POST",
            credentials: "include",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
          });
          if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
          return (await res.json()) as T;
        }
    '''), tags=("A-3",))
        .sym("function:getJson", "function:postJson")
        .exports("function:getJson", "function:postJson"))

    (repo.add(FORMAT, _src('''
        export function formatDate(ts: number): string {
          return new Date(ts * 1000).toISOString().slice(0, 10);
        }

        export const formatCount = (n: number): string => n.toLocaleString("en-US");
    '''))
        .sym("function:formatDate", "function:formatCount")
        .exports("function:formatDate", "function:formatCount"))

    api = {}
    for name, entity, path in (("users", "StaffUser", "/staff"), ("audit", "AuditEntry", "/audit"),
                               ("reports", "Report", "/reports")):
        p = f"frontend/src/api/{name}.ts"
        api[name] = p
        title = name.capitalize()
        (repo.add(p, _src(f'''
            import {{ getJson, postJson }} from "../lib/http";

            export interface {entity} {{
              id: string;
              label: string;
            }}

            export function list{title}(): Promise<{entity}[]> {{
              return getJson<{entity}[]>("{path}");
            }}

            export function create{title}(input: Omit<{entity}, "id">): Promise<{entity}> {{
              return postJson<{entity}>("{path}", input);
            }}
        '''))
            .sym(f"interface:{entity}", f"function:list{title}", f"function:create{title}")
            .imports(HTTP, "function:getJson", "function:postJson")
            .exports(f"interface:{entity}", f"function:list{title}", f"function:create{title}")
            .calls(f"function:list{title}", HTTP, "function:getJson")
            .calls(f"function:create{title}", HTTP, "function:postJson"))

    hook = "frontend/src/hooks/useAdminUsers.ts"
    (repo.add(hook, _src('''
        import { useEffect, useState } from "react";
        import { listUsers, type StaffUser } from "../api/users";

        export function useAdminUsers(): StaffUser[] {
          const [users, setUsers] = useState<StaffUser[]>([]);
          useEffect(() => {
            listUsers().then(setUsers);
          }, []);
          return users;
        }
    '''))
        .sym("function:useAdminUsers")
        .imports(api["users"], "function:listUsers", "interface:StaffUser")
        .exports("function:useAdminUsers")
        .calls("function:useAdminUsers", api["users"], "function:listUsers"))

    comps = "frontend/src/components"
    (repo.add(f"{comps}/DataTable.tsx", _src('''
        export interface Column<T> {
          key: keyof T;
          title: string;
        }

        export function DataTable<T>({ rows, columns }: { rows: T[]; columns: Column<T>[] }) {
          return (
            <table>
              <thead>
                <tr>{columns.map((c) => <th key={String(c.key)}>{c.title}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i}>{columns.map((c) => <td key={String(c.key)}>{String(row[c.key])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          );
        }
    '''))
        .sym("interface:Column", "function:DataTable")
        .exports("interface:Column", "function:DataTable"))
    (repo.add(f"{comps}/Sidebar.tsx", _src('''
        const LINKS = ["Dashboard", "Users", "Audit", "Reports"];

        export function Sidebar({ active }: { active: string }) {
          return (
            <nav>
              {LINKS.map((l) => (
                <a key={l} className={l === active ? "active" : ""} href={`#${l.toLowerCase()}`}>
                  {l}
                </a>
              ))}
            </nav>
          );
        }
    '''))
        .sym("function:Sidebar")
        .exports("function:Sidebar"))
    (repo.add(f"{comps}/ConfirmDialog.tsx", _src('''
        export function ConfirmDialog({ message, onConfirm }: { message: string; onConfirm: () => void }) {
          return (
            <dialog open>
              <p>{message}</p>
              <button onClick={onConfirm}>Confirm</button>
            </dialog>
          );
        }
    '''))
        .sym("function:ConfirmDialog")
        .exports("function:ConfirmDialog"))

    pages = "frontend/src/pages"
    (repo.add(f"{pages}/Users.tsx", _src('''
        import { DataTable } from "../components/DataTable";
        import { useAdminUsers } from "../hooks/useAdminUsers";

        export function UsersPage() {
          const users = useAdminUsers();
          return <DataTable rows={users} columns={[{ key: "label", title: "Name" }]} />;
        }
    '''), tags=("P-5",))
        .sym("function:UsersPage")
        .imports(f"{comps}/DataTable.tsx", "function:DataTable")
        .imports(hook, "function:useAdminUsers")
        .exports("function:UsersPage")
        .calls("function:UsersPage", hook, "function:useAdminUsers")
        .calls("function:UsersPage", f"{comps}/DataTable.tsx", "function:DataTable", "jsx"))
    (repo.add(f"{pages}/Audit.tsx", _src('''
        import { useEffect, useState } from "react";
        import { listAudit, type AuditEntry } from "../api/audit";
        import { DataTable } from "../components/DataTable";

        export function AuditPage() {
          const [entries, setEntries] = useState<AuditEntry[]>([]);
          useEffect(() => {
            listAudit().then(setEntries);
          }, []);
          return <DataTable rows={entries} columns={[{ key: "label", title: "Action" }]} />;
        }
    '''), tags=("P-5",))
        .sym("function:AuditPage")
        .imports(api["audit"], "function:listAudit", "interface:AuditEntry")
        .imports(f"{comps}/DataTable.tsx", "function:DataTable")
        .exports("function:AuditPage")
        .calls("function:AuditPage", api["audit"], "function:listAudit")
        .calls("function:AuditPage", f"{comps}/DataTable.tsx", "function:DataTable", "jsx"))
    (repo.add(f"{pages}/Reports.tsx", _src('''
        import { createReports } from "../api/reports";
        import { ConfirmDialog } from "../components/ConfirmDialog";

        export function ReportsPage() {
          const run = () => createReports({ label: "refunds" });
          return <ConfirmDialog message="Generate the refunds report?" onConfirm={run} />;
        }
    '''), tags=("P-5",))
        .sym("function:ReportsPage", "function:ReportsPage.run")
        .imports(api["reports"], "function:createReports")
        .imports(f"{comps}/ConfirmDialog.tsx", "function:ConfirmDialog")
        .exports("function:ReportsPage")
        .calls("function:ReportsPage.run", api["reports"], "function:createReports")
        .calls("function:ReportsPage", f"{comps}/ConfirmDialog.tsx", "function:ConfirmDialog", "jsx"))
    (repo.add(f"{pages}/Dashboard.tsx", _src('''
        import { formatCount, formatDate } from "../lib/format";

        export function DashboardPage({ orders, since }: { orders: number; since: number }) {
          return (
            <section>
              <h1>{formatCount(orders)} orders</h1>
              <small>since {formatDate(since)}</small>
            </section>
          );
        }
    '''))
        .sym("function:DashboardPage")
        .imports(FORMAT, "function:formatCount", "function:formatDate")
        .exports("function:DashboardPage")
        .calls("function:DashboardPage", FORMAT, "function:formatCount")
        .calls("function:DashboardPage", FORMAT, "function:formatDate"))

    app = "frontend/src/App.tsx"
    (repo.add(app, _src('''
        import { useState } from "react";
        import { Sidebar } from "./components/Sidebar";
        import { AuditPage } from "./pages/Audit";
        import { DashboardPage } from "./pages/Dashboard";
        import { ReportsPage } from "./pages/Reports";
        import { UsersPage } from "./pages/Users";

        export default function App() {
          const [page, setPage] = useState("Dashboard");
          return (
            <div onClick={() => setPage(page)}>
              <Sidebar active={page} />
              {page === "Users" && <UsersPage />}
              {page === "Audit" && <AuditPage />}
              {page === "Reports" && <ReportsPage />}
              {page === "Dashboard" && <DashboardPage orders={0} since={0} />}
            </div>
          );
        }
    '''), tags=("P-5",))
        .sym("function:App")
        .imports(f"{comps}/Sidebar.tsx", "function:Sidebar")
        .imports(f"{pages}/Audit.tsx", "function:AuditPage")
        .imports(f"{pages}/Dashboard.tsx", "function:DashboardPage")
        .imports(f"{pages}/Reports.tsx", "function:ReportsPage")
        .imports(f"{pages}/Users.tsx", "function:UsersPage")
        .exports("function:App"))
    for target, q in ((f"{comps}/Sidebar.tsx", "function:Sidebar"),
                      (f"{pages}/Users.tsx", "function:UsersPage"),
                      (f"{pages}/Audit.tsx", "function:AuditPage"),
                      (f"{pages}/Reports.tsx", "function:ReportsPage"),
                      (f"{pages}/Dashboard.tsx", "function:DashboardPage")):
        repo.file(app).calls("function:App", target, q, "jsx")
    (repo.add("frontend/src/main.tsx", _src('''
        import { createRoot } from "react-dom/client";
        import App from "./App";

        createRoot(document.getElementById("root")!).render(<App />);
    '''))
        .imports(app, "function:App", cat="default")
        .calls(None, app, "function:App", "jsx"))
