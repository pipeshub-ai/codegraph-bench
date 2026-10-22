"""The acme-shop services written in languages other than Python and JS/TS.

Each service is small but coherent: cross-file calls, imports where the
language has file-level imports, inheritance, interfaces and overrides. The key
records what is true about the code; most of these languages have no
cross-file edges in PipesHub today, so the scorer reports them as misses (P-3).

Files PipesHub should skip (vendored, generated, lockfiles) carry no symbols
and no edges point into them.
"""
from __future__ import annotations

import re

from .model import FileCtx, Repo

SVC = "services"


def _src(text: str) -> str:
    return text.lstrip("\n")


def _tabs(text: str) -> str:
    """gofmt indentation: every leading group of four spaces becomes a tab."""
    return re.sub(r"(?m)^((?:    )+)", lambda m: "\t" * (len(m.group(1)) // 4), _src(text))


def _code(repo: Repo, path: str, text: str, *, role: str = "source",
          tags: tuple[str, ...] = ("P-3",)) -> FileCtx:
    return repo.add(path, text, role=role, tags=tags)


def _skip(repo: Repo, path: str, text: str, role: str, tags: tuple[str, ...] = ()) -> None:
    repo.add(path, text, lang=None, role=role, expect_index=False, tags=tags)


def _meta(repo: Repo, path: str, text: str, role: str = "config") -> None:
    repo.add(path, text, lang=None, role=role)


def build(repo: Repo, step: int) -> None:
    _go(repo)
    _rust(repo)
    _java(repo)
    _kotlin(repo)
    _scala(repo)
    _groovy(repo)
    _c(repo)
    _cpp(repo)
    _csharp(repo)
    _ruby(repo)
    _php(repo)
    _swift(repo)
    _dart(repo)
    _lua(repo)


# --------------------------------------------------------------------- Go

def _go(repo: Repo) -> None:
    root = f"{SVC}/gateway-go"
    main, server = f"{root}/cmd/gateway/main.go", f"{root}/internal/http/server.go"
    mw, store = f"{root}/internal/http/middleware.go", f"{root}/internal/store/store.go"
    build_go = f"{root}/internal/build/build.go"

    _meta(repo, f"{root}/go.mod", _src("""
module github.com/acme/shop/gateway

go 1.22

require github.com/pkg/errors v0.9.1
"""))
    _skip(repo, f"{root}/go.sum", _src("""
github.com/pkg/errors v0.9.1 h1:FEBLx1zS214owpjy7qsBeixbURkuhQAwrK5UwLGTwt4=
github.com/pkg/errors v0.9.1/go.mod h1:bwawxfHBFNV+L2hUp1rHADufV3IMtnDRdf1r5NINEl0=
"""), role="lockfile")

    _skip(repo, f"{root}/vendor/github.com/pkg/errors/errors.go", _tabs("""
package errors

import "fmt"

type fundamental struct {
    msg string
}

func New(message string) error {
    return &fundamental{msg: message}
}

func (f *fundamental) Error() string {
    return f.msg
}

func Wrap(err error, message string) error {
    if err == nil {
        return nil
    }
    return fmt.Errorf("%s: %w", message, err)
}
"""), role="vendored", tags=("P-1",))

    (_code(repo, store, _tabs("""
package store

import (
    "sync"

    "github.com/pkg/errors"
)

type Store interface {
    Get(id string) (string, error)
    Put(id string, value string) error
}

type MemoryStore struct {
    mu    sync.RWMutex
    items map[string]string
}

func NewMemoryStore() *MemoryStore {
    return &MemoryStore{items: make(map[string]string)}
}

func (s *MemoryStore) Get(id string) (string, error) {
    s.mu.RLock()
    defer s.mu.RUnlock()
    v, ok := s.items[id]
    if !ok {
        return "", errors.New("not found: " + id)
    }
    return v, nil
}

func (s *MemoryStore) Put(id string, value string) error {
    if id == "" {
        return errors.New("empty id")
    }
    s.mu.Lock()
    defer s.mu.Unlock()
    s.items[id] = value
    return nil
}
"""))
        .sym("interface:Store", "method:Store.Get", "method:Store.Put", "struct:MemoryStore",
             "function:NewMemoryStore", "method:MemoryStore.Get", "method:MemoryStore.Put")
        .inherits("struct:MemoryStore", store, "interface:Store", rel="IMPLEMENTS")
        .overrides("method:MemoryStore.Get", store, "method:Store.Get")
        .overrides("method:MemoryStore.Put", store, "method:Store.Put"))

    (_code(repo, mw, _tabs("""
package httpapi

import (
    "errors"
    "log"
    "net/http"
    "time"
)

var errUnsupported = errors.New("content type must be application/json")

type Middleware func(http.Handler) http.Handler

func Chain(h http.Handler, middleware ...Middleware) http.Handler {
    for i := len(middleware) - 1; i >= 0; i-- {
        h = middleware[i](h)
    }
    return h
}

func Logging(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        start := time.Now()
        next.ServeHTTP(w, r)
        log.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(start))
    })
}

func RequireJSON(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if r.Method != http.MethodGet && r.Header.Get("Content-Type") != "application/json" {
            writeError(w, http.StatusUnsupportedMediaType, errUnsupported)
            return
        }
        next.ServeHTTP(w, r)
    })
}
"""))
        .sym("type_alias:Middleware", "function:Chain", "function:Logging", "function:RequireJSON")
        .calls("function:RequireJSON", server, "function:writeError", "cross-file"))

    (_code(repo, server, _tabs("""
package httpapi

import (
    "encoding/json"
    "net/http"

    "github.com/acme/shop/gateway/internal/store"
)

type Server struct {
    store  store.Store
    router *http.ServeMux
}

func NewServer(s store.Store) *Server {
    srv := &Server{store: s, router: http.NewServeMux()}
    srv.routes()
    return srv
}

func (s *Server) routes() {
    s.router.Handle("/items/", Chain(http.HandlerFunc(s.handleGet), Logging, RequireJSON))
    s.router.HandleFunc("/healthz", s.handleHealth)
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    s.router.ServeHTTP(w, r)
}

func (s *Server) handleGet(w http.ResponseWriter, r *http.Request) {
    id := r.URL.Path[len("/items/"):]
    value, err := s.store.Get(id)
    if err != nil {
        writeError(w, http.StatusNotFound, err)
        return
    }
    writeJSON(w, http.StatusOK, map[string]string{"id": id, "value": value})
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
    writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func writeJSON(w http.ResponseWriter, status int, body any) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    _ = json.NewEncoder(w).Encode(body)
}

func writeError(w http.ResponseWriter, status int, err error) {
    writeJSON(w, status, map[string]string{"error": err.Error()})
}
"""))
        .sym("struct:Server", "function:NewServer", "method:Server.routes", "method:Server.ServeHTTP",
             "method:Server.handleGet", "method:Server.handleHealth", "function:writeJSON",
             "function:writeError")
        .calls("function:NewServer", server, "method:Server.routes", "typed-member")
        .calls("method:Server.routes", mw, "function:Chain", "cross-file")
        .calls("method:Server.handleGet", store, "method:Store.Get", "interface-dispatch")
        .calls("method:Server.handleGet", server, "function:writeError")
        .calls("method:Server.handleGet", server, "function:writeJSON")
        .calls("method:Server.handleHealth", server, "function:writeJSON")
        .calls("function:writeError", server, "function:writeJSON"))

    (_code(repo, f"{root}/internal/http/server_test.go", _tabs("""
package httpapi

import (
    "net/http"
    "net/http/httptest"
    "testing"

    "github.com/acme/shop/gateway/internal/store"
)

func TestGetReturnsStoredItem(t *testing.T) {
    s := store.NewMemoryStore()
    if err := s.Put("sku-1", "widget"); err != nil {
        t.Fatal(err)
    }
    srv := NewServer(s)
    rec := httptest.NewRecorder()
    srv.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/items/sku-1", nil))
    if rec.Code != http.StatusOK {
        t.Fatalf("status = %d, want 200", rec.Code)
    }
}

func TestMissingItemIs404(t *testing.T) {
    srv := NewServer(store.NewMemoryStore())
    rec := httptest.NewRecorder()
    srv.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/items/nope", nil))
    if rec.Code != http.StatusNotFound {
        t.Fatalf("status = %d, want 404", rec.Code)
    }
}
"""), role="test")
        .sym("function:TestGetReturnsStoredItem", "function:TestMissingItemIs404")
        .calls("function:TestGetReturnsStoredItem", store, "function:NewMemoryStore", "namespace")
        .calls("function:TestGetReturnsStoredItem", store, "method:MemoryStore.Put", "typed-member")
        .calls("function:TestGetReturnsStoredItem", server, "function:NewServer")
        .calls("function:TestGetReturnsStoredItem", server, "method:Server.ServeHTTP", "typed-member")
        .calls("function:TestMissingItemIs404", server, "function:NewServer")
        .calls("function:TestMissingItemIs404", store, "function:NewMemoryStore", "namespace")
        .calls("function:TestMissingItemIs404", server, "method:Server.ServeHTTP", "typed-member"))

    (_code(repo, build_go, _tabs("""
package build

import "fmt"

var (
    Version   = "dev"
    Commit    = "none"
    BuildTime = "unknown"
)

type Info struct {
    Version   string `json:"version"`
    Commit    string `json:"commit"`
    BuildTime string `json:"buildTime"`
}

func Current() Info {
    return Info{Version: Version, Commit: Commit, BuildTime: BuildTime}
}

func (i Info) String() string {
    return fmt.Sprintf("gateway %s (%s, built %s)", i.Version, i.Commit, i.BuildTime)
}
"""), tags=("P-3", "P-7"))
        .sym("struct:Info", "function:Current", "method:Info.String"))

    (_code(repo, main, _tabs("""
package main

import (
    "log"
    "net/http"
    "os"

    "github.com/acme/shop/gateway/internal/build"
    httpapi "github.com/acme/shop/gateway/internal/http"
    "github.com/acme/shop/gateway/internal/store"
)

func main() {
    addr := envOr("GATEWAY_ADDR", ":8080")
    log.Println(build.Current().String())
    srv := httpapi.NewServer(store.NewMemoryStore())
    log.Fatal(http.ListenAndServe(addr, srv))
}

func envOr(key, fallback string) string {
    if v := os.Getenv(key); v != "" {
        return v
    }
    return fallback
}
"""))
        .sym("function:main", "function:envOr")
        .calls("function:main", main, "function:envOr")
        .calls("function:main", build_go, "function:Current", "namespace")
        .calls("function:main", build_go, "method:Info.String", "chained")
        .calls("function:main", server, "function:NewServer", "namespace")
        .calls("function:main", store, "function:NewMemoryStore", "namespace"))


# ------------------------------------------------------------------- Rust

def _rust(repo: Repo) -> None:
    root = f"{SVC}/pricing-rs"
    lib, discount = f"{root}/src/lib.rs", f"{root}/src/discount.rs"
    rules, engine = f"{root}/src/rules.rs", f"{root}/src/engine.rs"

    _meta(repo, f"{root}/Cargo.toml", _src("""
[package]
name = "pricing"
version = "0.4.2"
edition = "2021"

[lib]
name = "pricing"
path = "src/lib.rs"

[dependencies]
"""))
    _skip(repo, f"{root}/Cargo.lock", _src("""
# This file is automatically @generated by Cargo.
# It is not intended for manual editing.
version = 3

[[package]]
name = "pricing"
version = "0.4.2"
"""), role="lockfile")

    (_code(repo, lib, _src("""
pub mod discount;
pub mod engine;
pub mod rules;

pub use discount::compute_discount;
pub use engine::PriceEngine;
"""))
        .sym("module:discount", "module:engine", "module:rules")
        .imports(discount, "function:compute_discount", cat="rust-mod")
        .imports(engine, "struct:PriceEngine", cat="rust-mod")
        .imports(rules, cat="rust-mod"))

    (_code(repo, rules, _src("""
pub trait Rule {
    fn rate(&self, subtotal_cents: i64) -> f64;
    fn name(&self) -> &'static str;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tier {
    Standard,
    Silver,
    Gold,
}

pub struct FlatRule {
    pub percent: f64,
}

pub struct ThresholdRule {
    pub threshold_cents: i64,
    pub percent: f64,
}

impl Rule for FlatRule {
    fn rate(&self, _subtotal_cents: i64) -> f64 {
        self.percent / 100.0
    }

    fn name(&self) -> &'static str {
        "flat"
    }
}

impl Rule for ThresholdRule {
    fn rate(&self, subtotal_cents: i64) -> f64 {
        if subtotal_cents >= self.threshold_cents {
            self.percent / 100.0
        } else {
            0.0
        }
    }

    fn name(&self) -> &'static str {
        "threshold"
    }
}

impl Tier {
    pub fn rule(self) -> Box<dyn Rule> {
        match self {
            Tier::Standard => Box::new(FlatRule { percent: 0.0 }),
            Tier::Silver => Box::new(FlatRule { percent: 5.0 }),
            Tier::Gold => Box::new(ThresholdRule { threshold_cents: 10_000, percent: 10.0 }),
        }
    }
}
"""))
        .sym("trait:Rule", "method:Rule.rate", "method:Rule.name", "enum:Tier", "struct:FlatRule",
             "struct:ThresholdRule", "method:FlatRule.rate", "method:FlatRule.name",
             "method:ThresholdRule.rate", "method:ThresholdRule.name", "method:Tier.rule")
        .inherits("struct:FlatRule", rules, "trait:Rule", rel="IMPLEMENTS")
        .inherits("struct:ThresholdRule", rules, "trait:Rule", rel="IMPLEMENTS")
        .overrides("method:FlatRule.rate", rules, "method:Rule.rate")
        .overrides("method:FlatRule.name", rules, "method:Rule.name")
        .overrides("method:ThresholdRule.rate", rules, "method:Rule.rate")
        .overrides("method:ThresholdRule.name", rules, "method:Rule.name"))

    (_code(repo, discount, _src("""
use crate::rules::{Rule, Tier};

pub fn compute_discount(subtotal_cents: i64, tier: Tier) -> i64 {
    let rule: Box<dyn Rule> = tier.rule();
    let rate = rule.rate(subtotal_cents);
    round_cents(subtotal_cents as f64 * rate)
}

fn round_cents(value: f64) -> i64 {
    value.round() as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gold_tier_gets_ten_percent_over_threshold() {
        assert_eq!(compute_discount(20_000, Tier::Gold), 2_000);
    }
}
"""), tags=("P-3", "R-2"))
        .sym("function:compute_discount", "function:round_cents", "module:tests",
             "function:tests.gold_tier_gets_ten_percent_over_threshold")
        .imports(rules, "trait:Rule", "enum:Tier")
        .calls("function:compute_discount", rules, "method:Tier.rule", "typed-member")
        .calls("function:compute_discount", rules, "method:Rule.rate", "interface-dispatch")
        .calls("function:compute_discount", discount, "function:round_cents")
        .calls("function:tests.gold_tier_gets_ten_percent_over_threshold", discount,
               "function:compute_discount"))

    (_code(repo, engine, _src("""
use crate::discount::compute_discount;
use crate::rules::Tier;

pub struct LineItem {
    pub sku: String,
    pub unit_cents: i64,
    pub quantity: u32,
}

pub struct PriceEngine {
    tax_rate: f64,
}

impl PriceEngine {
    pub fn new(tax_rate: f64) -> Self {
        PriceEngine { tax_rate }
    }

    pub fn subtotal(&self, items: &[LineItem]) -> i64 {
        items.iter().map(|i| i.unit_cents * i64::from(i.quantity)).sum()
    }

    pub fn quote(&self, items: &[LineItem], tier: Tier) -> Quote {
        let subtotal = self.subtotal(items);
        let discount = compute_discount(subtotal, tier);
        let taxable = subtotal - discount;
        Quote {
            subtotal_cents: subtotal,
            discount_cents: discount,
            tax_cents: self.tax(taxable),
        }
    }

    fn tax(&self, taxable_cents: i64) -> i64 {
        (taxable_cents as f64 * self.tax_rate).round() as i64
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct Quote {
    pub subtotal_cents: i64,
    pub discount_cents: i64,
    pub tax_cents: i64,
}
"""))
        .sym("struct:LineItem", "struct:PriceEngine", "method:PriceEngine.new",
             "method:PriceEngine.subtotal", "method:PriceEngine.quote", "method:PriceEngine.tax",
             "struct:Quote")
        .imports(discount, "function:compute_discount")
        .imports(rules, "enum:Tier")
        .calls("method:PriceEngine.quote", engine, "method:PriceEngine.subtotal", "self-member")
        .calls("method:PriceEngine.quote", discount, "function:compute_discount", "cross-file")
        .calls("method:PriceEngine.quote", engine, "method:PriceEngine.tax", "self-member"))

    (_code(repo, f"{root}/tests/quote.rs", _src("""
use pricing::engine::LineItem;
use pricing::rules::Tier;
use pricing::PriceEngine;

#[test]
fn gold_quote_applies_discount_before_tax() {
    let engine = PriceEngine::new(0.2);
    let items = vec![LineItem { sku: "A1".into(), unit_cents: 5_000, quantity: 3 }];
    let quote = engine.quote(&items, Tier::Gold);
    assert_eq!(quote.subtotal_cents, 15_000);
    assert_eq!(quote.discount_cents, 1_500);
    assert_eq!(quote.tax_cents, 2_700);
}
"""), role="test")
        .sym("function:gold_quote_applies_discount_before_tax")
        .imports(engine, "struct:LineItem")
        .imports(rules, "enum:Tier")
        .imports(engine, "struct:PriceEngine", via=lib, cat="barrel")
        .calls("function:gold_quote_applies_discount_before_tax", engine, "method:PriceEngine.new",
               "classmethod")
        .calls("function:gold_quote_applies_discount_before_tax", engine,
               "method:PriceEngine.quote", "typed-member"))


# ------------------------------------------------------------------- Java

def _java(repo: Repo) -> None:
    root = f"{SVC}/payments-java"
    pkg = f"{root}/src/main/java/com/acme/payments"
    gateway, result = f"{pkg}/PaymentGateway.java", f"{pkg}/ChargeResult.java"
    stripe, service = f"{pkg}/StripeGateway.java", f"{pkg}/PaymentService.java"
    builder = f"{root}/src/main/java/com/acme/build/Builder.java"

    _meta(repo, f"{root}/pom.xml", _src("""
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.acme</groupId>
  <artifactId>payments</artifactId>
  <version>1.3.0</version>
  <properties>
    <maven.compiler.release>17</maven.compiler.release>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.10.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""))

    (_code(repo, gateway, _src("""
package com.acme.payments;

import java.math.BigDecimal;

public interface PaymentGateway {
    ChargeResult charge(String orderId, BigDecimal amount);

    void refund(String chargeId);
}
"""))
        .sym("interface:PaymentGateway", "method:PaymentGateway.charge", "method:PaymentGateway.refund"))

    (_code(repo, result, _src("""
package com.acme.payments;

public final class ChargeResult {
    private final String chargeId;
    private final boolean approved;

    public ChargeResult(String chargeId, boolean approved) {
        this.chargeId = chargeId;
        this.approved = approved;
    }

    public String getChargeId() {
        return chargeId;
    }

    public boolean isApproved() {
        return approved;
    }
}
"""))
        .sym("class:ChargeResult", "method:ChargeResult.ChargeResult", "method:ChargeResult.getChargeId",
             "method:ChargeResult.isApproved"))

    (_code(repo, stripe, _src("""
package com.acme.payments;

import java.math.BigDecimal;
import java.util.UUID;
import java.util.logging.Logger;

public class StripeGateway implements PaymentGateway {
    private static final Logger LOG = Logger.getLogger(StripeGateway.class.getName());

    private final String apiKey;

    public StripeGateway(String apiKey) {
        this.apiKey = apiKey;
    }

    @Override
    public ChargeResult charge(String orderId, BigDecimal amount) {
        if (amount.signum() <= 0) {
            return new ChargeResult(null, false);
        }
        String chargeId = "ch_" + UUID.randomUUID();
        LOG.info(() -> "charged " + orderId + " " + amount + " as " + chargeId);
        return new ChargeResult(chargeId, true);
    }

    @Override
    public void refund(String chargeId) {
        LOG.info(() -> "refunded " + chargeId);
    }

    boolean isConfigured() {
        return apiKey != null && !apiKey.isBlank();
    }
}
"""))
        .sym("class:StripeGateway", "method:StripeGateway.StripeGateway", "method:StripeGateway.charge",
             "method:StripeGateway.refund", "method:StripeGateway.isConfigured")
        .inherits("class:StripeGateway", gateway, "interface:PaymentGateway", rel="IMPLEMENTS")
        .overrides("method:StripeGateway.charge", gateway, "method:PaymentGateway.charge")
        .overrides("method:StripeGateway.refund", gateway, "method:PaymentGateway.refund")
        .calls("method:StripeGateway.charge", result, "class:ChargeResult", "ctor"))

    (_code(repo, service, _src("""
package com.acme.payments;

import com.acme.build.Builder;
import java.math.BigDecimal;
import java.util.Objects;

public class PaymentService {
    private final PaymentGateway gateway;

    public PaymentService(PaymentGateway gateway) {
        this.gateway = Objects.requireNonNull(gateway);
    }

    public ChargeResult pay(String orderId, BigDecimal amount) {
        validate(orderId, amount);
        return gateway.charge(orderId, amount);
    }

    public void cancel(ChargeResult result) {
        if (result.isApproved()) {
            gateway.refund(result.getChargeId());
        }
    }

    public String describe() {
        return Builder.describe("payments", gateway.getClass().getSimpleName());
    }

    private static void validate(String orderId, BigDecimal amount) {
        if (orderId == null || orderId.isEmpty()) {
            throw new IllegalArgumentException("orderId is required");
        }
        if (amount == null || amount.signum() <= 0) {
            throw new IllegalArgumentException("amount must be positive");
        }
    }
}
"""))
        .sym("class:PaymentService", "method:PaymentService.PaymentService", "method:PaymentService.pay",
             "method:PaymentService.cancel", "method:PaymentService.describe",
             "method:PaymentService.validate")
        .imports(builder, "class:Builder")
        .calls("method:PaymentService.pay", service, "method:PaymentService.validate", "self-member")
        .calls("method:PaymentService.pay", gateway, "method:PaymentGateway.charge", "interface-dispatch")
        .calls("method:PaymentService.cancel", result, "method:ChargeResult.isApproved", "typed-member")
        .calls("method:PaymentService.cancel", result, "method:ChargeResult.getChargeId", "typed-member")
        .calls("method:PaymentService.cancel", gateway, "method:PaymentGateway.refund", "interface-dispatch")
        .calls("method:PaymentService.describe", builder, "method:Builder.describe", "classmethod"))

    (_code(repo, builder, _src("""
package com.acme.build;

public final class Builder {
    private final StringBuilder parts = new StringBuilder();

    private Builder() {
    }

    public static Builder create() {
        return new Builder();
    }

    public Builder add(String key, String value) {
        if (parts.length() > 0) {
            parts.append(", ");
        }
        parts.append(key).append('=').append(value);
        return this;
    }

    public String build() {
        return "{" + parts + "}";
    }

    public static String describe(String service, String implementation) {
        return create().add("service", service).add("impl", implementation).build();
    }
}
"""), tags=("P-3", "P-7"))
        .sym("class:Builder", "method:Builder.Builder", "method:Builder.create", "method:Builder.add",
             "method:Builder.build", "method:Builder.describe")
        .calls("method:Builder.create", builder, "class:Builder", "ctor")
        .calls("method:Builder.describe", builder, "method:Builder.create")
        .calls("method:Builder.describe", builder, "method:Builder.add", "chained")
        .calls("method:Builder.describe", builder, "method:Builder.build", "chained"))

    test = f"{root}/src/test/java/com/acme/payments/PaymentServiceTest.java"
    (_code(repo, test, _src("""
package com.acme.payments;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.math.BigDecimal;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class PaymentServiceTest {
    private PaymentService service;

    @BeforeEach
    void setUp() {
        service = new PaymentService(new StripeGateway("test-key"));
    }

    @Test
    void approvesPositiveAmounts() {
        ChargeResult result = service.pay("order-1", new BigDecimal("12.50"));
        assertTrue(result.isApproved());
    }

    @Test
    void rejectsMissingOrderId() {
        assertThrows(IllegalArgumentException.class, () -> service.pay("", BigDecimal.ONE));
    }

    @Test
    void cancelSkipsDeclinedCharges() {
        ChargeResult declined = new ChargeResult(null, false);
        service.cancel(declined);
        assertFalse(declined.isApproved());
    }
}
"""), role="test")
        .sym("class:PaymentServiceTest", "method:PaymentServiceTest.setUp",
             "method:PaymentServiceTest.approvesPositiveAmounts",
             "method:PaymentServiceTest.rejectsMissingOrderId",
             "method:PaymentServiceTest.cancelSkipsDeclinedCharges")
        .calls("method:PaymentServiceTest.setUp", service, "class:PaymentService", "ctor")
        .calls("method:PaymentServiceTest.setUp", stripe, "class:StripeGateway", "ctor")
        .calls("method:PaymentServiceTest.approvesPositiveAmounts", service, "method:PaymentService.pay",
               "attr-member")
        .calls("method:PaymentServiceTest.approvesPositiveAmounts", result,
               "method:ChargeResult.isApproved", "typed-member")
        .calls("method:PaymentServiceTest.rejectsMissingOrderId", service, "method:PaymentService.pay",
               "attr-member")
        .calls("method:PaymentServiceTest.cancelSkipsDeclinedCharges", result, "class:ChargeResult", "ctor")
        .calls("method:PaymentServiceTest.cancelSkipsDeclinedCharges", service,
               "method:PaymentService.cancel", "attr-member")
        .calls("method:PaymentServiceTest.cancelSkipsDeclinedCharges", result,
               "method:ChargeResult.isApproved", "typed-member"))


# ----------------------------------------------------------------- Kotlin

def _kotlin(repo: Repo) -> None:
    root = f"{SVC}/notifications-kt"
    pkg = f"{root}/src/main/kotlin/com/acme/notifications"
    notifier, email, dispatcher = f"{pkg}/Notifier.kt", f"{pkg}/EmailNotifier.kt", f"{pkg}/Dispatcher.kt"

    repo.add(f"{root}/build.gradle.kts", _src("""
plugins {
    kotlin("jvm") version "1.9.24"
    application
}

repositories {
    mavenCentral()
}

dependencies {
    testImplementation(kotlin("test"))
}

application {
    mainClass.set("com.acme.notifications.DispatcherKt")
}
"""), role="build")

    (_code(repo, notifier, _src("""
package com.acme.notifications

data class Message(val recipient: String, val subject: String, val body: String)

interface Notifier {
    val channel: String

    fun send(message: Message): Boolean

    fun supports(recipient: String): Boolean = recipient.isNotBlank()
}

fun Message.preview(maxLength: Int = 40): String =
    if (body.length <= maxLength) body else body.take(maxLength) + "…"
"""))
        .sym("class:Message", "interface:Notifier", "method:Notifier.send", "method:Notifier.supports",
             "function:preview"))

    (_code(repo, email, _src(r"""
package com.acme.notifications

class EmailNotifier(private val sender: String) : Notifier {
    override val channel: String = "email"

    override fun send(message: Message): Boolean {
        if (!supports(message.recipient)) return false
        println("[$channel] from=$sender to=${message.recipient}: ${message.preview()}")
        return true
    }

    override fun supports(recipient: String): Boolean = EMAIL.matches(recipient)

    companion object {
        private val EMAIL = Regex("^[^@\\s]+@[^@\\s]+$")
    }
}
"""))
        .sym("class:EmailNotifier", "method:EmailNotifier.send", "method:EmailNotifier.supports")
        .inherits("class:EmailNotifier", notifier, "interface:Notifier", rel="IMPLEMENTS")
        .overrides("method:EmailNotifier.send", notifier, "method:Notifier.send")
        .overrides("method:EmailNotifier.supports", notifier, "method:Notifier.supports")
        .calls("method:EmailNotifier.send", email, "method:EmailNotifier.supports", "self-member")
        .calls("method:EmailNotifier.send", notifier, "function:preview", "extension"))

    (_code(repo, dispatcher, _src("""
package com.acme.notifications

object Dispatcher {
    private val notifiers = mutableListOf<Notifier>()

    fun register(notifier: Notifier) {
        notifiers += notifier
    }

    fun dispatch(message: Message): Int =
        notifiers.count { it.supports(message.recipient) && it.send(message) }
}

fun defaultDispatcher(sender: String): Dispatcher {
    Dispatcher.register(EmailNotifier(sender))
    return Dispatcher
}

fun main() {
    val dispatcher = defaultDispatcher("noreply@acme.test")
    val sent = dispatcher.dispatch(Message("ada@acme.test", "Order shipped", "Your order is on its way."))
    println("sent=$sent")
}
"""))
        .sym("class:Dispatcher", "method:Dispatcher.register", "method:Dispatcher.dispatch",
             "function:defaultDispatcher", "function:main")
        .calls("method:Dispatcher.dispatch", notifier, "method:Notifier.supports", "interface-dispatch")
        .calls("method:Dispatcher.dispatch", notifier, "method:Notifier.send", "interface-dispatch")
        .calls("function:defaultDispatcher", dispatcher, "method:Dispatcher.register", "classmethod")
        .calls("function:defaultDispatcher", email, "class:EmailNotifier", "ctor")
        .calls("function:main", dispatcher, "function:defaultDispatcher")
        .calls("function:main", dispatcher, "method:Dispatcher.dispatch", "typed-member")
        .calls("function:main", notifier, "class:Message", "ctor"))

    test = f"{root}/src/test/kotlin/com/acme/notifications/DispatcherTest.kt"
    (_code(repo, test, _src("""
package com.acme.notifications

import kotlin.test.Test
import kotlin.test.assertEquals

class DispatcherTest {
    private class RecordingNotifier : Notifier {
        val sent = mutableListOf<Message>()
        override val channel = "test"
        override fun send(message: Message): Boolean = sent.add(message)
    }

    @Test
    fun dispatchesToSupportingNotifiers() {
        val notifier = RecordingNotifier()
        Dispatcher.register(notifier)
        val delivered = Dispatcher.dispatch(Message("ada@acme.test", "Hi", "Hello"))
        assertEquals(1, delivered)
        assertEquals("Hello", notifier.sent.single().body)
    }
}
"""), role="test")
        .sym("class:DispatcherTest", "class:DispatcherTest.RecordingNotifier",
             "method:DispatcherTest.RecordingNotifier.send",
             "method:DispatcherTest.dispatchesToSupportingNotifiers")
        .inherits("class:DispatcherTest.RecordingNotifier", notifier, "interface:Notifier",
                  rel="IMPLEMENTS")
        .overrides("method:DispatcherTest.RecordingNotifier.send", notifier, "method:Notifier.send")
        .calls("method:DispatcherTest.dispatchesToSupportingNotifiers", test,
               "class:DispatcherTest.RecordingNotifier", "ctor")
        .calls("method:DispatcherTest.dispatchesToSupportingNotifiers", dispatcher,
               "method:Dispatcher.register", "classmethod")
        .calls("method:DispatcherTest.dispatchesToSupportingNotifiers", dispatcher,
               "method:Dispatcher.dispatch", "classmethod")
        .calls("method:DispatcherTest.dispatchesToSupportingNotifiers", notifier, "class:Message", "ctor"))


# ------------------------------------------------------------------ Scala

def _scala(repo: Repo) -> None:
    root = f"{SVC}/analytics-scala"
    metrics, job = f"{root}/src/main/scala/acme/Metrics.scala", f"{root}/src/main/scala/acme/Job.scala"

    _meta(repo, f"{root}/build.sbt", _src("""
ThisBuild / scalaVersion := "2.13.14"

lazy val analytics = (project in file("."))
  .settings(
    name := "acme-analytics",
    libraryDependencies += "org.scalatest" %% "scalatest" % "3.2.18" % Test
  )
"""))

    (_code(repo, metrics, _src("""
package acme

final case class Event(name: String, userId: String, value: Double)

trait Aggregator {
  def metricName(): String
  def aggregate(events: Seq[Event]): Double
}

class SumAggregator extends Aggregator {
  override def metricName(): String = "sum"
  override def aggregate(events: Seq[Event]): Double = events.map(_.value).sum
}

class UniqueUsers extends Aggregator {
  override def metricName(): String = "unique_users"
  override def aggregate(events: Seq[Event]): Double = events.map(_.userId).distinct.size.toDouble
}

object Metrics {
  def defaults: Seq[Aggregator] = Seq(new SumAggregator, new UniqueUsers)

  def summarize(events: Seq[Event], aggregators: Seq[Aggregator]): Map[String, Double] =
    aggregators.map(a => a.metricName() -> a.aggregate(events)).toMap

  def summarizeAll(events: Seq[Event]): Map[String, Double] = summarize(events, defaults)
}
"""))
        .sym("class:Event", "trait:Aggregator", "method:Aggregator.metricName",
             "method:Aggregator.aggregate", "class:SumAggregator", "method:SumAggregator.metricName",
             "method:SumAggregator.aggregate", "class:UniqueUsers", "method:UniqueUsers.metricName",
             "method:UniqueUsers.aggregate", "class:Metrics", "method:Metrics.defaults",
             "method:Metrics.summarize", "method:Metrics.summarizeAll")
        .inherits("class:SumAggregator", metrics, "trait:Aggregator", rel="IMPLEMENTS")
        .inherits("class:UniqueUsers", metrics, "trait:Aggregator", rel="IMPLEMENTS")
        .overrides("method:SumAggregator.metricName", metrics, "method:Aggregator.metricName")
        .overrides("method:SumAggregator.aggregate", metrics, "method:Aggregator.aggregate")
        .overrides("method:UniqueUsers.metricName", metrics, "method:Aggregator.metricName")
        .overrides("method:UniqueUsers.aggregate", metrics, "method:Aggregator.aggregate")
        .calls("method:Metrics.defaults", metrics, "class:SumAggregator", "ctor")
        .calls("method:Metrics.defaults", metrics, "class:UniqueUsers", "ctor")
        .calls("method:Metrics.summarize", metrics, "method:Aggregator.metricName", "interface-dispatch")
        .calls("method:Metrics.summarize", metrics, "method:Aggregator.aggregate", "interface-dispatch")
        .calls("method:Metrics.summarizeAll", metrics, "method:Metrics.summarize")
        .calls("method:Metrics.summarizeAll", metrics, "method:Metrics.defaults"))

    (_code(repo, job, _src("""
package acme

import scala.io.Source

object Job {
  def parse(line: String): Option[Event] =
    line.split(",").map(_.trim) match {
      case Array(name, user, value) => value.toDoubleOption.map(Event(name, user, _))
      case _                        => None
    }

  def load(path: String): Seq[Event] = {
    val source = Source.fromFile(path)
    try source.getLines().flatMap(parse).toSeq
    finally source.close()
  }

  def main(args: Array[String]): Unit = {
    val events = load(args.headOption.getOrElse("events.csv"))
    Metrics.summarizeAll(events).foreach { case (metric, value) => println(f"$metric%-14s $value%.2f") }
  }
}
"""))
        .sym("class:Job", "method:Job.parse", "method:Job.load", "method:Job.main")
        .calls("method:Job.parse", metrics, "class:Event", "ctor")
        .calls("method:Job.main", job, "method:Job.load")
        .calls("method:Job.main", metrics, "method:Metrics.summarizeAll", "classmethod"))

    spec = f"{root}/src/test/scala/acme/JobSpec.scala"
    (_code(repo, spec, _src("""
package acme

import org.scalatest.funsuite.AnyFunSuite

class JobSpec extends AnyFunSuite {
  test("parse reads a well-formed line") {
    assert(Job.parse("checkout, u1, 12.5").contains(Event("checkout", "u1", 12.5)))
  }

  test("sum adds event values") {
    val events = Seq(Event("a", "u1", 1.0), Event("b", "u2", 2.0))
    assert(new SumAggregator().aggregate(events) == 3.0)
  }
}
"""), role="test", tags=("P-3", "P-6"))
        .sym("class:JobSpec")
        .calls("class:JobSpec", job, "method:Job.parse", "classmethod")
        .calls("class:JobSpec", metrics, "class:Event", "ctor")
        .calls("class:JobSpec", metrics, "class:SumAggregator", "ctor")
        .calls("class:JobSpec", metrics, "method:SumAggregator.aggregate", "chained"))


# ----------------------------------------------------------------- Groovy

def _groovy(repo: Repo) -> None:
    root = f"{SVC}/ci"
    deploy = f"{root}/src/Deploy.groovy"

    (_code(repo, deploy, _src("""
class Deploy {
    String env;
    List<String> services = [];

    void rollout() {
        for (String name : services) {
            if (healthy(name)) {
                println("deploying ${name} to ${env}")
            } else {
                println("skipping ${name}: health check failed")
            }
        }
    }

    boolean healthy(String name) {
        return name && !name.contains(' ')
    }
}
"""))
        .sym("class:Deploy", "method:Deploy.rollout", "method:Deploy.healthy")
        .calls("method:Deploy.rollout", deploy, "method:Deploy.healthy", "self-member"))

    (_code(repo, f"{root}/Jenkinsfile.groovy", _src("""
def deployer = new Deploy(env: 'staging', services: ['gateway', 'pricing', 'payments'])

pipeline {
    agent any
    stages {
        stage('Build') {
            steps {
                sh 'make -C services/native'
            }
        }
        stage('Deploy') {
            steps {
                script {
                    deployer.rollout()
                }
            }
        }
    }
}
"""), role="build")
        .calls(None, deploy, "class:Deploy", "ctor")
        .calls(None, deploy, "method:Deploy.rollout", "typed-member"))

    repo.add(f"{root}/build.gradle", _src("""
plugins {
    id 'groovy'
}

repositories {
    mavenCentral()
}

dependencies {
    implementation 'org.codehaus.groovy:groovy-all:3.0.21'
}

sourceSets {
    main {
        groovy {
            srcDirs = ['src']
        }
    }
}
"""), role="build")


# ---------------------------------------------------------------------- C

def _c(repo: Repo) -> None:
    root = f"{SVC}/native"
    header, resize, clamp = f"{root}/include/resize.h", f"{root}/src/resize.c", f"{root}/src/clamp.c"

    (_code(repo, header, _src("""
#ifndef ACME_RESIZE_H
#define ACME_RESIZE_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    int width;
    int height;
    uint8_t *pixels;
} image_t;

int clamp_int(int value, int low, int high);
image_t *image_alloc(int width, int height);
void image_free(image_t *img);
int resize_nearest(const image_t *src, image_t *dst);

#endif
"""))
        .sym("struct:image_t"))

    (_code(repo, clamp, _src("""
#include "resize.h"

int clamp_int(int value, int low, int high) {
    if (value < low) {
        return low;
    }
    if (value > high) {
        return high;
    }
    return value;
}
"""))
        .sym("function:clamp_int")
        .imports(header, cat="include"))

    (_code(repo, resize, _src("""
#include <stdlib.h>

#include "resize.h"

image_t *image_alloc(int width, int height) {
    image_t *img = malloc(sizeof(image_t));
    if (img == NULL) {
        return NULL;
    }
    img->width = width;
    img->height = height;
    img->pixels = calloc((size_t)width * (size_t)height, 1);
    if (img->pixels == NULL) {
        free(img);
        return NULL;
    }
    return img;
}

void image_free(image_t *img) {
    if (img != NULL) {
        free(img->pixels);
        free(img);
    }
}

int resize_nearest(const image_t *src, image_t *dst) {
    if (src == NULL || dst == NULL || src->width == 0 || src->height == 0) {
        return -1;
    }
    for (int y = 0; y < dst->height; y++) {
        int sy = clamp_int(y * src->height / dst->height, 0, src->height - 1);
        for (int x = 0; x < dst->width; x++) {
            int sx = clamp_int(x * src->width / dst->width, 0, src->width - 1);
            dst->pixels[y * dst->width + x] = src->pixels[sy * src->width + sx];
        }
    }
    return 0;
}
"""))
        .sym("function:image_alloc", "function:image_free", "function:resize_nearest")
        .imports(header, cat="include")
        .calls("function:resize_nearest", clamp, "function:clamp_int", "cross-file"))

    repo.add(f"{root}/Makefile", (
        "CC ?= cc\n"
        "CFLAGS ?= -O2 -Wall -Iinclude\n"
        "\n"
        "libresize.a: src/resize.o src/clamp.o\n"
        "\tar rcs $@ $^\n"
        "\n"
        "clean:\n"
        "\trm -f src/*.o libresize.a\n"
    ), lang=None, role="build")


# -------------------------------------------------------------------- C++

def _cpp(repo: Repo) -> None:
    root = f"{SVC}/native-cpp"
    hpp, impl = f"{root}/include/img/resizer.hpp", f"{root}/src/resizer.cpp"
    main = f"{root}/src/main.cpp"

    (_code(repo, hpp, _src("""
#pragma once

#include <cstdint>
#include <vector>

namespace img {

struct Size {
    int width;
    int height;
};

class Resizer {
public:
    explicit Resizer(Size target);
    std::vector<std::uint8_t> run(const std::vector<std::uint8_t>& pixels, Size source) const;
    Size target() const { return target_; }

private:
    int sourceIndex(int x, int y, Size source) const;
    Size target_;
};

}  // namespace img
""", ))
        .sym("module:img", "struct:img.Size", "class:img.Resizer", "method:img.Resizer.target"))

    (_code(repo, impl, _src("""
#include "img/resizer.hpp"

#include <algorithm>

namespace img {

Resizer::Resizer(Size target) : target_(target) {}

std::vector<std::uint8_t> Resizer::run(const std::vector<std::uint8_t>& pixels, Size source) const {
    std::vector<std::uint8_t> out(static_cast<std::size_t>(target_.width) * target_.height);
    for (int y = 0; y < target_.height; ++y) {
        for (int x = 0; x < target_.width; ++x) {
            out[static_cast<std::size_t>(y) * target_.width + x] = pixels[sourceIndex(x, y, source)];
        }
    }
    return out;
}

int Resizer::sourceIndex(int x, int y, Size source) const {
    int sx = std::min(x * source.width / target_.width, source.width - 1);
    int sy = std::min(y * source.height / target_.height, source.height - 1);
    return sy * source.width + sx;
}

}  // namespace img
"""))
        .sym("module:img", "method:img.Resizer.Resizer", "method:img.Resizer.run",
             "method:img.Resizer.sourceIndex")
        .imports(hpp, cat="include")
        .calls("method:img.Resizer.run", impl, "method:img.Resizer.sourceIndex", "self-member"))

    (_code(repo, main, _src("""
#include <cstdio>
#include <vector>

#include "img/resizer.hpp"

int main() {
    img::Resizer resizer({2, 2});
    std::vector<std::uint8_t> pixels{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
    auto out = resizer.run(pixels, {4, 4});
    std::printf("%zu pixels, target %dx%d\\n", out.size(), resizer.target().width, resizer.target().height);
    return 0;
}
"""))
        .sym("function:main")
        .imports(hpp, cat="include")
        .calls("function:main", hpp, "class:img.Resizer", "ctor")
        .calls("function:main", impl, "method:img.Resizer.run", "typed-member")
        .calls("function:main", hpp, "method:img.Resizer.target", "typed-member"))

    (_code(repo, f"{root}/src/resize_test.cc", _src("""
#include <cassert>
#include <vector>

#include "img/resizer.hpp"

static void downscalesToTarget() {
    img::Resizer resizer({1, 1});
    std::vector<std::uint8_t> pixels{9, 9, 9, 9};
    auto out = resizer.run(pixels, {2, 2});
    assert(out.size() == 1);
    assert(out[0] == 9);
}

int main() {
    downscalesToTarget();
    return 0;
}
"""), role="test", tags=("P-3", "P-6"))
        .sym("function:downscalesToTarget", "function:main")
        .imports(hpp, cat="include")
        .calls("function:downscalesToTarget", hpp, "class:img.Resizer", "ctor")
        .calls("function:downscalesToTarget", impl, "method:img.Resizer.run", "typed-member")
        .calls("function:main", f"{root}/src/resize_test.cc", "function:downscalesToTarget"))

    repo.add(f"{root}/CMakeLists.txt", _src("""
cmake_minimum_required(VERSION 3.20)
project(acme_img CXX)

set(CMAKE_CXX_STANDARD 17)

add_library(img src/resizer.cpp)
target_include_directories(img PUBLIC include)

add_executable(resize_demo src/main.cpp)
target_link_libraries(resize_demo img)

enable_testing()
add_executable(resize_test src/resize_test.cc)
target_link_libraries(resize_test img)
add_test(NAME resize_test COMMAND resize_test)
"""), lang=None, role="build")


# --------------------------------------------------------------------- C#

def _csharp(repo: Repo) -> None:
    root = f"{SVC}/reporting-cs"
    ns = "Acme.Reporting"
    ireport, sales, runner = f"{root}/src/IReport.cs", f"{root}/src/SalesReport.cs", f"{root}/src/ReportRunner.cs"

    repo.add(f"{root}/Reporting.csproj", _src("""
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <RootNamespace>Acme.Reporting</RootNamespace>
  </PropertyGroup>
</Project>
"""), lang=None, role="build")

    (_code(repo, ireport, _src("""
using System.Collections.Generic;

namespace Acme.Reporting
{
    public interface IReport
    {
        string Title { get; }

        IReadOnlyList<string> Render();
    }
}
"""))
        .sym(f"module:{ns}", f"interface:{ns}.IReport", f"method:{ns}.IReport.Render"))

    (_code(repo, sales, _src("""
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace Acme.Reporting
{
    public sealed class SalesReport : IReport
    {
        private readonly IReadOnlyList<decimal> _dailyTotals;

        public SalesReport(IEnumerable<decimal> dailyTotals)
        {
            _dailyTotals = dailyTotals?.ToList() ?? throw new ArgumentNullException(nameof(dailyTotals));
        }

        public string Title => "Daily sales";

        public IReadOnlyList<string> Render()
        {
            var lines = new List<string> { Title };
            lines.AddRange(_dailyTotals.Select((total, day) => FormatLine(day + 1, total)));
            lines.Add(FormatLine(0, Total()));
            return lines;
        }

        public decimal Total() => _dailyTotals.Sum();

        private static string FormatLine(int day, decimal amount) =>
            day == 0
                ? $"Total: {amount.ToString("C", CultureInfo.InvariantCulture)}"
                : $"Day {day}: {amount.ToString("C", CultureInfo.InvariantCulture)}";
    }
}
"""))
        .sym(f"module:{ns}", f"class:{ns}.SalesReport", f"method:{ns}.SalesReport.SalesReport",
             f"method:{ns}.SalesReport.Render", f"method:{ns}.SalesReport.Total",
             f"method:{ns}.SalesReport.FormatLine")
        .inherits(f"class:{ns}.SalesReport", ireport, f"interface:{ns}.IReport", rel="IMPLEMENTS")
        .overrides(f"method:{ns}.SalesReport.Render", ireport, f"method:{ns}.IReport.Render")
        .calls(f"method:{ns}.SalesReport.Render", sales, f"method:{ns}.SalesReport.FormatLine")
        .calls(f"method:{ns}.SalesReport.Render", sales, f"method:{ns}.SalesReport.Total"))

    (_code(repo, runner, _src("""
using System;
using System.Collections.Generic;

namespace Acme.Reporting
{
    public static class ReportRunner
    {
        public static int Print(IReport report)
        {
            var lines = report.Render();
            foreach (var line in lines)
            {
                Console.WriteLine(line);
            }
            return lines.Count;
        }

        public static void Main(string[] args)
        {
            var report = new SalesReport(new List<decimal> { 120.5m, 98.25m, 143m });
            Print(report);
        }
    }
}
"""))
        .sym(f"module:{ns}", f"class:{ns}.ReportRunner", f"method:{ns}.ReportRunner.Print",
             f"method:{ns}.ReportRunner.Main")
        .calls(f"method:{ns}.ReportRunner.Print", ireport, f"method:{ns}.IReport.Render",
               "interface-dispatch")
        .calls(f"method:{ns}.ReportRunner.Main", sales, f"class:{ns}.SalesReport", "ctor")
        .calls(f"method:{ns}.ReportRunner.Main", runner, f"method:{ns}.ReportRunner.Print"))

    tns = f"{ns}.Tests"
    (_code(repo, f"{root}/tests/SalesReportTests.cs", _src("""
using Acme.Reporting;
using Xunit;

namespace Acme.Reporting.Tests
{
    public class SalesReportTests
    {
        [Fact]
        public void TotalSumsDailyAmounts()
        {
            var report = new SalesReport(new[] { 10m, 20m, 30.5m });
            Assert.Equal(60.5m, report.Total());
        }

        [Fact]
        public void RenderStartsWithTitle()
        {
            IReport report = new SalesReport(new[] { 1m });
            Assert.Equal("Daily sales", report.Render()[0]);
        }
    }
}
"""), role="test")
        .sym(f"module:{tns}", f"class:{tns}.SalesReportTests",
             f"method:{tns}.SalesReportTests.TotalSumsDailyAmounts",
             f"method:{tns}.SalesReportTests.RenderStartsWithTitle")
        .calls(f"method:{tns}.SalesReportTests.TotalSumsDailyAmounts", sales, f"class:{ns}.SalesReport", "ctor")
        .calls(f"method:{tns}.SalesReportTests.TotalSumsDailyAmounts", sales,
               f"method:{ns}.SalesReport.Total", "typed-member")
        .calls(f"method:{tns}.SalesReportTests.RenderStartsWithTitle", sales, f"class:{ns}.SalesReport", "ctor")
        .calls(f"method:{tns}.SalesReportTests.RenderStartsWithTitle", ireport,
               f"method:{ns}.IReport.Render", "interface-dispatch"))


# ------------------------------------------------------------------- Ruby

def _ruby(repo: Repo) -> None:
    root = f"{SVC}/admin-rb"
    base, user, exporter = f"{root}/lib/admin/base.rb", f"{root}/lib/admin/user.rb", f"{root}/lib/admin/exporter.rb"

    _meta(repo, f"{root}/Gemfile", _src("""
source "https://rubygems.org"

gem "csv", "~> 3.3"

group :test do
  gem "rspec", "~> 3.13"
end
"""))
    _skip(repo, f"{root}/Gemfile.lock", _src("""
GEM
  remote: https://rubygems.org/
  specs:
    csv (3.3.0)
    rspec (3.13.0)

PLATFORMS
  ruby

DEPENDENCIES
  csv (~> 3.3)
  rspec (~> 3.13)
"""), role="lockfile")

    (_code(repo, base, _src("""
# frozen_string_literal: true

module Admin
  def self.store
    @store ||= []
  end

  class Base
    attr_reader :attributes, :errors

    def initialize(attributes = {})
      @attributes = attributes.transform_keys(&:to_sym)
      @errors = []
    end

    def save
      validate
      return false unless errors.empty?

      persist
      true
    end

    def validate; end

    def to_h
      attributes.dup
    end

    private

    def persist
      Admin.store << to_h
    end
  end
end
"""))
        .sym("module:Admin", "method:Admin.store", "class:Admin.Base", "method:Admin.Base.initialize",
             "method:Admin.Base.save", "method:Admin.Base.validate", "method:Admin.Base.to_h",
             "method:Admin.Base.persist")
        .calls("method:Admin.Base.save", base, "method:Admin.Base.validate", "self-member")
        .calls("method:Admin.Base.save", base, "method:Admin.Base.persist", "self-member")
        .calls("method:Admin.Base.persist", base, "method:Admin.store", "classmethod")
        .calls("method:Admin.Base.persist", base, "method:Admin.Base.to_h", "self-member"))

    (_code(repo, user, _src(r"""
# frozen_string_literal: true

require_relative "base"

module Admin
  class User < Base
    EMAIL = /\A[^@\s]+@[^@\s]+\z/

    def validate
      errors << "email is invalid" unless EMAIL.match?(attributes[:email].to_s)
      errors << "name is required" if attributes[:name].to_s.strip.empty?
    end

    def display_name
      "#{attributes[:name]} <#{attributes[:email]}>"
    end
  end
end
"""))
        .sym("module:Admin", "class:Admin.User", "method:Admin.User.validate", "method:Admin.User.display_name")
        .imports(base, cat="require")
        .inherits("class:Admin.User", base, "class:Admin.Base")
        .overrides("method:Admin.User.validate", base, "method:Admin.Base.validate"))

    (_code(repo, exporter, _src("""
# frozen_string_literal: true

require "csv"
require_relative "user"

module Admin
  class Exporter
    HEADERS = %w[name email].freeze

    def initialize(users)
      @users = users
    end

    def to_csv
      CSV.generate do |csv|
        csv << HEADERS
        valid_users.each { |user| csv << user.to_h.values_at(:name, :email) }
      end
    end

    def valid_users
      @users.select(&:save)
    end

    def self.export(rows)
      new(rows.map { |row| User.new(row) }).to_csv
    end
  end
end
"""))
        .sym("module:Admin", "class:Admin.Exporter", "method:Admin.Exporter.initialize",
             "method:Admin.Exporter.to_csv", "method:Admin.Exporter.valid_users", "method:Admin.Exporter.export")
        .imports(user, cat="require")
        .calls("method:Admin.Exporter.to_csv", exporter, "method:Admin.Exporter.valid_users", "self-member")
        .calls("method:Admin.Exporter.export", user, "class:Admin.User", "ctor")
        .calls("method:Admin.Exporter.export", exporter, "class:Admin.Exporter", "ctor")
        .calls("method:Admin.Exporter.export", exporter, "method:Admin.Exporter.to_csv", "chained"))

    (_code(repo, f"{root}/spec/admin/user_spec.rb", _src("""
# frozen_string_literal: true

require_relative "../../lib/admin/user"

RSpec.describe Admin::User do
  it "rejects a malformed email" do
    user = described_class.new(name: "Ada", email: "ada")
    expect(user.save).to be(false)
    expect(user.errors).to include("email is invalid")
  end

  it "saves a valid user" do
    user = Admin::User.new(name: "Ada", email: "ada@acme.test")
    expect(user.save).to be(true)
  end
end
"""), role="test")
        .imports(user, cat="require")
        .calls(None, user, "class:Admin.User", "ctor")
        .calls(None, base, "method:Admin.Base.save", "inherited-member"))


# -------------------------------------------------------------------- PHP

def _php(repo: Repo) -> None:
    root = f"{SVC}/legacy-php"
    ns_decl = "module:Acme\\Webhook"
    base, signer = f"{root}/src/Webhook/BaseHandler.php", f"{root}/src/Webhook/Signer.php"
    handler = f"{root}/src/Webhook/Handler.php"

    _meta(repo, f"{root}/composer.json", _src(r"""
{
    "name": "acme/legacy-webhooks",
    "type": "project",
    "require": {
        "php": ">=8.1"
    },
    "require-dev": {
        "phpunit/phpunit": "^10.5"
    },
    "autoload": {
        "psr-4": {
            "Acme\\Webhook\\": "src/Webhook/"
        }
    },
    "autoload-dev": {
        "psr-4": {
            "Acme\\Webhook\\Tests\\": "tests/"
        }
    }
}
"""))

    (_code(repo, base, _src("""
<?php

declare(strict_types=1);

namespace Acme\\Webhook;

abstract class BaseHandler
{
    public function __construct(protected Signer $signer)
    {
    }

    public function process(string $payload, string $signature): array
    {
        if (!$this->signer->verify($payload, $signature)) {
            return ['status' => 401, 'body' => 'invalid signature'];
        }
        $event = json_decode($payload, true, 512, JSON_THROW_ON_ERROR);
        return $this->handle($event);
    }

    abstract protected function handle(array $event): array;
}
"""))
        .sym(ns_decl, "class:BaseHandler", "method:BaseHandler.__construct",
             "method:BaseHandler.process", "method:BaseHandler.handle")
        .calls("method:BaseHandler.process", signer, "method:Signer.verify", "attr-member")
        .calls("method:BaseHandler.process", base, "method:BaseHandler.handle", "self-member"))

    (_code(repo, signer, _src("""
<?php

declare(strict_types=1);

namespace Acme\\Webhook;

final class Signer
{
    public function __construct(private string $secret)
    {
    }

    public function sign(string $payload): string
    {
        return hash_hmac('sha256', $payload, $this->secret);
    }

    public function verify(string $payload, string $signature): bool
    {
        return hash_equals($this->sign($payload), $signature);
    }
}
"""))
        .sym(ns_decl, "class:Signer", "method:Signer.__construct",
             "method:Signer.sign", "method:Signer.verify")
        .calls("method:Signer.verify", signer, "method:Signer.sign", "self-member"))

    (_code(repo, handler, _src("""
<?php

declare(strict_types=1);

namespace Acme\\Webhook;

final class Handler extends BaseHandler
{
    private array $processed = [];

    protected function handle(array $event): array
    {
        $type = $event['type'] ?? 'unknown';
        if (isset($this->processed[$event['id'] ?? ''])) {
            return ['status' => 200, 'body' => 'duplicate'];
        }
        $this->processed[$event['id'] ?? ''] = $type;
        return ['status' => 202, 'body' => $this->describe($type)];
    }

    private function describe(string $type): string
    {
        return sprintf('accepted %s', $type);
    }

    public static function fromEnv(): self
    {
        return new self(new Signer(getenv('WEBHOOK_SECRET') ?: 'dev-only'));
    }
}
"""))
        .sym(ns_decl, "class:Handler", "method:Handler.handle",
             "method:Handler.describe", "method:Handler.fromEnv")
        .inherits("class:Handler", base, "class:BaseHandler")
        .overrides("method:Handler.handle", base, "method:BaseHandler.handle")
        .calls("method:Handler.handle", handler, "method:Handler.describe", "self-member")
        .calls("method:Handler.fromEnv", handler, "class:Handler", "ctor")
        .calls("method:Handler.fromEnv", signer, "class:Signer", "ctor"))

    tns_decl = "module:Acme\\Webhook\\Tests"
    (_code(repo, f"{root}/tests/WebhookTest.php", _src("""
<?php

declare(strict_types=1);

namespace Acme\\Webhook\\Tests;

use Acme\\Webhook\\Handler;
use Acme\\Webhook\\Signer;
use PHPUnit\\Framework\\TestCase;

final class WebhookTest extends TestCase
{
    public function testRejectsBadSignature(): void
    {
        $handler = new Handler(new Signer('secret'));
        $result = $handler->process('{"id":"1","type":"order.paid"}', 'bad');
        $this->assertSame(401, $result['status']);
    }

    public function testAcceptsSignedPayload(): void
    {
        $signer = new Signer('secret');
        $payload = '{"id":"2","type":"order.paid"}';
        $result = (new Handler($signer))->process($payload, $signer->sign($payload));
        $this->assertSame(202, $result['status']);
    }
}
"""), role="test", tags=("P-3", "P-6"))
        .sym(tns_decl, "class:WebhookTest", "method:WebhookTest.testRejectsBadSignature",
             "method:WebhookTest.testAcceptsSignedPayload")
        .imports(handler, "class:Handler", cat="use")
        .imports(signer, "class:Signer", cat="use")
        .calls("method:WebhookTest.testRejectsBadSignature", handler, "class:Handler", "ctor")
        .calls("method:WebhookTest.testRejectsBadSignature", signer, "class:Signer", "ctor")
        .calls("method:WebhookTest.testRejectsBadSignature", base, "method:BaseHandler.process",
               "inherited-member")
        .calls("method:WebhookTest.testAcceptsSignedPayload", signer, "class:Signer", "ctor")
        .calls("method:WebhookTest.testAcceptsSignedPayload", handler, "class:Handler", "ctor")
        .calls("method:WebhookTest.testAcceptsSignedPayload", base, "method:BaseHandler.process",
               "chained")
        .calls("method:WebhookTest.testAcceptsSignedPayload", signer, "method:Signer.sign",
               "typed-member"))


# ------------------------------------------------------------------ Swift

def _swift(repo: Repo) -> None:
    root = f"{SVC}/ios"
    cart, store = f"{root}/Sources/Cart/Cart.swift", f"{root}/Sources/Cart/CartStore.swift"
    ext = f"{root}/Sources/Cart/Extensions.swift"

    repo.add(f"{root}/Package.swift", _src("""
// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Cart",
    products: [.library(name: "Cart", targets: ["Cart"])],
    targets: [
        .target(name: "Cart"),
        .testTarget(name: "CartTests", dependencies: ["Cart"]),
    ]
)
"""), role="build")

    (_code(repo, cart, _src("""
import Foundation

public protocol CartPricing {
    func total() -> Decimal
}

public struct LineItem: Equatable {
    public let sku: String
    public let unitPrice: Decimal
    public var quantity: Int

    public init(sku: String, unitPrice: Decimal, quantity: Int = 1) {
        self.sku = sku
        self.unitPrice = unitPrice
        self.quantity = quantity
    }

    public func subtotal() -> Decimal {
        unitPrice * Decimal(quantity)
    }
}

public struct Cart: CartPricing {
    public private(set) var items: [LineItem] = []

    public init() {}

    public mutating func add(_ item: LineItem) {
        if let index = items.firstIndex(where: { $0.sku == item.sku }) {
            items[index].quantity += item.quantity
        } else {
            items.append(item)
        }
    }

    public func total() -> Decimal {
        items.reduce(Decimal(0)) { $0 + $1.subtotal() }
    }
}
"""))
        .sym("trait:CartPricing", "method:CartPricing.total", "struct:LineItem", "method:LineItem.init",
             "method:LineItem.subtotal", "struct:Cart", "method:Cart.init", "method:Cart.add",
             "method:Cart.total")
        .inherits("struct:Cart", cart, "trait:CartPricing", rel="IMPLEMENTS")
        .overrides("method:Cart.total", cart, "method:CartPricing.total")
        .calls("method:Cart.total", cart, "method:LineItem.subtotal", "typed-member"))

    (_code(repo, store, _src("""
import Foundation

public final class CartStore {
    private(set) var cart: Cart
    private let storageKey: String

    public init(storageKey: String = "acme.cart") {
        self.storageKey = storageKey
        self.cart = Cart()
    }

    public func add(sku: String, price: Decimal) {
        cart.add(LineItem(sku: sku, unitPrice: price))
    }

    public func checkoutTotal() -> Decimal {
        cart.total()
    }
}
"""))
        .sym("class:CartStore", "method:CartStore.init", "method:CartStore.add", "method:CartStore.checkoutTotal")
        .calls("method:CartStore.init", cart, "struct:Cart", "ctor")
        .calls("method:CartStore.add", cart, "method:Cart.add", "attr-member")
        .calls("method:CartStore.add", cart, "struct:LineItem", "ctor")
        .calls("method:CartStore.checkoutTotal", cart, "method:Cart.total", "attr-member"))

    (_code(repo, ext, _src("""
import Foundation

extension Cart {
    public var isEmpty: Bool {
        items.isEmpty
    }

    public func formattedTotal(locale: Locale = Locale(identifier: "en_US")) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.locale = locale
        return formatter.string(from: total() as NSDecimalNumber) ?? "-"
    }
}

extension CartStore {
    public func summary() -> String {
        "\\(cart.items.count) items, \\(cart.formattedTotal())"
    }
}
"""))
        .sym("method:Cart.formattedTotal", "method:CartStore.summary")
        .calls("method:Cart.formattedTotal", cart, "method:Cart.total", "self-member")
        .calls("method:CartStore.summary", ext, "method:Cart.formattedTotal", "attr-member"))

    (_code(repo, f"{root}/Tests/CartTests/CartTests.swift", _src("""
import XCTest
@testable import Cart

final class CartTests: XCTestCase {
    func testAddingSameSkuMergesQuantity() {
        var cart = Cart()
        cart.add(LineItem(sku: "A1", unitPrice: 2))
        cart.add(LineItem(sku: "A1", unitPrice: 2, quantity: 2))
        XCTAssertEqual(cart.items.count, 1)
        XCTAssertEqual(cart.total(), 6)
    }

    func testStoreSummary() {
        let store = CartStore()
        store.add(sku: "B2", price: 5)
        XCTAssertEqual(store.checkoutTotal(), 5)
    }
}
"""), role="test", tags=("P-3", "P-6"))
        .sym("class:CartTests", "method:CartTests.testAddingSameSkuMergesQuantity",
             "method:CartTests.testStoreSummary")
        .calls("method:CartTests.testAddingSameSkuMergesQuantity", cart, "struct:Cart", "ctor")
        .calls("method:CartTests.testAddingSameSkuMergesQuantity", cart, "struct:LineItem", "ctor")
        .calls("method:CartTests.testAddingSameSkuMergesQuantity", cart, "method:Cart.add", "typed-member")
        .calls("method:CartTests.testAddingSameSkuMergesQuantity", cart, "method:Cart.total", "typed-member")
        .calls("method:CartTests.testStoreSummary", store, "class:CartStore", "ctor")
        .calls("method:CartTests.testStoreSummary", store, "method:CartStore.add", "typed-member")
        .calls("method:CartTests.testStoreSummary", store, "method:CartStore.checkoutTotal", "typed-member"))


# ------------------------------------------------------------------- Dart

def _dart(repo: Repo) -> None:
    root = f"{SVC}/mobile"
    cart, repository, main = f"{root}/lib/cart.dart", f"{root}/lib/cart_repository.dart", f"{root}/lib/main.dart"

    _meta(repo, f"{root}/pubspec.yaml", _src("""
name: acme_mobile
description: Acme shop mobile cart.
version: 2.1.0
environment:
  sdk: ">=3.3.0 <4.0.0"
dependencies:
  http: ^1.2.1
  json_annotation: ^4.9.0
dev_dependencies:
  build_runner: ^2.4.9
  json_serializable: ^6.8.0
  test: ^1.25.2
"""))

    (_code(repo, cart, _src("""
import 'package:json_annotation/json_annotation.dart';

part 'cart.g.dart';

@JsonSerializable()
class CartLine {
  CartLine({required this.sku, required this.quantity, required this.unitCents});

  factory CartLine.fromJson(Map<String, dynamic> json) => _$CartLineFromJson(json);

  final String sku;
  final int quantity;
  final int unitCents;

  int get totalCents => quantity * unitCents;

  Map<String, dynamic> toJson() => _$CartLineToJson(this);
}

abstract class CartSource {
  Future<List<CartLine>> fetch(String userId);
}
"""))
        .sym("class:CartLine", "method:CartLine.CartLine", "method:CartLine.fromJson", "method:CartLine.toJson",
             "class:CartSource", "method:CartSource.fetch"))

    _skip(repo, f"{root}/lib/cart.g.dart", _src("""
// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'cart.dart';

CartLine _$CartLineFromJson(Map<String, dynamic> json) => CartLine(
      sku: json['sku'] as String,
      quantity: (json['quantity'] as num).toInt(),
      unitCents: (json['unitCents'] as num).toInt(),
    );

Map<String, dynamic> _$CartLineToJson(CartLine instance) => <String, dynamic>{
      'sku': instance.sku,
      'quantity': instance.quantity,
      'unitCents': instance.unitCents,
    };
"""), role="generated")

    (_code(repo, repository, _src("""
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'cart.dart';

class HttpCartSource extends CartSource {
  HttpCartSource(this.baseUrl, {http.Client? client}) : _client = client ?? http.Client();

  final Uri baseUrl;
  final http.Client _client;

  @override
  Future<List<CartLine>> fetch(String userId) async {
    final response = await _client.get(baseUrl.resolve('/carts/$userId'));
    if (response.statusCode != 200) {
      throw StateError('cart fetch failed: ${response.statusCode}');
    }
    final body = jsonDecode(response.body) as List<dynamic>;
    return body.map((row) => CartLine.fromJson(row as Map<String, dynamic>)).toList();
  }
}

class CartRepository {
  CartRepository(this._source);

  final CartSource _source;
  final Map<String, List<CartLine>> _cache = {};

  Future<List<CartLine>> linesFor(String userId) async {
    return _cache[userId] ??= await _source.fetch(userId);
  }

  Future<int> totalCents(String userId) async {
    final lines = await linesFor(userId);
    return lines.fold<int>(0, (sum, line) => sum + line.totalCents);
  }
}
"""))
        .sym("class:HttpCartSource", "method:HttpCartSource.HttpCartSource", "method:HttpCartSource.fetch",
             "class:CartRepository", "method:CartRepository.CartRepository", "method:CartRepository.linesFor",
             "method:CartRepository.totalCents")
        .imports(cart, "class:CartLine", "class:CartSource")
        .inherits("class:HttpCartSource", cart, "class:CartSource", rel="IMPLEMENTS")
        .overrides("method:HttpCartSource.fetch", cart, "method:CartSource.fetch")
        .calls("method:HttpCartSource.fetch", cart, "method:CartLine.fromJson", "classmethod")
        .calls("method:CartRepository.linesFor", cart, "method:CartSource.fetch", "interface-dispatch")
        .calls("method:CartRepository.totalCents", repository, "method:CartRepository.linesFor",
               "self-member"))

    (_code(repo, main, _src("""
import 'cart.dart';
import 'cart_repository.dart';

Future<void> main() async {
  final repository = CartRepository(HttpCartSource(Uri.parse('https://api.acme.test')));
  final total = await repository.totalCents('user-1');
  final line = CartLine(sku: 'A1', quantity: 2, unitCents: 450);
  print('cart total: $total cents, sample line: ${line.toJson()}');
}
"""))
        .sym("function:main")
        .imports(cart, "class:CartLine")
        .imports(repository, "class:CartRepository", "class:HttpCartSource")
        .calls("function:main", repository, "class:CartRepository", "ctor")
        .calls("function:main", repository, "class:HttpCartSource", "ctor")
        .calls("function:main", repository, "method:CartRepository.totalCents", "typed-member")
        .calls("function:main", cart, "class:CartLine", "ctor")
        .calls("function:main", cart, "method:CartLine.toJson", "typed-member"))

    (_code(repo, f"{root}/test/cart_test.dart", _src("""
import 'package:acme_mobile/cart.dart';
import 'package:test/test.dart';

void main() {
  test('totalCents multiplies quantity by unit price', () {
    final line = CartLine(sku: 'A1', quantity: 3, unitCents: 250);
    expect(line.totalCents, 750);
  });

  test('json round trip keeps the sku', () {
    final line = CartLine(sku: 'B2', quantity: 1, unitCents: 99);
    expect(CartLine.fromJson(line.toJson()).sku, 'B2');
  });
}
"""), role="test", tags=("P-3", "P-6"))
        .sym("function:main")
        .imports(cart, "class:CartLine", cat="package")
        .calls("function:main", cart, "class:CartLine", "ctor")
        .calls("function:main", cart, "method:CartLine.fromJson", "classmethod")
        .calls("function:main", cart, "method:CartLine.toJson", "typed-member"))


# -------------------------------------------------------------------- Lua

def _lua(repo: Repo) -> None:
    root = f"{SVC}/nginx-lua"
    util, rate, init = f"{root}/plugins/util.lua", f"{root}/plugins/rate_limit.lua", f"{root}/plugins/init.lua"

    (_code(repo, util, _src("""
local M = {}

function M.client_key(headers, remote_addr)
  local forwarded = headers["x-forwarded-for"]
  if forwarded and forwarded ~= "" then
    return M.trim(forwarded:match("^[^,]+"))
  end
  return remote_addr
end

function M.trim(value)
  return (value:gsub("^%s+", ""):gsub("%s+$", ""))
end

function M.now()
  return ngx and ngx.now() or os.time()
end

return M
"""))
        .sym("function:M.client_key", "function:M.trim", "function:M.now")
        .calls("function:M.client_key", util, "function:M.trim", "namespace"))

    (_code(repo, rate, _src("""
local util = require("plugins.util")

local M = {}

local buckets = {}

local function refill(bucket, rate, burst, now)
  local elapsed = now - bucket.updated
  bucket.tokens = math.min(burst, bucket.tokens + elapsed * rate)
  bucket.updated = now
end

function M.check(key, rate, burst)
  local now = util.now()
  local bucket = buckets[key]
  if not bucket then
    bucket = { tokens = burst, updated = now }
    buckets[key] = bucket
  end
  refill(bucket, rate, burst, now)
  if bucket.tokens < 1 then
    return false
  end
  bucket.tokens = bucket.tokens - 1
  return true
end

function M.reset()
  buckets = {}
end

return M
"""))
        .sym("function:refill", "function:M.check", "function:M.reset")
        .imports(util, cat="require")
        .calls("function:M.check", util, "function:M.now", "namespace")
        .calls("function:M.check", rate, "function:refill"))

    (_code(repo, init, _src("""
local rate_limit = require("plugins.rate_limit")
local util = require("plugins.util")

local RATE = tonumber(os.getenv("RATE_LIMIT_PER_SEC") or "10")
local BURST = tonumber(os.getenv("RATE_LIMIT_BURST") or "20")

local function access()
  local key = util.client_key(ngx.req.get_headers(), ngx.var.remote_addr)
  if not rate_limit.check(key, RATE, BURST) then
    return ngx.exit(429)
  end
end

return {
  access = access,
}
"""))
        .sym("function:access")
        .imports(rate, cat="require")
        .imports(util, cat="require")
        .calls("function:access", util, "function:M.client_key", "namespace")
        .calls("function:access", rate, "function:M.check", "namespace"))

    (_code(repo, f"{root}/spec/rate_limit_spec.lua", _src("""
local rate_limit = require("plugins.rate_limit")

describe("rate_limit.check", function()
  before_each(function()
    rate_limit.reset()
  end)

  it("allows a burst and then throttles", function()
    for _ = 1, 3 do
      assert.is_true(rate_limit.check("client-a", 0, 3))
    end
    assert.is_false(rate_limit.check("client-a", 0, 3))
  end)
end)
"""), role="test")
        .imports(rate, cat="require")
        .calls(None, rate, "function:M.reset", "namespace")
        .calls(None, rate, "function:M.check", "namespace"))
