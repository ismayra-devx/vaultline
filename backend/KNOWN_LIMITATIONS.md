# Vaultline Known Limitations

This document outlines the known architectural boundaries and operational constraints of the Vaultline MVP.

---

### 1. SQLite Single-Worker Concurrency
- **Architecture**: Vaultline uses SQLite configured in **WAL mode** (`PRAGMA journal_mode = WAL`) with busy timeout handling (`PRAGMA busy_timeout = 5000`).
- **Limitation**: While WAL mode permits multiple concurrent readers alongside a writer, SQLite enforces a single writer process at any instant at the filesystem level.
- **Production Guidance**: For high-throughput environments with multi-node horizontally scaled instances, SQLite should be swapped for an external ACID-compliant RDBMS such as PostgreSQL with explicit row-level locking (`SELECT ... FOR UPDATE`).

---

### 2. Audit Trail: Tamper-Evident, Not Immutable
- **Architecture**: Audit records form a cryptographic SHA-256 hash chain (`prev_hash` bound to the SHA-256 hash of the canonical serialized record and previous hash). The `GET /api/merchant/audit` endpoint runs `verify_audit_chain()` to validate chain integrity.
- **Limitation**: The audit log is **tamper-evident**, not immutable. Any alteration of an audit record’s payload or timestamps invalidates the hash chain and is immediately flagged by the verification algorithm. However, an attacker with direct, unrestricted write access to the SQLite file or underlying disk could theoretically recompute the entire hash chain from the point of alteration forward.
- **Production Guidance**: For compliance regimes requiring non-repudiation and true immutability, stream audit events to append-only WORM storage (e.g. AWS S3 Object Lock, GCP Cloud Storage Bucket Lock with Retention Policy) or a distributed ledger.

---

### 3. MVP Scope
- **Currency**: Limited to `USD` only. Multi-currency support is omitted by design for the MVP.
- **Action Type**: Limited to `refund`. Authorizations, captures, voids, payouts, and recurring billing are intentionally not supported in this gateway.
- **Adapter Isolation**: Real money-moving calls require the production PayPal adapter (provided separately by the platform integration team). The included `FakePaypalAdapter` provides full test coverage with configurable idempotency and failure simulation.

