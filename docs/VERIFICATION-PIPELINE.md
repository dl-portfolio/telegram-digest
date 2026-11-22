# Verification Pipeline - Quick Reference

> **Note:** the shell scripts referenced here (`docs/manual-test-epic*.sh`, `/tmp/*.sh`) were local development helpers and are not part of this repository; the pytest commands are.

## When to Run This

Run this verification pipeline **before committing** any of these changes:
- Database schema changes
- API endpoint modifications
- Core business logic updates
- Dependency upgrades
- Docker configuration changes
- Model field changes (especially pgvector fields)

---

## Quick Verification (5 minutes)

```bash
cd telegram-digest

# 1. Ensure containers are running
docker-compose up -d

# 2. Run integration tests
docker-compose exec backend pytest tests/integration/ -v

# 3. Manual API verification
bash /tmp/manual-verification.sh
```

**Expected Results:**
- ✅ 15+ integration tests pass
- ✅ Authentication works
- ✅ CRUD operations work
- ✅ All endpoints return 200/201/204

---

## Full Verification (10 minutes)

**Use this for major changes or before releases.**

### Step 1: Integration Tests
```bash
docker-compose exec backend pytest tests/integration/ -v
```
Expected: All tests pass

### Step 2: Prepare Real Data
```bash
# Note: Requires Telegram authentication and OpenAI API key
bash docs/manual-test-epic2.sh
```

Wait for:
- Channel creation (immediate)
- Scraping completion (~30 seconds)
- Embedding generation (~90 seconds)
- Deduplication (~45 seconds)

### Step 3: Verify Similarity Detection
```bash
bash /tmp/show-real-similarity-with-text.sh
```

Expected output:
```
PRIMARY MESSAGE:
Text: Трамп признал Иерусалим...

SIMILAR MESSAGES DETECTED:
🔍 Similarity Score: 1.0
💬 Text: Карикатура на Трампа...

✅ SEMANTIC SIMILARITY WORKING ON REAL DATA!
```

### Step 4: Full Pipeline Test
```bash
docker-compose exec backend pytest tests/integration/test_epic2_full_pipeline.py -v -s
```

Expected: All 7 pipeline steps pass

---

## Troubleshooting

### Tests Fail: "Incorrect email or password"
```bash
docker-compose up -d prestart
sleep 5
# Retry tests
```

### Tests Pass But Manual Verification Fails
```bash
docker-compose restart backend
sleep 5
bash /tmp/manual-verification.sh
```

### "No deduplicated messages found"
```bash
# Run the full pipeline to generate data
bash docs/manual-test-epic2.sh
# Wait for completion, then verify
bash /tmp/show-real-similarity-with-text.sh
```

---

## One-Command Full Check

```bash
# Run everything in sequence
docker-compose up -d && \
docker-compose exec backend pytest tests/integration/ -v && \
bash /tmp/manual-verification.sh && \
echo "✅ All verifications passed!"
```

---

## CI/CD Integration

### Pre-Commit Hook
Add to `.git/hooks/pre-commit`:
```bash
#!/bin/bash
docker-compose exec -T backend pytest tests/integration/ -v
if [ $? -ne 0 ]; then
    echo "❌ Integration tests failed"
    exit 1
fi
echo "✅ Integration tests passed"
```

### GitHub Actions (Example)
```yaml
- name: Run Integration Tests
  run: |
    docker-compose up -d
    docker-compose exec -T backend pytest tests/integration/ -v
```

---

## Success Checklist

Before committing major changes, verify:

- [ ] All integration tests pass
- [ ] Manual verification script passes
- [ ] No 500 errors in docker logs
- [ ] API endpoints return expected data
- [ ] Database operations persist
- [ ] (For dedup changes) Similarity detection works on real data

---

## Emergency: "Everything is Broken"

### Full Reset
```bash
# Stop everything
docker-compose down

# Remove all data
docker-compose down -v

# Start fresh
docker-compose up -d

# Wait for initialization
sleep 10

# Run tests
docker-compose exec backend pytest tests/integration/ -v
```

This should restore a working state. If tests still fail, there's a code issue.
