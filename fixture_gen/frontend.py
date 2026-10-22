"""The TypeScript / JavaScript half of acme-shop: ``frontend/``, ``bff/`` and ``worker/``.

Every file's relations are recorded next to its source, following
docs/answer-key.md. Placeholders in templates are ``__E__`` (entity),
``__K__`` (domain key), ``__T__`` (title) and ``__C__`` (camel entity), so the
TS template literals' ``${...}`` stay untouched.
"""
from __future__ import annotations

import posixpath

from .domains import Domain, active_domains
from .model import FileCtx, Repo, endpoint

FE = "frontend"
SRC = f"{FE}/src"
LIB = f"{SRC}/lib"
UI = f"{SRC}/components/ui"
BFF = "bff"
WK = "worker"

NUMERIC = frozenset({"total", "amount", "quantity", "rating", "percent", "value", "limit"})
# Domains whose cards import from format.ts by name (with an alias); the rest use
# a namespace import.
MONEY = frozenset({"orders", "payments", "billing"})


# -- helpers ------------------------------------------------------------------

def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(p.capitalize() for p in rest)


def _sub(text: str, d: Domain) -> str:
    return (text.replace("__E__", d.entity).replace("__K__", d.key)
            .replace("__T__", d.title).replace("__C__", d.camel))


def _spec(from_path: str, to_path: str, ext: str = "") -> str:
    """Relative import specifier from one file to another (extension dropped)."""
    target = to_path
    for suffix in (".tsx", ".ts", ".mts", ".cts", ".jsx", ".js"):
        if target.endswith(suffix):
            target = target[: -len(suffix)]
            break
    if target.endswith("/index"):
        target = target[: -len("/index")]
    rel = posixpath.relpath(target, posixpath.dirname(from_path))
    if not rel.startswith("."):
        rel = "./" + rel
    return rel + ext


class _Key:
    """Edge writer that drops exact duplicates (the model rejects them)."""

    def __init__(self, repo: Repo) -> None:
        self.seen = {(e["rel"], e["from"], e["to"]) for e in repo.edges}

    def edge(self, ctx: FileCtx, rel: str, src: str, dst: str, cat: str, note: str = "") -> None:
        key = (rel, src, dst)
        if key in self.seen:
            return
        self.seen.add(key)
        ctx._edge(rel, src, dst, cat, note)

    def call(self, ctx: FileCtx, from_q: str | None, to_path: str, to_q: str,
             cat: str = "direct", note: str = "") -> None:
        self.edge(ctx, "CALLS", endpoint(ctx.path, from_q), endpoint(to_path, to_q), cat, note)

    def imp(self, ctx: FileCtx, spec_file: str, defs: list[tuple[str, str]],
            cat: str = "named") -> None:
        """One import statement: IMPORTS per name, one IMPORTS_FROM to the specifier's file."""
        for path, q in defs:
            c = cat if (path == spec_file or cat != "named") else "barrel"
            self.edge(ctx, "IMPORTS", ctx.path, endpoint(path, q), c)
        self.edge(ctx, "IMPORTS_FROM", ctx.path, spec_file, cat)

    def reexp(self, ctx: FileCtx, to_path: str) -> None:
        self.edge(ctx, "RE_EXPORTS", ctx.path, to_path, "barrel")

    def exp(self, ctx: FileCtx, *qs: str, cat: str = "export") -> None:
        for q in qs:
            self.edge(ctx, "EXPORTS", ctx.path, endpoint(ctx.path, q), cat)

    def inh(self, ctx: FileCtx, from_q: str, to_path: str, to_q: str, rel: str) -> None:
        self.edge(ctx, rel, endpoint(ctx.path, from_q), endpoint(to_path, to_q), "heritage")

    def ovr(self, ctx: FileCtx, from_q: str, to_path: str, to_q: str) -> None:
        self.edge(ctx, "OVERRIDES", endpoint(ctx.path, from_q), endpoint(to_path, to_q), "override")


# -- domain paths ---------------------------------------------------------------

class _D:
    def __init__(self, d: Domain) -> None:
        self.d = d
        base = f"{SRC}/features/{d.key}"
        self.base = base
        self.types = f"{base}/types.ts"
        self.api = f"{base}/api.ts"
        self.store = f"{base}/store.ts"
        self.hook_name = f"use{d.title}"
        self.hook = f"{base}/hooks/{self.hook_name}.ts"
        self.card = f"{base}/components/{d.entity}Card.tsx"
        self.list = f"{base}/components/{d.entity}List.tsx"
        self.form = f"{base}/components/{d.entity}Form.tsx"
        self.index = f"{base}/index.ts"
        self.test = f"{base}/__tests__/{d.entity}List.test.tsx"
        e, t = d.entity, d.title
        self.fetch_all, self.fetch_one = f"fetch{t}", f"fetch{e}"
        self.create, self.update, self.remove = f"create{e}", f"update{e}", f"remove{e}"
        self.fields = [(_camel(f), "number" if f in NUMERIC else "string") for f in d.fields]


def _fields_ts(fields: list[tuple[str, str]], indent: str = "  ") -> str:
    return "\n".join(f"{indent}{name}: {typ};" for name, typ in fields)


def _sample(fields: list[tuple[str, str]]) -> str:
    return ", ".join(f'{n}: {"1" if t == "number" else repr("x").replace(chr(39), chr(34))}'
                     for n, t in fields)


# -- shared lib -------------------------------------------------------------------

def _lib(repo: Repo, k: _Key, fmt: str) -> None:
    clamp_src = """export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function clampInt(value: number, min: number, max: number): number {
  return Math.round(clamp(value, min, max));
}
"""
    c = repo.add(f"{LIB}/clamp.ts", clamp_src, tags=("A-7",))
    c.sym("function:clamp", "function:clampInt")
    k.exp(c, "function:clamp", "function:clampInt")
    k.call(c, "function:clampInt", c.path, "function:clamp")

    c = repo.add(f"{LIB}/env.mts", """export function readEnv(name: string, fallback = ""): string {
  const env = (import.meta as unknown as { env: Record<string, string | undefined> }).env;
  return env[name] ?? fallback;
}
""", tags=("esm-js-specifier",))
    c.sym("function:readEnv")
    k.exp(c, "function:readEnv")

    c = repo.add(f"{LIB}/legacyConfig.cts", """export interface LegacyFlags {
  newCheckout: boolean;
  compactTables: boolean;
}

export function legacyFlags(): LegacyFlags {
  return { newCheckout: true, compactTables: false };
}
""", tags=("esm-js-specifier",))
    c.sym("interface:LegacyFlags", "field:LegacyFlags.newCheckout", "field:LegacyFlags.compactTables",
          "function:legacyFlags")
    k.exp(c, "interface:LegacyFlags", "function:legacyFlags")

    api_client = f"{LIB}/apiClient.ts"
    c = repo.add(api_client, """export interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
}

export class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "HttpError";
  }
}

export class ApiClient {
  constructor(private readonly baseUrl: string) {}

  async fetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const response = await window.fetch(this.baseUrl + path, {
      method: options.method ?? "GET",
      headers: { "Content-Type": "application/json", ...options.headers },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (!response.ok) {
      throw new HttpError(response.status, await response.text());
    }
    return (await response.json()) as T;
  }

  get<T>(path: string): Promise<T> {
    return this.fetch<T>(path);
  }

  post<T>(path: string, body: unknown): Promise<T> {
    return this.fetch<T>(path, { method: "POST", body });
  }

  put<T>(path: string, body: unknown): Promise<T> {
    return this.fetch<T>(path, { method: "PUT", body });
  }

  delete<T>(path: string): Promise<T> {
    return this.fetch<T>(path, { method: "DELETE" });
  }
}
""", tags=("P-5",))
    c.sym("interface:RequestOptions", "field:RequestOptions.method", "field:RequestOptions.body",
          "field:RequestOptions.headers", "class:HttpError", "method:HttpError.constructor",
          "class:ApiClient", "method:ApiClient.constructor", "method:ApiClient.fetch",
          "method:ApiClient.get", "method:ApiClient.post", "method:ApiClient.put",
          "method:ApiClient.delete")
    k.exp(c, "interface:RequestOptions", "class:HttpError", "class:ApiClient")
    k.call(c, "method:ApiClient.fetch", api_client, "class:HttpError", "ctor")
    for m in ("get", "post", "put", "delete"):
        k.call(c, f"method:ApiClient.{m}", api_client, "method:ApiClient.fetch", "self-member",
               "member named like the fetch builtin")

    c = repo.add(f"{LIB}/http.ts", """import { ApiClient } from "./apiClient";
import { readEnv } from "./env.mjs";

const api = new ApiClient(readEnv("VITE_API_URL", ""));

export function getJson<T>(path: string): Promise<T> {
  return api.get<T>(path);
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return api.post<T>(path, body);
}

export function putJson<T>(path: string, body: unknown): Promise<T> {
  return api.put<T>(path, body);
}

export function deleteJson(path: string): Promise<void> {
  return api.delete<void>(path);
}
""", tags=("esm-js-specifier",))
    c.sym("function:getJson", "function:postJson", "function:putJson", "function:deleteJson")
    k.exp(c, "function:getJson", "function:postJson", "function:putJson", "function:deleteJson")
    k.imp(c, api_client, [(api_client, "class:ApiClient")])
    k.imp(c, f"{LIB}/env.mts", [(f"{LIB}/env.mts", "function:readEnv")], "esm-js-specifier")
    k.call(c, None, api_client, "class:ApiClient", "ctor")
    k.call(c, None, f"{LIB}/env.mts", "function:readEnv", "esm-js-specifier")
    for fn, m in (("getJson", "get"), ("postJson", "post"), ("putJson", "put"), ("deleteJson", "delete")):
        k.call(c, f"function:{fn}", api_client, f"method:ApiClient.{m}", "typed-member",
               "receiver typed by a module-level constructor assignment")

    c = repo.add(f"{LIB}/store.ts", """export type Listener<T> = (items: T[]) => void;

export abstract class BaseStore<T> {
  protected items: T[] = [];
  private listeners: Listener<T>[] = [];

  abstract load(): Promise<void>;

  getItems(): T[] {
    return this.items;
  }

  subscribe(listener: Listener<T>): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  protected setItems(items: T[]): void {
    this.items = items;
    this.notify();
  }

  private notify(): void {
    for (const listener of this.listeners) {
      listener(this.items);
    }
  }
}
""")
    c.sym("type_alias:Listener", "class:BaseStore", "field:BaseStore.items", "field:BaseStore.listeners",
          "method:BaseStore.load", "method:BaseStore.getItems", "method:BaseStore.subscribe",
          "method:BaseStore.setItems", "method:BaseStore.notify")
    k.exp(c, "type_alias:Listener", "class:BaseStore")
    k.call(c, "method:BaseStore.setItems", c.path, "method:BaseStore.notify", "self-member")

    clamp_spec = _spec(fmt, f"{LIB}/clamp.ts", ".js")
    c = repo.add(fmt, f"""import {{ clamp }} from "{clamp_spec}";

const currency = new Intl.NumberFormat("en-US", {{ style: "currency", currency: "USD" }});

export function formatMoney(value: number): string {{
  return currency.format(value);
}}

export function formatDate(iso: string): string {{
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("en-US");
}}

export function formatLabel(value: string): string {{
  return value.replace(/[_-]+/g, " ").replace(/\\b\\w/g, (ch) => ch.toUpperCase());
}}

export function formatPercent(value: number): string {{
  return `${{clamp(value, 0, 100).toFixed(0)}}%`;
}}
""", tags=("esm-js-specifier",))
    c.sym("function:formatMoney", "function:formatDate", "function:formatLabel", "function:formatPercent")
    k.exp(c, "function:formatMoney", "function:formatDate", "function:formatLabel", "function:formatPercent")
    k.imp(c, f"{LIB}/clamp.ts", [(f"{LIB}/clamp.ts", "function:clamp")], "esm-js-specifier")
    k.call(c, "function:formatPercent", f"{LIB}/clamp.ts", "function:clamp", "esm-js-specifier")

    c = repo.add(f"{LIB}/ids.ts", """export function newId(prefix = "id"): string {
  return `${prefix}_${randomPart()}`;
}

function randomPart(): string {
  return Math.random().toString(36).slice(2, 10);
}
""")
    c.sym("function:newId", "function:randomPart")
    k.exp(c, "function:newId")
    k.call(c, "function:newId", c.path, "function:randomPart")

    events = f"{LIB}/events.ts"
    c = repo.add(events, """import { newId } from "./ids.js";

export interface AppEvent<T = unknown> {
  id: string;
  name: string;
  payload: T;
  at: string;
}

export type Handler = (event: AppEvent) => void;

export class EventBus {
  private readonly handlers = new Map<string, Handler[]>();

  on(name: string, handler: Handler): () => void {
    const list = this.handlers.get(name) ?? [];
    this.handlers.set(name, [...list, handler]);
    return () => this.off(name, handler);
  }

  off(name: string, handler: Handler): void {
    this.handlers.set(name, (this.handlers.get(name) ?? []).filter((h) => h !== handler));
  }

  emit<T>(name: string, payload: T): AppEvent<T> {
    const event: AppEvent<T> = { id: newId("evt"), name, payload, at: new Date().toISOString() };
    for (const handler of this.handlers.get(name) ?? []) {
      handler(event as AppEvent);
    }
    return event;
  }
}

export function createBus(): EventBus {
  return new EventBus();
}
""", tags=("esm-js-specifier",))
    c.sym("interface:AppEvent", "field:AppEvent.id", "field:AppEvent.name", "field:AppEvent.payload",
          "field:AppEvent.at", "type_alias:Handler", "class:EventBus", "field:EventBus.handlers",
          "method:EventBus.on", "method:EventBus.off", "method:EventBus.emit", "function:createBus")
    k.exp(c, "interface:AppEvent", "type_alias:Handler", "class:EventBus", "function:createBus")
    k.imp(c, f"{LIB}/ids.ts", [(f"{LIB}/ids.ts", "function:newId")], "esm-js-specifier")
    k.call(c, "method:EventBus.on", events, "method:EventBus.off", "self-member")
    k.call(c, "method:EventBus.emit", f"{LIB}/ids.ts", "function:newId", "esm-js-specifier")
    k.call(c, "function:createBus", events, "class:EventBus", "ctor")

    c = repo.add(f"{LIB}/debounce.ts", """export function debounce<A extends unknown[]>(
  fn: (...args: A) => void,
  waitMs: number,
): (...args: A) => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return (...args: A) => {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
    timer = setTimeout(() => fn(...args), waitMs);
  };
}
""", tags=("R-1",))
    c.sym("function:debounce")
    k.exp(c, "function:debounce")


# -- ui components ------------------------------------------------------------------

def _ui(repo: Repo, k: _Key, fmt: str) -> None:
    c = repo.add(f"{UI}/Button.tsx", """import type { ButtonHTMLAttributes, ReactNode } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost";
  children: ReactNode;
}

export function Button({ variant = "primary", children, ...rest }: ButtonProps) {
  return (
    <button className={`btn btn-${variant}`} {...rest}>
      {children}
    </button>
  );
}
""")
    c.sym("interface:ButtonProps", "field:ButtonProps.variant", "field:ButtonProps.children", "function:Button")
    k.exp(c, "interface:ButtonProps", "function:Button")

    c = repo.add(f"{UI}/Modal.tsx", """import type { ReactNode } from "react";
import { Button } from "./Button";

export interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

export function Modal({ open, title, onClose, children }: ModalProps) {
  if (!open) {
    return null;
  }
  return (
    <div className="modal" role="dialog" aria-label={title}>
      <header>
        <h2>{title}</h2>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </header>
      <div className="modal-body">{children}</div>
    </div>
  );
}
""", tags=("P-5",))
    c.sym("interface:ModalProps", "field:ModalProps.open", "field:ModalProps.title", "field:ModalProps.onClose",
          "field:ModalProps.children", "function:Modal")
    k.exp(c, "interface:ModalProps", "function:Modal")
    k.imp(c, f"{UI}/Button.tsx", [(f"{UI}/Button.tsx", "function:Button")])
    k.call(c, "function:Modal", f"{UI}/Button.tsx", "function:Button", "jsx")

    c = repo.add(f"{UI}/Table.tsx", """import type { ReactNode } from "react";

export interface TableProps {
  title: string;
  children: ReactNode;
  empty?: string;
}

export function Table({ title, children, empty = "Nothing here yet" }: TableProps) {
  const rows = Array.isArray(children) ? children : [children];
  return (
    <section className="table">
      <h2>{title}</h2>
      {rows.length === 0 ? <p>{empty}</p> : rows}
    </section>
  );
}
""")
    c.sym("interface:TableProps", "field:TableProps.title", "field:TableProps.children", "field:TableProps.empty",
          "function:Table")
    k.exp(c, "interface:TableProps", "function:Table")

    c = repo.add(f"{UI}/StatusBadge.tsx", f"""import {{ formatLabel }} from "{_spec(f"{UI}/StatusBadge.tsx", fmt)}";

export interface StatusBadgeProps {{
  status: string;
}}

const TONES: Record<string, string> = {{ draft: "grey", active: "green", archived: "red" }};

export function StatusBadge({{ status }}: StatusBadgeProps) {{
  return <span className={{`badge badge-${{TONES[status] ?? "grey"}}`}}>{{formatLabel(status)}}</span>;
}}
""")
    c.sym("interface:StatusBadgeProps", "field:StatusBadgeProps.status", "function:StatusBadge")
    k.exp(c, "interface:StatusBadgeProps", "function:StatusBadge")
    k.imp(c, fmt, [(fmt, "function:formatLabel")], "relative")
    k.call(c, "function:StatusBadge", fmt, "function:formatLabel")

    c = repo.add(f"{UI}/Spinner.tsx", """export function Spinner({ label }: { label: string }) {
  return <div className="spinner" role="status" aria-label={label} />;
}
""")
    c.sym("function:Spinner")
    k.exp(c, "function:Spinner")

    ww = f"{SRC}/app/useWindowWidth.ts"
    c = repo.add(ww, """import { useEffect, useState } from "react";
import { debounce } from "../lib/debounce";

export function useWindowWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = debounce(() => setWidth(window.innerWidth), 100);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return width;
}
""", tags=("R-1",))
    c.sym("function:useWindowWidth")
    k.exp(c, "function:useWindowWidth")
    k.imp(c, f"{LIB}/debounce.ts", [(f"{LIB}/debounce.ts", "function:debounce")], "relative")
    k.call(c, "function:useWindowWidth", f"{LIB}/debounce.ts", "function:debounce")

    lay = f"{UI}/Layout.tsx"
    c = repo.add(lay, f"""import type {{ ReactNode }} from "react";
import {{ formatLabel }} from "{_spec(lay, fmt)}";
import {{ useWindowWidth }} from "../../app/useWindowWidth";

export interface NavItem {{
  label: string;
  href: string;
}}

export interface LayoutProps {{
  title: string;
  nav: NavItem[];
  onNavigate: (href: string) => void;
  children: ReactNode;
}}

export function Layout({{ title, nav, onNavigate, children }}: LayoutProps) {{
  const width = useWindowWidth();
  return (
    <div className={{width < 768 ? "layout compact" : "layout"}}>
      <nav>
        {{nav.map((item) => (
          <a
            key={{item.href}}
            href={{item.href}}
            onClick={{(event) => {{
              event.preventDefault();
              onNavigate(item.href);
            }}}}
          >
            {{formatLabel(item.label)}}
          </a>
        ))}}
      </nav>
      <main>
        <h1>{{title}}</h1>
        {{children}}
      </main>
    </div>
  );
}}
""")
    c.sym("interface:NavItem", "field:NavItem.label", "field:NavItem.href", "interface:LayoutProps",
          "field:LayoutProps.title", "field:LayoutProps.nav", "field:LayoutProps.onNavigate",
          "field:LayoutProps.children", "function:Layout")
    k.exp(c, "interface:NavItem", "interface:LayoutProps", "function:Layout")
    k.imp(c, fmt, [(fmt, "function:formatLabel")], "relative")
    k.imp(c, ww, [(ww, "function:useWindowWidth")], "relative")
    k.call(c, "function:Layout", ww, "function:useWindowWidth", "cross-dir")
    k.call(c, "function:Layout", fmt, "function:formatLabel")
    k.call(c, "function:Layout", c.path, "field:LayoutProps.onNavigate", "interface-dispatch")


# -- one feature domain ---------------------------------------------------------

def _domain(repo: Repo, k: _Key, dd: _D, idx: int, fmt: str) -> None:
    d, e = dd.d, dd.d.entity
    http = f"{LIB}/http.ts"

    c = repo.add(dd.types, _sub(f"""export type __E__Status = "draft" | "active" | "archived";

export interface __E__ {{
  id: string;
  status: __E__Status;
  createdAt: string;
{_fields_ts(dd.fields)}
}}

export interface __E__Input {{
{_fields_ts(dd.fields)}
}}
""", d))
    c.sym(f"type_alias:{e}Status", f"interface:{e}", f"field:{e}.id", f"field:{e}.status",
          f"field:{e}.createdAt", *(f"field:{e}.{n}" for n, _ in dd.fields),
          f"interface:{e}Input", *(f"field:{e}Input.{n}" for n, _ in dd.fields))
    k.exp(c, f"type_alias:{e}Status", f"interface:{e}", f"interface:{e}Input")

    c = repo.add(dd.api, _sub(f"""import {{ deleteJson, getJson, postJson, putJson }} from "{_spec(dd.api, http)}";
import type {{ __E__, __E__Input }} from "./types";

const BASE = "/api/__K__";

export function {dd.fetch_all}(): Promise<__E__[]> {{
  return getJson<__E__[]>(BASE);
}}

export function {dd.fetch_one}(id: string): Promise<__E__> {{
  return getJson<__E__>(`${{BASE}}/${{id}}`);
}}

export function {dd.create}(input: __E__Input): Promise<__E__> {{
  return postJson<__E__>(BASE, input);
}}

export function {dd.update}(id: string, input: Partial<__E__Input>): Promise<__E__> {{
  return putJson<__E__>(`${{BASE}}/${{id}}`, input);
}}

export function {dd.remove}(id: string): Promise<void> {{
  return deleteJson(`${{BASE}}/${{id}}`);
}}
""", d))
    fns = (dd.fetch_all, dd.fetch_one, dd.create, dd.update, dd.remove)
    c.sym(*(f"function:{f}" for f in fns))
    k.exp(c, *(f"function:{f}" for f in fns))
    k.imp(c, http, [(http, f"function:{n}") for n in ("deleteJson", "getJson", "postJson", "putJson")],
          "relative")
    k.imp(c, dd.types, [(dd.types, f"interface:{e}"), (dd.types, f"interface:{e}Input")], "relative")
    for fn, h in ((dd.fetch_all, "getJson"), (dd.fetch_one, "getJson"), (dd.create, "postJson"),
                  (dd.update, "putJson"), (dd.remove, "deleteJson")):
        k.call(c, f"function:{fn}", http, f"function:{h}", "cross-dir")

    store = f"{LIB}/store.ts"
    c = repo.add(dd.store, _sub(f"""import {{ BaseStore }} from "{_spec(dd.store, store)}";
import {{ {dd.fetch_all} }} from "./api";
import type {{ __E__ }} from "./types";

export class __E__Store extends BaseStore<__E__> {{
  async load(): Promise<void> {{
    this.setItems(await {dd.fetch_all}());
  }}

  findById(id: string): __E__ | undefined {{
    return this.getItems().find((item) => item.id === id);
  }}
}}
""", d))
    sc = f"class:{e}Store"
    c.sym(sc, f"method:{e}Store.load", f"method:{e}Store.findById")
    k.exp(c, sc)
    k.imp(c, store, [(store, "class:BaseStore")], "relative")
    k.imp(c, dd.api, [(dd.api, f"function:{dd.fetch_all}")], "relative")
    k.imp(c, dd.types, [(dd.types, f"interface:{e}")], "relative")
    k.inh(c, sc, store, "class:BaseStore", "INHERITS")
    k.ovr(c, f"method:{e}Store.load", store, "method:BaseStore.load")
    k.call(c, f"method:{e}Store.load", store, "method:BaseStore.setItems", "inherited-member")
    k.call(c, f"method:{e}Store.load", dd.api, f"function:{dd.fetch_all}")
    k.call(c, f"method:{e}Store.findById", store, "method:BaseStore.getItems", "inherited-member")

    c = repo.add(dd.hook, _sub(f"""import {{ useEffect, useMemo, useState }} from "react";
import {{ __E__Store }} from "../store";
import type {{ __E__ }} from "../types";

export function {dd.hook_name}(): {{ items: __E__[]; loading: boolean }} {{
  const store = useMemo(() => new __E__Store(), []);
  const [items, setItems] = useState<__E__[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {{
    const unsubscribe = store.subscribe(setItems);
    store.load().finally(() => setLoading(false));
    return unsubscribe;
  }}, [store]);

  return {{ items, loading }};
}}
""", d))
    hq = f"function:{dd.hook_name}"
    c.sym(hq)
    k.exp(c, hq)
    k.imp(c, dd.store, [(dd.store, sc)], "relative")
    k.imp(c, dd.types, [(dd.types, f"interface:{e}")], "relative")
    k.call(c, hq, dd.store, sc, "ctor")
    k.call(c, hq, store, "method:BaseStore.subscribe", "typed-member", "type inferred through useMemo")
    k.call(c, hq, dd.store, f"method:{e}Store.load", "typed-member", "type inferred through useMemo")

    # Card: half the domains import format helpers by name (with an alias), half as a namespace.
    badge = f"{UI}/StatusBadge.tsx"
    first, first_t = dd.fields[0]
    shown = f"String(item.{first})" if first_t == "number" else f"item.{first}"
    cq = f"function:{e}Card"
    alias = idx % 2 == 0
    if alias:
        names = ["formatDate", "formatLabel as label"] + (["formatMoney as fm"] if d.key in MONEY else [])
        fmt_import = f'import {{ {", ".join(names)} }} from "{_spec(dd.card, fmt)}";'
        date_call, label_call = "formatDate", "label"
    else:
        fmt_import = f'import * as fmt from "{_spec(dd.card, fmt)}";'
        date_call, label_call = "fmt.formatDate", "fmt.formatLabel"
    money_line = ""
    if alias and d.key in MONEY:
        money_line = f"\n      <span className=\"amount\">{{fm(item.{dd.fields[1][0]})}}</span>"
    c = repo.add(dd.card, _sub(f"""{fmt_import}
import {{ StatusBadge }} from "../../../components/ui/StatusBadge";
import type {{ __E__ }} from "../types";

interface __E__CardProps {{
  item: __E__;
  onSelect?: (id: string) => void;
}}

export function __E__Card({{ item, onSelect }}: __E__CardProps) {{
  return (
    <div className="card" onClick={{() => onSelect?.(item.id)}}>
      <h3>{{{label_call}({shown})}}</h3>
      <StatusBadge status={{item.status}} />
      <time>{{{date_call}(item.createdAt)}}</time>{money_line}
    </div>
  );
}}
""", d), tags=("P-5",))
    c.sym(f"interface:{e}CardProps", f"field:{e}CardProps.item", f"field:{e}CardProps.onSelect", cq)
    k.exp(c, cq)
    k.call(c, cq, c.path, f"field:{e}CardProps.onSelect", "interface-dispatch")
    if alias:
        defs = [(fmt, "function:formatDate"), (fmt, "function:formatLabel")]
        if d.key in MONEY:
            defs.append((fmt, "function:formatMoney"))
        k.imp(c, fmt, defs, "alias")
        k.call(c, cq, fmt, "function:formatDate", "cross-dir")
        k.call(c, cq, fmt, "function:formatLabel", "alias")
        if d.key in MONEY:
            k.call(c, cq, fmt, "function:formatMoney", "alias")
    else:
        k.edge(c, "IMPORTS_FROM", c.path, fmt, "namespace")
        k.call(c, cq, fmt, "function:formatDate", "namespace")
        k.call(c, cq, fmt, "function:formatLabel", "namespace")
    k.imp(c, badge, [(badge, "function:StatusBadge")], "relative")
    k.imp(c, dd.types, [(dd.types, f"interface:{e}")], "relative")
    k.call(c, cq, badge, "function:StatusBadge", "jsx")

    lq = f"function:{e}List"
    c = repo.add(dd.list, _sub(f"""import {{ Spinner }} from "../../../components/ui/Spinner";
import {{ Table }} from "../../../components/ui/Table";
import {{ {dd.hook_name} }} from "../hooks/{dd.hook_name}";
import {{ __E__Card }} from "./__E__Card";

export function __E__List({{ onSelect }}: {{ onSelect?: (id: string) => void }}) {{
  const {{ items, loading }} = {dd.hook_name}();
  if (loading) {{
    return <Spinner label="Loading __K__" />;
  }}
  return (
    <Table title="__T__">
      {{items.map((item) => (
        <__E__Card key={{item.id}} item={{item}} onSelect={{onSelect}} />
      ))}}
    </Table>
  );
}}
""", d), tags=("P-5",))
    c.sym(lq)
    k.exp(c, lq)
    k.imp(c, f"{UI}/Spinner.tsx", [(f"{UI}/Spinner.tsx", "function:Spinner")], "relative")
    k.imp(c, f"{UI}/Table.tsx", [(f"{UI}/Table.tsx", "function:Table")], "relative")
    k.imp(c, dd.hook, [(dd.hook, hq)], "relative")
    k.imp(c, dd.card, [(dd.card, cq)], "relative")
    k.call(c, lq, dd.hook, hq)
    k.call(c, lq, f"{UI}/Spinner.tsx", "function:Spinner", "jsx")
    k.call(c, lq, f"{UI}/Table.tsx", "function:Table", "jsx")
    k.call(c, lq, dd.card, cq, "jsx")

    fq = f"function:{e}Form"
    empty = ", ".join(f'{n}: {"0" if t == "number" else chr(34) * 2}' for n, t in dd.fields)
    conv = f"Number(event.target.value)" if first_t == "number" else "event.target.value"
    c = repo.add(dd.form, _sub(f"""import {{ useState }} from "react";
import {{ Button }} from "../../../components/ui/Button";
import {{ Modal }} from "../../../components/ui/Modal";
import {{ {dd.create} }} from "../api";
import type {{ __E__, __E__Input }} from "../types";

const EMPTY: __E__Input = {{ {empty} }};

const submit = (input: __E__Input): Promise<__E__> => {dd.create}(input);

export function __E__Form({{ open, onClose }}: {{ open: boolean; onClose: () => void }}) {{
  const [input, setInput] = useState<__E__Input>(EMPTY);
  const [saving, setSaving] = useState(false);
  return (
    <Modal open={{open}} title="New {d.var.replace('_', ' ')}" onClose={{onClose}}>
      <form
        onSubmit={{(event) => {{
          event.preventDefault();
          setSaving(true);
          submit(input)
            .then(onClose)
            .finally(() => setSaving(false));
        }}}}
      >
        <input
          name="{first}"
          value={{String(input.{first})}}
          onChange={{(event) => setInput({{ ...input, {first}: {conv} }})}}
        />
        <Button type="submit" disabled={{saving}}>
          Save
        </Button>
      </form>
    </Modal>
  );
}}
""", d), tags=("P-5",))
    c.sym("function:submit", fq)
    k.exp(c, fq)
    k.imp(c, f"{UI}/Button.tsx", [(f"{UI}/Button.tsx", "function:Button")], "relative")
    k.imp(c, f"{UI}/Modal.tsx", [(f"{UI}/Modal.tsx", "function:Modal")], "relative")
    k.imp(c, dd.api, [(dd.api, f"function:{dd.create}")], "relative")
    k.imp(c, dd.types, [(dd.types, f"interface:{e}"), (dd.types, f"interface:{e}Input")], "relative")
    k.call(c, "function:submit", dd.api, f"function:{dd.create}", "direct", "expression-bodied arrow")
    k.call(c, fq, c.path, "function:submit")
    k.call(c, fq, f"{UI}/Modal.tsx", "function:Modal", "jsx")
    k.call(c, fq, f"{UI}/Button.tsx", "function:Button", "jsx")

    extra = ""
    if d.key == "catalog":
        extra = 'export { ProductDetails } from "./components/ProductDetails";\n'
    c = repo.add(dd.index, _sub(f"""export {{ __E__List }} from "./components/__E__List";
export {{ __E__Card }} from "./components/__E__Card";
export {{ __E__Form }} from "./components/__E__Form";
{extra}export {{ {dd.hook_name} }} from "./hooks/{dd.hook_name}";
export {{ __E__Store }} from "./store";
export * from "./api";
export type {{ __E__, __E__Input, __E__Status }} from "./types";
""", d))
    for target in (dd.list, dd.card, dd.form, dd.hook, dd.store, dd.api, dd.types):
        k.reexp(c, target)
    if d.key == "catalog":
        k.reexp(c, f"{dd.base}/components/ProductDetails.tsx")

    c = repo.add(dd.test, _sub(f"""import {{ render, screen }} from "@testing-library/react";
import {{ describe, expect, it, vi }} from "vitest";
import * as api from "../api";
import {{ __E__List }} from "../components/__E__List";

describe("__E__List", () => {{
  it("renders one card per item", async () => {{
    vi.spyOn(api, "{dd.fetch_all}").mockResolvedValue([
      {{ id: "1", status: "active", createdAt: "2024-01-01T00:00:00Z", {_sample(dd.fields)} }},
    ]);
    render(<__E__List />);
    expect(await screen.findByRole("heading")).toBeTruthy();
  }});

  it("shows a spinner while loading", () => {{
    vi.spyOn(api, "{dd.fetch_all}").mockReturnValue(new Promise(() => undefined));
    render(<__E__List />);
    expect(screen.getByRole("status")).toBeTruthy();
  }});
}});
""", d), role="test")
    k.edge(c, "IMPORTS_FROM", c.path, dd.api, "namespace")
    k.imp(c, dd.list, [(dd.list, lq)], "relative")
    k.call(c, None, dd.list, lq, "jsx")


def _domain_extras(repo: Repo, k: _Key, by_key: dict[str, _D], fmt: str) -> None:
    cat = by_key["catalog"]
    spec_path = f"{cat.base}/spec/productSpec.ts"
    c = repo.add(spec_path, """export interface ProductSpec {
  dimensions: string;
  weightGrams: number;
}

export function parseSpec(raw: string): ProductSpec {
  const [dimensions = "", weight = "0"] = raw.split(";").map((part) => part.trim());
  return { dimensions, weightGrams: Number.parseInt(weight, 10) || 0 };
}

export function describeSpec(spec: ProductSpec): string {
  return `${spec.dimensions} · ${formatWeight(spec.weightGrams)}`;
}

function formatWeight(grams: number): string {
  return grams >= 1000 ? `${(grams / 1000).toFixed(1)} kg` : `${grams} g`;
}
""", tags=("P-6",))
    c.sym("interface:ProductSpec", "field:ProductSpec.dimensions", "field:ProductSpec.weightGrams",
          "function:parseSpec", "function:describeSpec", "function:formatWeight")
    k.exp(c, "interface:ProductSpec", "function:parseSpec", "function:describeSpec")
    k.call(c, "function:describeSpec", spec_path, "function:formatWeight")

    det = f"{cat.base}/components/ProductDetails.tsx"
    c = repo.add(det, f"""import {{ formatLabel }} from "{_spec(det, fmt)}";
import {{ describeSpec, parseSpec }} from "../spec/productSpec";
import type {{ Product }} from "../types";

export function ProductDetails({{ product, rawSpec }}: {{ product: Product; rawSpec: string }}) {{
  const spec = parseSpec(rawSpec);
  return (
    <section>
      <h2>{{formatLabel(product.title)}}</h2>
      <p>{{describeSpec(spec)}}</p>
    </section>
  );
}}
""")
    c.sym("function:ProductDetails")
    k.exp(c, "function:ProductDetails")
    k.imp(c, fmt, [(fmt, "function:formatLabel")], "relative")
    k.imp(c, spec_path, [(spec_path, "function:describeSpec"), (spec_path, "function:parseSpec")], "relative")
    k.imp(c, cat.types, [(cat.types, "interface:Product")], "relative")
    k.call(c, "function:ProductDetails", spec_path, "function:parseSpec")
    k.call(c, "function:ProductDetails", spec_path, "function:describeSpec")
    k.call(c, "function:ProductDetails", fmt, "function:formatLabel", "cross-dir")

    srch = by_key["search"]
    sug = f"{srch.base}/hooks/useSearchSuggestions.ts"
    c = repo.add(sug, f"""import {{ debounce }} from "lodash";
import {{ useMemo }} from "react";
import {{ {srch.fetch_all} }} from "../api";
import type {{ SearchQuery }} from "../types";

export function useSearchSuggestions(onResults: (items: SearchQuery[]) => void) {{
  return useMemo(
    () =>
      debounce((text: string) => {{
        if (text.trim().length > 1) {{
          {srch.fetch_all}().then(onResults);
        }}
      }}, 250),
    [onResults],
  );
}}
""", tags=("R-1",))
    c.sym("function:useSearchSuggestions")
    k.exp(c, "function:useSearchSuggestions")
    k.imp(c, srch.api, [(srch.api, f"function:{srch.fetch_all}")], "relative")
    k.imp(c, srch.types, [(srch.types, "interface:SearchQuery")], "relative")
    k.call(c, "function:useSearchSuggestions", srch.api, f"function:{srch.fetch_all}")


# -- payments, cart, app ------------------------------------------------------------

def _payments(repo: Repo, k: _Key, fmt: str) -> None:
    pdir = f"{SRC}/payments"
    pp = f"{pdir}/providers/PaymentProvider.ts"
    api_client = f"{LIB}/apiClient.ts"
    c = repo.add(pp, """export interface ChargeResult {
  id: string;
  status: "ok" | "declined";
}

export interface PaymentProvider {
  readonly name: string;
  charge(amountCents: number, token: string): Promise<ChargeResult>;
  refund(chargeId: string): Promise<void>;
}

export interface RefundingProvider extends PaymentProvider {
  partialRefund(chargeId: string, amountCents: number): Promise<void>;
}
""")
    c.sym("interface:ChargeResult", "field:ChargeResult.id", "field:ChargeResult.status",
          "interface:PaymentProvider", "field:PaymentProvider.name", "method:PaymentProvider.charge",
          "method:PaymentProvider.refund", "interface:RefundingProvider",
          "method:RefundingProvider.partialRefund")
    k.exp(c, "interface:ChargeResult", "interface:PaymentProvider", "interface:RefundingProvider")
    k.inh(c, "interface:RefundingProvider", pp, "interface:PaymentProvider", "EXTENDS")

    card = f"{pdir}/providers/CardProvider.ts"
    c = repo.add(card, """import type { ApiClient } from "../../lib/apiClient";
import type { ChargeResult, PaymentProvider } from "./PaymentProvider";

export class CardProvider implements PaymentProvider {
  readonly name = "card";

  constructor(private readonly client: ApiClient) {}

  async charge(amountCents: number, token: string): Promise<ChargeResult> {
    return this.client.post<ChargeResult>("/charges", { amountCents, token, method: "card" });
  }

  async refund(chargeId: string): Promise<void> {
    await this.client.post<void>("/refunds", { chargeId });
  }
}
""")
    c.sym("class:CardProvider", "field:CardProvider.name", "method:CardProvider.constructor",
          "method:CardProvider.charge", "method:CardProvider.refund")
    k.exp(c, "class:CardProvider")
    k.imp(c, api_client, [(api_client, "class:ApiClient")], "relative")
    k.imp(c, pp, [(pp, "interface:ChargeResult"), (pp, "interface:PaymentProvider")], "relative")
    k.inh(c, "class:CardProvider", pp, "interface:PaymentProvider", "IMPLEMENTS")
    k.ovr(c, "method:CardProvider.charge", pp, "method:PaymentProvider.charge")
    k.ovr(c, "method:CardProvider.refund", pp, "method:PaymentProvider.refund")
    k.call(c, "method:CardProvider.charge", api_client, "method:ApiClient.post", "attr-member")
    k.call(c, "method:CardProvider.refund", api_client, "method:ApiClient.post", "attr-member")

    wallet = f"{pdir}/providers/WalletProvider.ts"
    c = repo.add(wallet, """import type { ApiClient } from "../../lib/apiClient";
import type { ChargeResult, RefundingProvider } from "./PaymentProvider";

export class WalletProvider implements RefundingProvider {
  readonly name = "wallet";

  constructor(private readonly client: ApiClient) {}

  async charge(amountCents: number, token: string): Promise<ChargeResult> {
    const result = await this.client.post<ChargeResult>("/wallet/debit", { amountCents, token });
    return { ...result, status: result.status === "ok" ? "ok" : "declined" };
  }

  async refund(chargeId: string): Promise<void> {
    await this.client.post<void>("/wallet/credit", { chargeId });
  }

  async partialRefund(chargeId: string, amountCents: number): Promise<void> {
    await this.client.post<void>("/wallet/credit", { chargeId, amountCents });
  }
}
""")
    c.sym("class:WalletProvider", "field:WalletProvider.name", "method:WalletProvider.constructor",
          "method:WalletProvider.charge", "method:WalletProvider.refund", "method:WalletProvider.partialRefund")
    k.exp(c, "class:WalletProvider")
    k.imp(c, api_client, [(api_client, "class:ApiClient")], "relative")
    k.imp(c, pp, [(pp, "interface:ChargeResult"), (pp, "interface:RefundingProvider")], "relative")
    k.inh(c, "class:WalletProvider", pp, "interface:RefundingProvider", "IMPLEMENTS")
    k.ovr(c, "method:WalletProvider.charge", pp, "method:PaymentProvider.charge")
    k.ovr(c, "method:WalletProvider.refund", pp, "method:PaymentProvider.refund")
    k.ovr(c, "method:WalletProvider.partialRefund", pp, "method:RefundingProvider.partialRefund")
    for m in ("charge", "refund", "partialRefund"):
        k.call(c, f"method:WalletProvider.{m}", api_client, "method:ApiClient.post", "attr-member")

    reg = f"{pdir}/providers/registry.ts"
    c = repo.add(reg, """import { ApiClient } from "../../lib/apiClient";
import { CardProvider } from "./CardProvider";
import type { PaymentProvider } from "./PaymentProvider";
import { WalletProvider } from "./WalletProvider";

export type ProviderKind = "card" | "wallet";

export function providerFor(kind: ProviderKind, client: ApiClient = new ApiClient("/payments")): PaymentProvider {
  return kind === "card" ? new CardProvider(client) : new WalletProvider(client);
}
""")
    c.sym("type_alias:ProviderKind", "function:providerFor")
    k.exp(c, "type_alias:ProviderKind", "function:providerFor")
    k.imp(c, api_client, [(api_client, "class:ApiClient")], "relative")
    k.imp(c, card, [(card, "class:CardProvider")], "relative")
    k.imp(c, pp, [(pp, "interface:PaymentProvider")], "relative")
    k.imp(c, wallet, [(wallet, "class:WalletProvider")], "relative")
    k.call(c, "function:providerFor", api_client, "class:ApiClient", "ctor")
    k.call(c, "function:providerFor", card, "class:CardProvider", "ctor")
    k.call(c, "function:providerFor", wallet, "class:WalletProvider", "ctor")

    events = f"{LIB}/events.ts"
    co = f"{pdir}/checkout.ts"
    c = repo.add(co, """import { createBus } from "../lib/events.js";
import type { ChargeResult, PaymentProvider } from "./providers/PaymentProvider";

const bus = createBus();

export async function checkout(
  provider: PaymentProvider,
  amountCents: number,
  token: string,
): Promise<ChargeResult | null> {
  const result = await provider.charge(amountCents, token);
  if (result.status === "declined") {
    await provider.refund(result.id);
    return null;
  }
  bus.emit("payment.charged", result);
  return result;
}
""", tags=("esm-js-specifier",))
    c.sym("function:checkout")
    k.exp(c, "function:checkout")
    k.imp(c, events, [(events, "function:createBus")], "esm-js-specifier")
    k.imp(c, pp, [(pp, "interface:ChargeResult"), (pp, "interface:PaymentProvider")], "relative")
    k.call(c, None, events, "function:createBus", "esm-js-specifier")
    k.call(c, "function:checkout", pp, "method:PaymentProvider.charge", "interface-dispatch")
    k.call(c, "function:checkout", pp, "method:PaymentProvider.refund", "interface-dispatch")
    k.call(c, "function:checkout", events, "method:EventBus.emit", "typed-member",
           "receiver typed by createBus()'s return annotation")

    btn = f"{UI}/Button.tsx"
    cb = f"{pdir}/CheckoutButton.tsx"
    c = repo.add(cb, f"""import {{ useState }} from "react";
import {{ Button }} from "../components/ui/Button";
import {{ formatMoney as fm }} from "{_spec(cb, fmt)}";
import {{ checkout }} from "./checkout";
import {{ providerFor, type ProviderKind }} from "./providers/registry";

type Status = "idle" | "paying" | "done" | "declined";

export function CheckoutButton({{
  amountCents,
  token,
  kind = "card",
}}: {{
  amountCents: number;
  token: string;
  kind?: ProviderKind;
}}) {{
  const [status, setStatus] = useState<Status>("idle");
  return (
    <Button
      disabled={{status === "paying"}}
      onClick={{() => {{
        setStatus("paying");
        checkout(providerFor(kind), amountCents, token).then((result) =>
          setStatus(result ? "done" : "declined"),
        );
      }}}}
    >
      Pay {{fm(amountCents / 100)}}
    </Button>
  );
}}
""", tags=("P-5",))
    c.sym("type_alias:Status", "function:CheckoutButton")
    k.exp(c, "function:CheckoutButton")
    k.imp(c, btn, [(btn, "function:Button")], "relative")
    k.imp(c, fmt, [(fmt, "function:formatMoney")], "alias")
    k.imp(c, co, [(co, "function:checkout")], "relative")
    k.imp(c, reg, [(reg, "function:providerFor"), (reg, "type_alias:ProviderKind")], "relative")
    k.call(c, "function:CheckoutButton", btn, "function:Button", "jsx")
    k.call(c, "function:CheckoutButton", fmt, "function:formatMoney", "alias")
    k.call(c, "function:CheckoutButton", co, "function:checkout")
    k.call(c, "function:CheckoutButton", reg, "function:providerFor")


def _cart(repo: Repo, k: _Key, fmt: str) -> None:
    cart = f"{SRC}/cart/Cart.ts"
    c = repo.add(cart, """export interface CartLine {
  sku: string;
  quantity: number;
  unitPrice: number;
}

export class Cart {
  private lines: CartLine[] = [];

  add(line: CartLine): void {
    const existing = this.lines.find((l) => l.sku === line.sku);
    if (existing) {
      existing.quantity += line.quantity;
    } else {
      this.lines.push({ ...line });
    }
  }

  remove(sku: string): void {
    this.lines = this.lines.filter((l) => l.sku !== sku);
  }

  total(): number {
    return this.lines.reduce((sum, l) => sum + l.quantity * l.unitPrice, 0);
  }

  totalWithTax(rate: number): number {
    return this.total() * (1 + rate);
  }
}
""", tags=("R-2",))
    c.sym("interface:CartLine", "field:CartLine.sku", "field:CartLine.quantity", "field:CartLine.unitPrice",
          "class:Cart", "field:Cart.lines", "method:Cart.add", "method:Cart.remove", "method:Cart.total",
          "method:Cart.totalWithTax")
    k.exp(c, "interface:CartLine", "class:Cart")
    k.call(c, "method:Cart.totalWithTax", cart, "method:Cart.total", "self-member")

    cs = f"{SRC}/cart/CartSummary.tsx"
    c = repo.add(cs, f"""import {{ formatMoney }} from "{_spec(cs, fmt)}";
import type {{ Cart }} from "./Cart";

export function CartSummary({{ cart, taxRate = 0.2 }}: {{ cart: Cart; taxRate?: number }}) {{
  return (
    <dl className="cart-summary">
      <dt>Subtotal</dt>
      <dd>{{formatMoney(cart.total())}}</dd>
      <dt>Total</dt>
      <dd>{{formatMoney(cart.totalWithTax(taxRate))}}</dd>
    </dl>
  );
}}
""", tags=("R-2",))
    c.sym("function:CartSummary")
    k.exp(c, "function:CartSummary")
    k.imp(c, fmt, [(fmt, "function:formatMoney")], "relative")
    k.imp(c, cart, [(cart, "class:Cart")], "relative")
    k.call(c, "function:CartSummary", fmt, "function:formatMoney", "cross-dir")
    k.call(c, "function:CartSummary", cart, "method:Cart.total", "typed-member")
    k.call(c, "function:CartSummary", cart, "method:Cart.totalWithTax", "typed-member")


def _app(repo: Repo, k: _Key, ds: list[_D]) -> None:
    feats = f"{SRC}/features/index.ts"
    body = "".join(f'export {{ {x.d.entity}List, {x.d.entity}Form }} from "./{x.d.key}";\n' for x in ds)
    c = repo.add(feats, body)
    for x in ds:
        k.reexp(c, x.index)

    routes = f"{SRC}/app/routes.tsx"
    legacy = f"{LIB}/legacyConfig.cts"
    lists = ", ".join(f"{x.d.entity}List" for x in ds)
    rows = "\n".join(f'    {{ path: "/{x.d.key}", title: "{x.d.title}", component: {x.d.entity}List }},'
                     for x in ds)
    c = repo.add(routes, f"""import type {{ ComponentType }} from "react";
import {{ {lists} }} from "../features";
import {{ legacyFlags }} from "../lib/legacyConfig.cjs";

export interface RouteDef {{
  path: string;
  title: string;
  component: ComponentType;
}}

export function buildRoutes(): RouteDef[] {{
  const flags = legacyFlags();
  const routes: RouteDef[] = [
{rows}
  ];
  return flags.compactTables ? routes.slice(0, 8) : routes;
}}
""", tags=("esm-js-specifier",))
    c.sym("interface:RouteDef", "field:RouteDef.path", "field:RouteDef.title", "field:RouteDef.component",
          "function:buildRoutes")
    k.exp(c, "interface:RouteDef", "function:buildRoutes")
    k.imp(c, feats, [(x.list, f"function:{x.d.entity}List") for x in ds], "barrel")
    k.imp(c, legacy, [(legacy, "function:legacyFlags")], "esm-js-specifier")
    k.call(c, "function:buildRoutes", legacy, "function:legacyFlags", "esm-js-specifier")

    orders = next(x for x in ds if x.d.key == "orders")
    app = f"{SRC}/app/App.tsx"
    c = repo.add(app, """import { useState } from "react";
import { CartSummary } from "../cart/CartSummary";
import { Cart } from "../cart/Cart";
import { Layout } from "../components/ui/Layout";
import { OrderForm } from "../features/orders";
import { CheckoutButton } from "../payments/CheckoutButton";
import { buildRoutes } from "./routes";

export default function App() {
  const routes = buildRoutes();
  const [active, setActive] = useState(routes[0].path);
  const [creating, setCreating] = useState(false);
  const [cart] = useState(() => new Cart());
  const current = routes.find((route) => route.path === active) ?? routes[0];
  const Page = current.component;
  return (
    <Layout
      title={current.title}
      nav={routes.map((route) => ({ label: route.title, href: route.path }))}
      onNavigate={setActive}
    >
      <Page />
      <CartSummary cart={cart} />
      <CheckoutButton amountCents={1999} token="tok_demo" />
      <button type="button" onClick={() => setCreating(true)}>
        New order
      </button>
      <OrderForm open={creating} onClose={() => setCreating(false)} />
    </Layout>
  );
}
""", tags=("P-5",))
    c.sym("function:App")
    k.exp(c, "function:App")
    cs, cart, lay = f"{SRC}/cart/CartSummary.tsx", f"{SRC}/cart/Cart.ts", f"{UI}/Layout.tsx"
    cb = f"{SRC}/payments/CheckoutButton.tsx"
    k.imp(c, cs, [(cs, "function:CartSummary")], "relative")
    k.imp(c, cart, [(cart, "class:Cart")], "relative")
    k.imp(c, lay, [(lay, "function:Layout")], "relative")
    k.imp(c, orders.index, [(orders.form, "function:OrderForm")], "barrel")
    k.imp(c, cb, [(cb, "function:CheckoutButton")], "relative")
    k.imp(c, routes, [(routes, "function:buildRoutes")], "relative")
    k.call(c, "function:App", routes, "function:buildRoutes")
    k.call(c, "function:App", cart, "class:Cart", "ctor")
    for path, q in ((lay, "function:Layout"), (cs, "function:CartSummary"), (cb, "function:CheckoutButton"),
                    (orders.form, "function:OrderForm")):
        k.call(c, "function:App", path, q, "jsx")

    main = f"{SRC}/main.tsx"
    c = repo.add(main, """import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App";

const container = document.getElementById("root");
if (!container) {
  throw new Error("missing #root element");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
""")
    k.imp(c, app, [(app, "function:App")], "relative")
    k.call(c, None, app, "function:App", "jsx")


# -- non-source frontend files --------------------------------------------------------

def _frontend_misc(repo: Repo, k: _Key) -> None:
    repo.add(f"{FE}/package.json", """{
  "name": "@acme/frontend",
  "private": true,
  "version": "0.14.2",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "e2e": "playwright test"
  },
  "dependencies": {
    "lodash": "^4.17.21",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@playwright/test": "^1.47.0",
    "@testing-library/react": "^16.0.1",
    "@types/lodash": "^4.17.7",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.2",
    "vitest": "^2.0.5"
  }
}
""", role="config")
    repo.add(f"{FE}/tsconfig.json", """{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "allowImportingTsExtensions": false,
    "types": ["vite/client"]
  },
  "include": ["src", "e2e"]
}
""", role="config")
    repo.add(f"{FE}/vite.config.ts", """import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
  test: { environment: "jsdom" },
});
""", role="config")

    c = repo.add(f"{FE}/e2e/checkout.e2e-spec.ts", """import { expect, test, type Page } from "@playwright/test";

async function fillCard(page: Page, number: string): Promise<void> {
  await page.getByLabel("Card number").fill(number);
  await page.getByLabel("Expiry").fill("12/30");
  await page.getByLabel("CVC").fill("123");
}

test("a shopper can pay for their cart", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /pay/i }).click();
  await fillCard(page, "4242424242424242");
  await expect(page.getByText("Payment complete")).toBeVisible();
});

test("a declined card shows an error", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /pay/i }).click();
  await fillCard(page, "4000000000000002");
  await expect(page.getByText("declined", { exact: false })).toBeVisible();
});
""", role="test", tags=("P-6",))
    c.sym("function:fillCard")
    k.call(c, None, c.path, "function:fillCard")

    repo.add(f"{SRC}/generated/graphql.ts", """/* eslint-disable */
// This file was automatically generated by graphql-codegen. DO NOT EDIT.
export type Maybe<T> = T | null;
export type Scalars = { ID: string; String: string; Int: number; Float: number; DateTime: string };

export type OrderFieldsFragment = {
  __typename?: "Order";
  id: Scalars["ID"];
  status: Scalars["String"];
  total: Scalars["Float"];
};

export type ListOrdersQueryVariables = { first?: Maybe<Scalars["Int"]>; after?: Maybe<Scalars["String"]> };

export type ListOrdersQuery = {
  orders: { edges: Array<{ node: OrderFieldsFragment }>; pageInfo: { hasNextPage: boolean } };
};

export const ListOrdersDocument = /* GraphQL */ `
  query ListOrders($first: Int, $after: String) {
    orders(first: $first, after: $after) { edges { node { ...OrderFields } } pageInfo { hasNextPage } }
  }
`;

export function getSdk(request: <R, V>(doc: string, vars?: V) => Promise<R>) {
  return {
    ListOrders(variables?: ListOrdersQueryVariables): Promise<ListOrdersQuery> {
      return request<ListOrdersQuery, ListOrdersQueryVariables>(ListOrdersDocument, variables);
    },
  };
}
""", role="generated", expect_index=False, tags=("P-1",))

    c = repo.add(f"{SRC}/types/env.d.ts", """interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_SENTRY_DSN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
""", role="type_definition")
    c.sym("interface:ImportMetaEnv", "field:ImportMetaEnv.VITE_API_URL", "field:ImportMetaEnv.VITE_SENTRY_DSN",
          "interface:ImportMeta", "field:ImportMeta.env")
    repo.add(f"{SRC}/types/assets.d.ts", """declare module "*.svg" {
  const src: string;
  export default src;
}

declare module "*.module.css" {
  const classes: Readonly<Record<string, string>>;
  export default classes;
}
""", role="type_definition")

    nm = f"{FE}/node_modules/lodash"
    repo.add(f"{nm}/package.json", '{\n  "name": "lodash",\n  "version": "4.17.21",\n  "main": "index.js"\n}\n',
             role="vendored", expect_index=False, tags=("P-1",))
    repo.add(f"{nm}/debounce.js", """var isObject = function (value) {
  var type = typeof value;
  return value != null && (type == 'object' || type == 'function');
};

function debounce(func, wait, options) {
  var lastArgs, lastThis, result, timerId, lastCallTime;
  var leading = false, trailing = true;
  if (typeof func != 'function') {
    throw new TypeError('Expected a function');
  }
  wait = Number(wait) || 0;
  if (isObject(options)) {
    leading = !!options.leading;
    trailing = 'trailing' in options ? !!options.trailing : trailing;
  }
  function invoke() {
    var args = lastArgs, thisArg = lastThis;
    lastArgs = lastThis = undefined;
    result = func.apply(thisArg, args);
    return result;
  }
  function debounced() {
    lastArgs = arguments;
    lastThis = this;
    lastCallTime = Date.now();
    if (timerId === undefined && leading) {
      return invoke();
    }
    clearTimeout(timerId);
    timerId = setTimeout(function () { timerId = undefined; if (trailing) invoke(); }, wait);
    return result;
  }
  debounced.cancel = function () { clearTimeout(timerId); timerId = lastArgs = lastThis = lastCallTime = undefined; };
  return debounced;
}

module.exports = debounce;
""", role="vendored", expect_index=False, tags=("P-1",))
    repo.add(f"{nm}/throttle.js", """var debounce = require('./debounce');

function throttle(func, wait, options) {
  var leading = true, trailing = true;
  if (options && typeof options == 'object') {
    leading = 'leading' in options ? !!options.leading : leading;
    trailing = 'trailing' in options ? !!options.trailing : trailing;
  }
  return debounce(func, wait, { leading: leading, maxWait: wait, trailing: trailing });
}

module.exports = throttle;
""", role="vendored", expect_index=False, tags=("P-1",))
    repo.add(f"{nm}/index.js", """module.exports = {
  debounce: require('./debounce'),
  throttle: require('./throttle'),
};
""", role="vendored", expect_index=False, tags=("P-1",))

    parts = ['!function(w,d){"use strict";var r={};']
    size, i = len(parts[0]), 0
    while size < 600_000:
        j = i * 7919 % 1000003
        chunk = (f"function a{i:x}(t,e){{return t*{i % 97 + 1}+(e?e[{i % 13}]:{j % 251})}}"
                 f"r.k{i:x}=function(t){{return a{i:x}(t,r.v)}};")
        parts.append(chunk)
        size += len(chunk)
        i += 1
    parts.append('w.AcmeEmbed=r}(window,document);\n')
    repo.add(f"{FE}/public/widgets/embed.js", "".join(parts), role="minified", expect_index=False,
             tags=("P-4",))


# -- bff ------------------------------------------------------------------------------

_BFF_MODULES = (
    # (folder, Service class, Controller class, summary interface, upstream path)
    ("orders", "OrdersService", "OrdersController", "OrderSummary", "/orders"),
    ("products", "ProductsService", "ProductsController", "ProductSummary", "/catalog/products"),
    ("users", "UsersService", "UsersController", "UserSummary", "/users"),
)


def _bff(repo: Repo, k: _Key, clamp_src: bytes) -> None:
    repo.add(f"{BFF}/package.json", """{
  "name": "@acme/bff",
  "private": true,
  "version": "0.6.0",
  "scripts": { "start": "tsx src/main.ts", "test": "vitest run" },
  "devDependencies": { "tsx": "^4.19.0", "typescript": "^5.5.4", "vitest": "^2.0.5" }
}
""", role="config")
    repo.add(f"{BFF}/tsconfig.json", """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "strict": true,
    "experimentalDecorators": true,
    "outDir": "dist"
  },
  "include": ["src", "test"]
}
""", role="config")

    dec = f"{BFF}/src/decorators.ts"
    c = repo.add(dec, """export interface RouteMeta {
  method: "GET" | "POST";
  path: string;
  handler: string;
}

const registry = {
  controllers: new Map<unknown, string>(),
  routes: new Map<unknown, RouteMeta[]>(),
  injectables: new Set<unknown>(),
};

export function Controller(prefix: string): ClassDecorator {
  return (target) => {
    registry.controllers.set(target, prefix);
  };
}

function route(method: RouteMeta["method"], path: string): MethodDecorator {
  return (target, key) => {
    const list = registry.routes.get(target.constructor) ?? [];
    list.push({ method, path, handler: String(key) });
    registry.routes.set(target.constructor, list);
  };
}

export function Get(path = ""): MethodDecorator {
  return route("GET", path);
}

export function Post(path = ""): MethodDecorator {
  return route("POST", path);
}

export function Injectable(): ClassDecorator {
  return (target) => {
    registry.injectables.add(target);
  };
}

export function routesFor(controller: unknown): RouteMeta[] {
  return registry.routes.get(controller) ?? [];
}

export function prefixFor(controller: unknown): string {
  return registry.controllers.get(controller) ?? "";
}
""", tags=("P-5",))
    c.sym("interface:RouteMeta", "field:RouteMeta.method", "field:RouteMeta.path", "field:RouteMeta.handler",
          "function:Controller", "function:route", "function:Get", "function:Post", "function:Injectable",
          "function:routesFor", "function:prefixFor")
    k.exp(c, "interface:RouteMeta", "function:Controller", "function:Get", "function:Post",
          "function:Injectable", "function:routesFor", "function:prefixFor")
    k.call(c, "function:Get", dec, "function:route")
    k.call(c, "function:Post", dec, "function:route")

    up = f"{BFF}/src/http/upstream.ts"
    c = repo.add(up, """export class UpstreamError extends Error {
  constructor(readonly status: number, readonly path: string) {
    super(`upstream ${path} answered ${status}`);
  }
}

export class UpstreamClient {
  constructor(private readonly baseUrl: string) {}

  async get<T>(path: string): Promise<T> {
    return this.send<T>("GET", path);
  }

  async post<T>(path: string, body: unknown): Promise<T> {
    return this.send<T>("POST", path, body);
  }

  private async send<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(this.baseUrl + path, {
      method,
      headers: { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      throw new UpstreamError(response.status, path);
    }
    return (await response.json()) as T;
  }
}
""")
    c.sym("class:UpstreamError", "method:UpstreamError.constructor", "class:UpstreamClient",
          "method:UpstreamClient.constructor", "method:UpstreamClient.get", "method:UpstreamClient.post",
          "method:UpstreamClient.send")
    k.exp(c, "class:UpstreamError", "class:UpstreamClient")
    k.call(c, "method:UpstreamClient.get", up, "method:UpstreamClient.send", "self-member")
    k.call(c, "method:UpstreamClient.post", up, "method:UpstreamClient.send", "self-member")
    k.call(c, "method:UpstreamClient.send", up, "class:UpstreamError", "ctor")

    clamp = f"{BFF}/src/util/clamp.ts"
    c = repo.add(clamp, clamp_src, tags=("A-7",))
    c.sym("function:clamp", "function:clampInt")
    k.exp(c, "function:clamp", "function:clampInt")
    k.call(c, "function:clampInt", clamp, "function:clamp")

    ctrls = []
    for folder, svc, ctl, summ, upath in _BFF_MODULES:
        sp = f"{BFF}/src/{folder}/{folder}.service.ts"
        c = repo.add(sp, f"""import {{ Injectable }} from "../decorators";
import {{ UpstreamClient }} from "../http/upstream";

export interface {summ} {{
  id: string;
  name: string;
  status: string;
}}

@Injectable()
export class {svc} {{
  constructor(private readonly upstream: UpstreamClient) {{}}

  list(): Promise<{summ}[]> {{
    return this.upstream.get<{summ}[]>("{upath}");
  }}

  get(id: string): Promise<{summ}> {{
    return this.upstream.get<{summ}>(`{upath}/${{id}}`);
  }}

  create(body: Partial<{summ}>): Promise<{summ}> {{
    return this.upstream.post<{summ}>("{upath}", body);
  }}
}}
""", tags=("P-5",))
        c.sym(f"interface:{summ}", f"field:{summ}.id", f"field:{summ}.name", f"field:{summ}.status",
              f"class:{svc}", f"method:{svc}.constructor", f"method:{svc}.list", f"method:{svc}.get",
              f"method:{svc}.create")
        k.exp(c, f"interface:{summ}", f"class:{svc}")
        k.imp(c, dec, [(dec, "function:Injectable")], "relative")
        k.imp(c, up, [(up, "class:UpstreamClient")], "relative")
        k.call(c, f"class:{svc}", dec, "function:Injectable", "decorator")
        k.call(c, f"method:{svc}.list", up, "method:UpstreamClient.get", "attr-member")
        k.call(c, f"method:{svc}.get", up, "method:UpstreamClient.get", "attr-member")
        k.call(c, f"method:{svc}.create", up, "method:UpstreamClient.post", "attr-member")

        cp = f"{BFF}/src/{folder}/{folder}.controller.ts"
        c = repo.add(cp, f"""import {{ Controller, Get, Post }} from "../decorators";
import {{ clamp }} from "../util/clamp";
import {{ {svc}, type {summ} }} from "./{folder}.service";

@Controller("{folder}")
export class {ctl} {{
  constructor(private readonly {folder}: {svc}) {{}}

  @Get()
  async list(limit = 20): Promise<{summ}[]> {{
    const all = await this.{folder}.list();
    return all.slice(0, clamp(limit, 1, 100));
  }}

  @Get(":id")
  show(id: string): Promise<{summ}> {{
    return this.{folder}.get(id);
  }}

  @Post()
  create(body: Partial<{summ}>): Promise<{summ}> {{
    return this.{folder}.create(body);
  }}
}}
""", tags=("P-5",))
        c.sym(f"class:{ctl}", f"method:{ctl}.constructor", f"method:{ctl}.list", f"method:{ctl}.show",
              f"method:{ctl}.create")
        k.exp(c, f"class:{ctl}")
        k.imp(c, dec, [(dec, "function:Controller"), (dec, "function:Get"), (dec, "function:Post")], "relative")
        k.imp(c, clamp, [(clamp, "function:clamp")], "relative")
        k.imp(c, sp, [(sp, f"class:{svc}"), (sp, f"interface:{summ}")], "relative")
        k.call(c, f"class:{ctl}", dec, "function:Controller", "decorator")
        k.call(c, f"method:{ctl}.list", dec, "function:Get", "decorator")
        k.call(c, f"method:{ctl}.show", dec, "function:Get", "decorator")
        k.call(c, f"method:{ctl}.create", dec, "function:Post", "decorator")
        k.call(c, f"method:{ctl}.list", sp, f"method:{svc}.list", "attr-member")
        k.call(c, f"method:{ctl}.list", clamp, "function:clamp")
        k.call(c, f"method:{ctl}.show", sp, f"method:{svc}.get", "attr-member")
        k.call(c, f"method:{ctl}.create", sp, f"method:{svc}.create", "attr-member")
        ctrls.append((folder, svc, ctl, sp, cp))

    mod = f"{BFF}/src/app.module.ts"
    imports = "\n".join(
        f'import {{ {ctl} }} from "./{f}/{f}.controller";\nimport {{ {svc} }} from "./{f}/{f}.service";'
        for f, svc, ctl, _, _ in ctrls)
    news = ",\n".join(f"    new {ctl}(new {svc}(upstream))" for _, svc, ctl, _, _ in ctrls)
    c = repo.add(mod, f"""import {{ prefixFor, routesFor, type RouteMeta }} from "./decorators";
import {{ UpstreamClient }} from "./http/upstream";
{imports}

export interface MountedController {{
  prefix: string;
  routes: RouteMeta[];
}}

export function bootstrap(upstreamUrl: string): MountedController[] {{
  const upstream = new UpstreamClient(upstreamUrl);
  const controllers = [
{news},
  ];
  return controllers.map((controller) => ({{
    prefix: prefixFor(controller.constructor),
    routes: routesFor(controller.constructor),
  }}));
}}
""")
    c.sym("interface:MountedController", "field:MountedController.prefix", "field:MountedController.routes",
          "function:bootstrap")
    k.exp(c, "interface:MountedController", "function:bootstrap")
    k.imp(c, dec, [(dec, "function:prefixFor"), (dec, "function:routesFor"), (dec, "interface:RouteMeta")],
          "relative")
    k.imp(c, up, [(up, "class:UpstreamClient")], "relative")
    k.call(c, "function:bootstrap", up, "class:UpstreamClient", "ctor")
    for f, svc, ctl, sp, cp in ctrls:
        k.imp(c, cp, [(cp, f"class:{ctl}")], "relative")
        k.imp(c, sp, [(sp, f"class:{svc}")], "relative")
        k.call(c, "function:bootstrap", cp, f"class:{ctl}", "ctor")
        k.call(c, "function:bootstrap", sp, f"class:{svc}", "ctor")
    k.call(c, "function:bootstrap", dec, "function:prefixFor")
    k.call(c, "function:bootstrap", dec, "function:routesFor")

    c = repo.add(f"{BFF}/src/main.ts", """import { bootstrap } from "./app.module";

const mounted = bootstrap(process.env.UPSTREAM_URL ?? "http://localhost:8000");
for (const controller of mounted) {
  for (const route of controller.routes) {
    console.log(`${route.method} /${controller.prefix}/${route.path} -> ${route.handler}`);
  }
}
""")
    k.imp(c, mod, [(mod, "function:bootstrap")], "relative")
    k.call(c, None, mod, "function:bootstrap")

    _, svc, ctl, sp, cp = ctrls[0]
    c = repo.add(f"{BFF}/test/orders.controller.spec.ts", f"""import {{ describe, expect, it }} from "vitest";
import {{ {ctl} }} from "../src/orders/orders.controller";
import type {{ {svc} }} from "../src/orders/orders.service";

const rows = Array.from({{ length: 150 }}, (_, i) => ({{ id: String(i), name: `order ${{i}}`, status: "active" }}));

describe("{ctl}", () => {{
  it("caps the page size at 100", async () => {{
    const service = {{ list: async () => rows }} as unknown as {svc};
    const controller = new {ctl}(service);
    expect(await controller.list(500)).toHaveLength(100);
  }});
}});
""", role="test")
    k.imp(c, cp, [(cp, f"class:{ctl}")], "relative")
    k.imp(c, sp, [(sp, f"class:{svc}")], "relative")
    k.call(c, None, cp, f"class:{ctl}", "ctor")
    k.call(c, None, cp, f"method:{ctl}.list", "typed-member")


# -- worker -------------------------------------------------------------------------------

def _worker(repo: Repo, k: _Key, ds: list[_D]) -> None:
    repo.add(f"{WK}/package.json", """{
  "name": "@acme/worker",
  "private": true,
  "version": "1.3.0",
  "type": "module",
  "main": "src/index.js",
  "scripts": { "start": "node src/index.js", "test": "node --test test/" }
}
""", role="config")

    lib = f"{WK}/src/lib"
    log = f"{lib}/logger.js"
    c = repo.add(log, """export function createLogger(scope) {
  return {
    info: (message, meta) => write("info", scope, message, meta),
    error: (message, meta) => write("error", scope, message, meta),
  };
}

function write(level, scope, message, meta = {}) {
  const line = JSON.stringify({ ts: new Date().toISOString(), level, scope, message, ...meta });
  process.stdout.write(line + "\\n");
}
""")
    c.sym("function:createLogger", "function:write")
    k.exp(c, "function:createLogger")
    k.call(c, "function:createLogger", log, "function:write")

    retry = f"{lib}/retry.js"
    c = repo.add(retry, """export async function withRetry(fn, { attempts = 3, baseMs = 200 } = {}) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt < attempts) {
        await sleep(baseMs * 2 ** (attempt - 1));
      }
    }
  }
  throw lastError;
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
""")
    c.sym("function:withRetry", "function:sleep")
    k.exp(c, "function:withRetry", "function:sleep")
    k.call(c, "function:withRetry", retry, "function:sleep")

    client = f"{lib}/client.js"
    c = repo.add(client, """export class ApiClient {
  constructor(baseUrl = "http://localhost:8000", token = process.env.WORKER_TOKEN) {
    this.baseUrl = baseUrl;
    this.token = token;
  }

  async request(method, path, body) {
    const response = await fetch(this.baseUrl + path, {
      method,
      headers: { authorization: `Bearer ${this.token}`, "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`${method} ${path} failed with ${response.status}`);
    }
    return response.json();
  }

  get(path) {
    return this.request("GET", path);
  }

  post(path, body) {
    return this.request("POST", path, body);
  }
}
""")
    c.sym("class:ApiClient", "method:ApiClient.constructor", "method:ApiClient.request", "method:ApiClient.get",
          "method:ApiClient.post")
    k.exp(c, "class:ApiClient")
    k.call(c, "method:ApiClient.get", client, "method:ApiClient.request", "self-member")
    k.call(c, "method:ApiClient.post", client, "method:ApiClient.request", "self-member")

    queue = f"{lib}/queue.js"
    c = repo.add(queue, """import { withRetry } from "./retry.js";

export class JobQueue {
  constructor(log) {
    this.log = log;
    this.jobs = new Map();
  }

  register(name, handler) {
    this.jobs.set(name, handler);
    return this;
  }

  async runAll() {
    const results = {};
    for (const [name, handler] of this.jobs) {
      try {
        results[name] = await withRetry(handler, { attempts: 3 });
      } catch (err) {
        this.log.error("job failed", { name, err: String(err) });
        results[name] = null;
      }
    }
    return results;
  }
}
""")
    c.sym("class:JobQueue", "method:JobQueue.constructor", "method:JobQueue.register", "method:JobQueue.runAll")
    k.exp(c, "class:JobQueue")
    k.imp(c, retry, [(retry, "function:withRetry")], "relative")
    k.call(c, "method:JobQueue.runAll", retry, "function:withRetry")

    jobs = {}
    for x in ds:
        d = x.d
        jp = f"{WK}/src/jobs/sync{d.title}.js"
        jobs[d.key] = jp
        c = repo.add(jp, f"""import {{ ApiClient }} from "../lib/client.js";
import {{ createLogger }} from "../lib/logger.js";
import {{ withRetry }} from "../lib/retry.js";

const log = createLogger("sync-{d.key}");

export async function run(client = new ApiClient(process.env.API_URL)) {{
  const items = await withRetry(() => client.get("/api/{d.key}?updated_since=" + since()), {{ attempts: 5 }});
  log.info("synced", {{ count: items.length }});
  return items.length;
}}

function since() {{
  return new Date(Date.now() - 15 * 60 * 1000).toISOString();
}}
""")
        c.sym("function:run", "function:since")
        k.exp(c, "function:run")
        k.imp(c, client, [(client, "class:ApiClient")], "relative")
        k.imp(c, log, [(log, "function:createLogger")], "relative")
        k.imp(c, retry, [(retry, "function:withRetry")], "relative")
        k.call(c, None, log, "function:createLogger")
        k.call(c, "function:run", client, "class:ApiClient", "ctor")
        k.call(c, "function:run", retry, "function:withRetry")
        k.call(c, "function:run", client, "method:ApiClient.get", "typed-member",
               "receiver typed by its default parameter")
        k.call(c, "function:run", jp, "function:since")

    statics = [x.d for x in ds if x.d.key != "orders"]
    imports = "\n".join(f'import {{ run as sync{d.title} }} from "./jobs/sync{d.title}.js";' for d in statics)
    registers = "\n".join(f'  queue.register("{d.key}", sync{d.title});' for d in statics)
    idx = f"{WK}/src/index.js"
    c = repo.add(idx, f"""import {{ createLogger }} from "./lib/logger.js";
import {{ JobQueue }} from "./lib/queue.js";
{imports}

const log = createLogger("worker");

export async function main() {{
  await syncUsers();
  const queue = new JobQueue(log);
{registers}
  const orders = await import("./jobs/syncOrders.js");
  await orders.run();
  queue.register("orders", orders.run);
  return queue.runAll();
}}

main().catch((err) => {{
  log.error("worker crashed", {{ err: String(err) }});
  process.exit(1);
}});
""", tags=("P-5",))
    c.sym("function:main")
    k.exp(c, "function:main")
    k.imp(c, log, [(log, "function:createLogger")], "relative")
    k.imp(c, queue, [(queue, "class:JobQueue")], "relative")
    for d in statics:
        k.imp(c, jobs[d.key], [(jobs[d.key], "function:run")], "alias")
    k.edge(c, "IMPORTS_FROM", c.path, jobs["orders"], "dynamic-import")
    k.call(c, None, log, "function:createLogger")
    k.call(c, None, idx, "function:main")
    k.call(c, "function:main", jobs["users"], "function:run", "alias")
    k.call(c, "function:main", queue, "class:JobQueue", "ctor")
    k.call(c, "function:main", queue, "method:JobQueue.register", "typed-member")
    k.call(c, "function:main", queue, "method:JobQueue.runAll", "typed-member")
    k.call(c, "function:main", jobs["orders"], "function:run", "dynamic-import")

    leg = f"{WK}/legacy"
    util = f"{leg}/util.cjs"
    c = repo.add(util, """function chunk(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = { chunk, sleep };
""", tags=("P-5",))
    c.sym("function:chunk", "function:sleep")
    k.exp(c, "function:chunk", "function:sleep", cat="commonjs")

    rei = f"{leg}/reindex.cjs"
    c = repo.add(rei, """const http = require("node:http");
const { chunk, sleep } = require("./util.cjs");

function post(batch) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: process.env.API_HOST || "localhost", port: 8000, path: "/internal/reindex", method: "POST" },
      (res) => (res.statusCode < 300 ? resolve() : reject(new Error(`reindex failed: ${res.statusCode}`))),
    );
    req.on("error", reject);
    req.end(JSON.stringify({ ids: batch }));
  });
}

async function reindexAll(ids) {
  for (const batch of chunk(ids, 50)) {
    await post(batch);
    await sleep(100);
  }
  return ids.length;
}

module.exports = { reindexAll };
""", tags=("P-5",))
    c.sym("function:post", "function:reindexAll")
    k.exp(c, "function:reindexAll", cat="commonjs")
    k.imp(c, util, [(util, "function:chunk"), (util, "function:sleep")], "commonjs")
    k.call(c, "function:reindexAll", util, "function:chunk", "commonjs")
    k.call(c, "function:reindexAll", util, "function:sleep", "commonjs")
    k.call(c, "function:reindexAll", rei, "function:post")

    c = repo.add(f"{leg}/cli.cjs", """const { reindexAll } = require("./reindex.cjs");

if (require.main === module) {
  reindexAll(process.argv.slice(2))
    .then((count) => console.log(`queued ${count} records`))
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
""", tags=("P-5",))
    k.imp(c, rei, [(rei, "function:reindexAll")], "commonjs")
    k.call(c, None, rei, "function:reindexAll", "commonjs")

    c = repo.add(f"{WK}/test/retry.test.js", """import assert from "node:assert/strict";
import { test } from "node:test";
import { sleep, withRetry } from "../src/lib/retry.js";

test("withRetry returns the first success", async () => {
  let calls = 0;
  const value = await withRetry(async () => {
    calls += 1;
    if (calls < 2) throw new Error("flaky");
    return "ok";
  }, { baseMs: 1 });
  assert.equal(value, "ok");
  assert.equal(calls, 2);
});

test("sleep waits at least the given time", async () => {
  const started = Date.now();
  await sleep(20);
  assert.ok(Date.now() - started >= 15);
});
""", role="test")
    k.imp(c, retry, [(retry, "function:sleep"), (retry, "function:withRetry")], "relative")
    k.call(c, None, retry, "function:withRetry")
    k.call(c, None, retry, "function:sleep")

    c = repo.add(f"{WK}/test/queue.test.js", """import assert from "node:assert/strict";
import { test } from "node:test";
import { JobQueue } from "../src/lib/queue.js";

const silent = { info() {}, error() {} };

test("runAll records a failed job as null", async () => {
  const queue = new JobQueue(silent);
  queue.register("ok", async () => 3);
  queue.register("broken", async () => {
    throw new Error("nope");
  });
  assert.deepEqual(await queue.runAll(), { ok: 3, broken: null });
});
""", role="test")
    k.imp(c, queue, [(queue, "class:JobQueue")], "relative")
    k.call(c, None, queue, "class:JobQueue", "ctor")
    k.call(c, None, queue, "method:JobQueue.register", "typed-member")
    k.call(c, None, queue, "method:JobQueue.runAll", "typed-member")


# -- entry point ------------------------------------------------------------------------------

def build(repo: Repo, step: int) -> None:
    k = _Key(repo)
    fmt = f"{LIB}/text/format.ts" if step >= 3 else f"{LIB}/format.ts"
    ds = [_D(d) for d in active_domains(step)]

    _lib(repo, k, fmt)
    _ui(repo, k, fmt)
    for i, dd in enumerate(ds):
        _domain(repo, k, dd, i, fmt)
    _domain_extras(repo, k, {dd.d.key: dd for dd in ds}, fmt)
    _payments(repo, k, fmt)
    _cart(repo, k, fmt)
    _app(repo, k, ds)
    _frontend_misc(repo, k)
    _bff(repo, k, repo.files[f"{LIB}/clamp.ts"].content)
    _worker(repo, k, ds)
