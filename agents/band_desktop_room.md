# BAND Desktop: Pocketpay Dark Factory Room Guide 🏭

This guide explains how to set up and run the **Pocketpay Dark Factory** multi-agent team inside **BAND Desktop** or on **[app.band.ai](https://app.band.ai)** for the **WeAreDevelopers x BAND Hackathon**.

---

## 👥 The 4-Agent Band

| Agent Handle | Display Name | Role | Primary Responsibility |
| :--- | :--- | :--- | :--- |
| `@pocketpay-architect` | **Architect Agent** | System Architect | Governs the 4 invariants: $\sum \Delta = 0$, balance $\ge 0$, deadlock-free lock ordering, and idempotency. |
| `@pocketpay-builder` | **Builder Agent** | Core Implementer | Implements SQLite WAL schema, transactional ledger engine, and FastAPI REST endpoints. |
| `@pocketpay-fuzzer` | **Chaos Fuzzer** | Adversarial Stress Tester | Launches 50-thread overdraft races, deadlock gauntlet, idempotency stampedes, and PhonePe traffic replay. |
| `@pocketpay-auditor` | **Auditor Agent** | Mathematical Auditor | Reconciles stored vs calculated balances, verifies 0 discrepancies, and issues the Certificate. |

---

## 🚀 Setting Up the Room in BAND Desktop

1. Open **BAND Desktop** (or go to [app.band.ai](https://app.band.ai)).
2. Go to **Chats / Rooms** $\rightarrow$ Click **+ New Room**.
3. Name the room: `Pocketpay Dark Factory`.
4. Add the 4 agents to the room:
   * `@pocketpay-architect`
   * `@pocketpay-builder`
   * `@pocketpay-fuzzer`
   * `@pocketpay-auditor`
5. In the message box, send the kick-off trigger:
   ```text
   @pocketpay-architect Initialize the Dark Factory for Pocketpay. Enforce invariants and direct the factory to build and certify the ledger.
   ```

---

## ⚡ Autonomous Execution Flow (Lights Out)

```
[Human Kickoff: "@pocketpay-architect start"]
                     │
                     ▼
       ┌───────────────────────────┐
       │   @pocketpay-architect    │
       │   Issues 4 Invariants     │
       └─────────────┬─────────────┘
                     │ Directs Builder
                     ▼
       ┌───────────────────────────┐
       │     @pocketpay-builder    │
       │     Runs 15 Unit Tests    │
       │     Reports Readiness     │
       └─────────────┬─────────────┘
                     │ Hands off to Fuzzer
                     ▼
       ┌───────────────────────────┐
       │     @pocketpay-fuzzer     │
       │  50-Worker Overdraft Race │
       │  50-Worker Deadlock Race  │
       │  PhonePe Traffic Replay   │
       └─────────────┬─────────────┘
                     │ Hands off to Auditor
                     ▼
       ┌───────────────────────────┐
       │     @pocketpay-auditor    │
       │  Reconciles All Entries   │
       │  Checks Sum == 0          │
       │  Issues SHA-256 Cert      │
       └───────────────────────────┘
```

---

## 📜 Running Locally with the Autonomous Orchestrator

You can run the full multi-agent room locally with real live Gemini intelligence at any time:

```bash
python agents/orchestrator.py
```

This runs the exact conversation, executes the unit tests, unleashes the 50-thread chaos attacks, verifies the ledger, and writes the complete room transcript to:
* `agents/dark_factory_room_transcript.md`
* `agents/dark_factory_room_transcript.json`

The generated transcript and SHA-256 certificate can be attached directly to your hackathon submission on Lablab.ai!
