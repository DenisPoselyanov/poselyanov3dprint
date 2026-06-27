"""Спільна конфігурація pytest."""

# Post-deploy smoke-скрипт (python tests/smoke_test.py), не unit-тест.
collect_ignore = ["smoke_test.py"]
