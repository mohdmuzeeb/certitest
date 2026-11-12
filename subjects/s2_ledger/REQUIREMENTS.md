# Ledger service: functional requirements

Module `ledger` exposes class `Ledger` and exceptions `LedgerError` (base), `AccountExists`, `AccountNotFound`, `InvalidAmount`, `InsufficientFunds`, `CurrencyMismatch`, `IdempotencyConflict`, `LimitExceeded`, `AccountFrozen`, `AccountNotEmpty`. All amounts are integers in cents. Every failed operation raises and leaves all state unchanged.

- R1: `open_account(account_id, currency)` creates an account with balance 0. Supported currencies are exactly "USD", "EUR" and "GBP"; any other currency raises `ValueError`.
- R2: Opening an account whose id is already in use raises `AccountExists`.
- R3: An account id must be a non-empty alphanumeric string of at most 20 characters; otherwise `ValueError` is raised.
- R4: `deposit(account_id, amount)` increases the balance by `amount`. The amount must be an `int` greater than 0, otherwise `InvalidAmount` is raised.
- R5: Any operation naming an account that does not exist (never opened, or closed) raises `AccountNotFound`. Deposits never create accounts.
- R6: `withdraw(account_id, amount)` decreases the balance by `amount`; the amount rules of R4 apply.
- R7: A withdrawal larger than the balance raises `InsufficientFunds` and leaves the balance unchanged. Withdrawing exactly the full balance is allowed.
- R8: `transfer(src, dst, amount, idempotency_key)` moves `amount` from `src` to `dst` and returns a transfer id. Transfer ids are "T1", "T2", ... in the order successful transfers are made.
- R9: A transfer between accounts with different currencies raises `CurrencyMismatch`.
- R10: A transfer whose source and destination are the same account raises `ValueError`.
- R11: A transfer larger than the source balance raises `InsufficientFunds`; neither balance changes.
- R12: Repeating `transfer` with an idempotency key already used for the same (src, dst, amount) returns the original transfer id and moves no money.
- R13: Reusing an idempotency key with different (src, dst, amount) raises `IdempotencyConflict`.
- R14: Outgoing money (withdrawals plus outgoing transfers) per account per day must not exceed 1,000,000 cents; an operation that would exceed it raises `LimitExceeded`. Reaching the limit exactly is allowed. Incoming transfers and deposits do not count. `set_day(day)` sets the current day (an int, initially 0); each day has its own limit.
- R15: `freeze(account_id)` makes withdrawals and outgoing transfers from the account raise `AccountFrozen`. Deposits and incoming transfers are still accepted.
- R16: `unfreeze(account_id)` restores normal behaviour.
- R17: `close_account(account_id)` closes an account whose balance is 0; a non-zero balance raises `AccountNotEmpty`. A closed account's id can never be opened again (`AccountExists`).
- R18: `balance(account_id)` returns the current balance as an int.
- R19: `history(account_id)` returns the account's successful operations in chronological order as tuples `(kind, amount, counterparty)` where kind is "deposit", "withdrawal", "transfer_in" or "transfer_out"; counterparty is the other account id for transfers and `None` otherwise.
- R20: `total_balance(currency)` returns the sum of balances of all open accounts (frozen included) in that currency.
