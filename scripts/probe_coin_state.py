from network.accounting_state_loader import load_machine_accounting_state_pure
state = load_machine_accounting_state_pure(r"\\10.0.0.90\c$\Goldclub")
for k in sorted(state):
    kl = k.lower()
    if any(x in kl for x in ["coin", "hopper", "drop", "cur"]):
        print(k, "=", state[k])
