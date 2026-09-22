# Contributing to YRadar

1. Describe the observed Tesla page, model, region, and redacted evidence. Never post a full VIN, order number, address, token, chat ID, cookie, or browser profile.
2. Keep Model Y and Model 3 rules separate. Hardware inference needs a source, confidence (`confirmed`, `likely`, `unknown`), and unsupported-model tests.
3. Preserve unknowns. Missing fees, history, coordinates, or hardware evidence must not become zero, clean, or false.
4. Keep network access out of unit tests. Use redacted or synthetic fixtures and run:

```bash
python -m unittest discover -s tests -v
python monitor.py --demo
```

5. Update English and Chinese docs together for user-visible changes.

Pull requests should explain the trigger, previous and new behavior, evidence boundary, and validation. Changes that increase request frequency, bypass access controls, or include personal data will not be accepted.
