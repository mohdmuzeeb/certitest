"""30 seeded faults for the S2 ledger, as exact (old, new) source replacements.

Each fault violates at least one requirement and is validated by
``scripts/validate_s2.py``: the hidden oracle suite passes on the reference and
fails on every faulty variant. Fault IDs name the primary violated requirement.
"""

FAULTS = {
    "F01-R1-gbp-missing": ('CURRENCIES = ("USD", "EUR", "GBP")', 'CURRENCIES = ("USD", "EUR")'),
    "F02-R2-dup-overwrites": ("        if account_id in self._used_ids:\n            raise AccountExists(account_id)\n", ""),
    "F03-R3-len-off-by-one": ("len(account_id) > 20", "len(account_id) >= 20"),
    "F04-R4-zero-deposit": ("if type(amount) is not int or amount <= 0:", "if type(amount) is not int or amount < 0:"),
    "F05-R7-exact-balance": ('        if amount > acct["balance"]:\n            raise InsufficientFunds(account_id)',
                             '        if amount >= acct["balance"]:\n            raise InsufficientFunds(account_id)'),
    "F06-R7-debit-before-check": ('        if amount > acct["balance"]:\n            raise InsufficientFunds(account_id)\n        self._check_limit(account_id, amount)\n        acct["balance"] -= amount\n',
                                  '        self._check_limit(account_id, amount)\n        acct["balance"] -= amount\n        if acct["balance"] < 0:\n            raise InsufficientFunds(account_id)\n'),
    "F07-R8-ids-from-zero": ("self._next_transfer = 1", "self._next_transfer = 0"),
    "F08-R9-no-currency-check": ('        if s["currency"] != d["currency"]:\n            raise CurrencyMismatch(f"{s[\'currency\']} != {d[\'currency\']}")\n', ""),
    "F09-R10-self-transfer": ('        if src == dst:\n            raise ValueError("source and destination must differ")\n', ""),
    "F10-R11-nonatomic": ('        if amount > s["balance"]:\n            raise InsufficientFunds(src)\n        self._check_limit(src, amount)\n        s["balance"] -= amount\n',
                          '        self._check_limit(src, amount)\n        s["balance"] -= amount\n        if s["balance"] < 0:\n            raise InsufficientFunds(src)\n'),
    "F11-R12-replay-moves-money": ("            return tid\n        if src == dst:", "            pass\n        if src == dst:"),
    "F12-R12-replay-new-id": ("                raise IdempotencyConflict(idempotency_key)\n            return tid\n",
                              "                raise IdempotencyConflict(idempotency_key)\n            tid = f\"T{self._next_transfer}\"\n            self._next_transfer += 1\n            return tid\n"),
    "F13-R13-conflict-ignored": ("            if prev != params:\n                raise IdempotencyConflict(idempotency_key)\n", ""),
    "F14-R14-exact-limit-rejected": ("if used + amount > DAILY_LIMIT:", "if used + amount >= DAILY_LIMIT:"),
    "F15-R14-withdraw-not-counted": ('        acct["balance"] -= amount\n        self._record_outgoing(account_id, amount)\n', '        acct["balance"] -= amount\n'),
    "F16-R14-no-daily-reset": ("    def set_day(self, day):\n        self._day = day\n", "    def set_day(self, day):\n        pass\n"),
    "F17-R15-frozen-blocks-deposit": ('        self._check_amount(amount)\n        acct["balance"] += amount\n',
                                      '        self._check_amount(amount)\n        if account_id in self._frozen:\n            raise AccountFrozen(account_id)\n        acct["balance"] += amount\n'),
    "F18-R15-frozen-transfer-out": ("        if src in self._frozen:\n            raise AccountFrozen(src)\n", ""),
    "F19-R16-unfreeze-noop": ("        self._get(account_id)\n        self._frozen.discard(account_id)\n\n    def set_day", "        self._get(account_id)\n\n    def set_day"),
    "F20-R17-close-nonempty": ('        if acct["balance"] != 0:\n            raise AccountNotEmpty(account_id)\n', ""),
    "F21-R17-id-reusable": ("        del self._accounts[account_id]\n", "        del self._accounts[account_id]\n        self._used_ids.discard(account_id)\n"),
    "F22-R5-closed-still-open": ("        del self._accounts[account_id]\n", "        self._accounts[account_id]['closed'] = True\n"),
    "F23-R19-no-counterparty": ('self._history[src].append(("transfer_out", amount, dst))', 'self._history[src].append(("transfer_out", amount, None))'),
    "F24-R19-records-failed": ('        self._check_amount(amount)\n        if account_id in self._frozen:\n            raise AccountFrozen(account_id)\n        if amount > acct["balance"]:\n            raise InsufficientFunds(account_id)\n',
                               '        self._check_amount(amount)\n        if account_id in self._frozen:\n            raise AccountFrozen(account_id)\n        if amount > acct["balance"]:\n            self._history[account_id].append(("withdrawal", amount, None))\n            raise InsufficientFunds(account_id)\n'),
    "F25-R19-reversed": ("        return list(self._history[account_id])", "        return list(reversed(self._history[account_id]))"),
    "F26-R20-excludes-frozen": ('return sum(a["balance"] for a in self._accounts.values() if a["currency"] == currency)',
                                'return sum(a["balance"] for k, a in self._accounts.items() if a["currency"] == currency and k not in self._frozen)'),
    "F27-R20-ignores-currency": ('return sum(a["balance"] for a in self._accounts.values() if a["currency"] == currency)', 'return sum(a["balance"] for a in self._accounts.values())'),
    "F28-R5-deposit-autocreates": ('    def deposit(self, account_id, amount):\n        acct = self._get(account_id)\n',
                                   '    def deposit(self, account_id, amount):\n        if account_id not in self._accounts:\n            self._accounts[account_id] = {"currency": "USD", "balance": 0}\n            self._history[account_id] = []\n        acct = self._get(account_id)\n'),
    "F29-R6-negative-withdraw": ('    def withdraw(self, account_id, amount):\n        acct = self._get(account_id)\n        self._check_amount(amount)\n',
                                '    def withdraw(self, account_id, amount):\n        acct = self._get(account_id)\n        if type(amount) is not int or amount == 0:\n            raise InvalidAmount(amount)\n'),
    "F30-R14-incoming-counted": ('        self._record_outgoing(src, amount)\n', '        self._record_outgoing(src, amount)\n        self._record_outgoing(dst, amount)\n'),
}


def apply(source: str, fault_id: str) -> str:
    old, new = FAULTS[fault_id]
    if source.count(old) != 1:
        raise ValueError(f"{fault_id}: anchor occurs {source.count(old)} times")
    return source.replace(old, new)
