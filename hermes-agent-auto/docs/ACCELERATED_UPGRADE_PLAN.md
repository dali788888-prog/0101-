# Hermes OS Accelerated Upgrade Plan

## Upgrade Mode

Starting from v16.9, the project should stop advancing only by small single-feature increments. Use release trains instead:

- **Foundation Train**: risk, audit, lifecycle, reports, UI wiring
- **Trading Readiness Train**: signal-to-ticket, risk engine, manual handoff, post-trade review
- **Operations Train**: scheduler, daily reports, financial summaries, operator tasks
- **Commercial Train**: customer ops, proof center, release gate, finance, compliance records

## Hard Safety Boundary

The system must not implement autonomous live order submission. All real trading remains:

1. Human reviewed
2. Human approved
3. Kill Switch controlled
4. Manual exchange-side execution
5. External order reference recorded after execution
6. Audited in local records

No API Secret storage, no withdrawal, no autonomous buy/sell, no bypassing approvals.

## Immediate Batch Target

Instead of one step per version, bundle the next work as follows:

### v16.9-v17.0 Batch

- Trade readiness lifecycle timeline
- Ticket review report
- Post-execution review record
- Risk dashboard summary
- Homepage status wiring
- Operator report sync

### v17.1-v17.3 Batch

- Strategy simulation ledger
- Paper trading PnL ledger
- Signal accuracy scoring
- False-positive tracking
- Review-to-strategy feedback loop

### v17.4-v17.6 Batch

- Portfolio exposure dashboard
- Exchange account checklist dashboard
- Daily risk budget ledger
- Manual trade journal
- Weekly strategy review

### v18.0 Milestone

Commercial-grade operations console:

- Full lifecycle from signal to review
- Manual handoff workflow
- Risk engine
- Paper/live-separate ledgers
- Operator task/reports
- Audit trails
- Deployment readiness checks

## Execution Rule

Each accelerated batch should modify backend, UI, routing, and version health status together. Avoid isolated UI-only upgrades unless they unlock a concrete workflow.
