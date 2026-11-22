# Testing Strategy - Telegram Digest

> **Note on scripts:** the manual validation scripts mentioned below (`docs/manual-test-epic*.sh`, `/tmp/manual-verification.sh`, `/tmp/show-real-similarity-with-text.sh`) were local helpers used during development and are **not included in this repository**. The pytest suites under `backend/tests/` are. CI (`.github/workflows/test-backend.yml`) runs the unit tests except `tests/integration/` (needs the running stack) and `tests/services/test_embeddings.py` (calls the live OpenAI API).

## Overview

This document defines our testing approach to ensure the application **actually works**, not just that tests pass.

**Critical Lesson Learned:** We had 112/114 unit tests passing while the Docker application was completely broken. This testing strategy prevents that from happening again.

---

## Testing Pyramid

```
                    ┌─────────────────┐
                    │   E2E Tests     │  ← Prove real functionality
                    │  (Integration)  │
                    ├─────────────────┤
                    │  Integration    │  ← Test in Docker
                    │     Tests       │
                    ├─────────────────┤
                    │   Unit Tests    │  ← Test components
                    │                 │
                    └─────────────────┘
```

### 1. Unit Tests (Fast, Many)
- **Location:** `backend/tests/`
- **Purpose:** Test individual functions and classes in isolation
- **Run:** `docker-compose exec backend pytest tests/api tests/crud tests/services`
- **Coverage:** Aim for 80%+ code coverage
- **Limitation:** ⚠️ **Can pass even when app is broken**

### 2. Integration Tests (Medium Speed, Focused)
- **Location:** `backend/tests/integration/`
- **Purpose:** Test complete workflows against running Docker environment
- **Run:** `docker-compose exec backend pytest tests/integration/ -v`
- **What They Test:**
  - Real API endpoints in Docker
  - Actual database operations (pgvector)
  - User authentication flows
  - Business logic enforcement (channel limits, duplicates)
- **Key Insight:** ✅ **These prove the app actually works**

### 3. End-to-End Verification (Slow, Comprehensive)
- **Location:** `docs/manual-test-epic*.sh`, `/tmp/show-real-similarity-with-text.sh`
- **Purpose:** Validate complete user workflows with real data
- **Run:** Manual execution of validation scripts
- **What They Prove:**
  - Full scraping → embedding → deduplication pipeline
  - Real Telegram channel integration
  - OpenAI API integration
  - Semantic similarity detection on actual messages

---

## Test Types and When to Use Them

### Integration Tests (Primary Quality Gate)

**Epic 1: Channel Management**
```bash
docker-compose exec backend pytest tests/integration/test_epic1_channel_flow.py -v
```

Tests:
- ✅ Create channel and verify in database
- ✅ Enforce 3-channel limit
- ✅ Prevent duplicate channels
- ✅ Update/delete operations
- ✅ Authentication required

**Epic 2: Deduplication Pipeline**
```bash
docker-compose exec backend pytest tests/integration/test_epic2_deduplication_flow.py -v
```

Tests:
- ✅ Deduplication stats endpoint
- ✅ Messages pagination and filtering
- ✅ Deduplicated messages endpoint
- ✅ Job creation
- ✅ Error handling

**Epic 2: Full Pipeline Validation**
```bash
docker-compose exec backend pytest tests/integration/test_epic2_full_pipeline.py -v -s
```

Tests (requires real data):
- ✅ Channels exist
- ✅ Messages scraped
- ✅ Embeddings generated
- ✅ Deduplication ran
- ✅ API returns results
- ✅ Duplicate details accessible

---

## Verification Pipeline for Major Changes

**Run this checklist before committing significant changes:**

### Step 1: Run All Integration Tests
```bash
cd telegram-digest
docker-compose up -d
docker-compose exec backend pytest tests/integration/ -v
```

**Expected:** All tests pass (15+ tests)

### Step 2: Manual API Verification
```bash
bash /tmp/manual-verification.sh
```

**Expected:**
- ✅ Authentication works
- ✅ Channel CRUD works
- ✅ Dedup endpoints accessible

### Step 3: Full Pipeline Test (Optional, for major changes)

**3a. Set up test data:**
```bash
bash docs/manual-test-epic2.sh
# Let it run through scraping and embedding generation
# This takes ~3-5 minutes
```

**3b. Verify similarity detection:**
```bash
bash /tmp/show-real-similarity-with-text.sh
```

**Expected:**
- Primary message displayed
- 2+ similar messages found
- Similarity scores shown (0.70-1.0 range)
- Message text displayed for all

### Step 4: Run Full Pipeline Test
```bash
docker-compose exec backend pytest tests/integration/test_epic2_full_pipeline.py -v -s
```

**Expected:** All 7 steps pass

---

## What Good Test Output Looks Like

### Integration Tests
```
tests/integration/test_epic1_channel_flow.py::TestEpic1ChannelManagement::test_create_channel_full_flow PASSED
tests/integration/test_epic1_channel_flow.py::TestEpic1ChannelManagement::test_channel_limit_enforcement PASSED
...
======================== 6 passed in 0.36s =========================
```

### Similarity Detection
```
═══════════════════════════════════════════════════════
SEMANTIC SIMILARITY DETECTION - REAL TELEGRAM DATA
═══════════════════════════════════════════════════════

PRIMARY MESSAGE:
Text: Трамп признал Иерусалим столицей Израиля...

SIMILAR MESSAGES DETECTED:
🔍 Similarity Score: 1.0
💬 Text: Карикатура на Трампа объявившего Иерусалим...

🔍 Similarity Score: 0.812
💬 Text: Страны предупредившие Трампа не объявлять...

✅ SEMANTIC SIMILARITY WORKING ON REAL DATA!
```

---

## Test Data Management

### Clean State for Integration Tests
Integration tests automatically clean up before/after:
- Channels are deleted via API before each test
- Tests are isolated and can run in any order

### Real Data for E2E Tests
- Requires actual Telegram channels (ToBeOr_Official, yigal_levin)
- Requires OpenAI API key for embeddings
- Data persists between runs (195 messages, 167 embeddings)

### Resetting Test Environment
```bash
# Stop all containers
docker-compose down

# Remove volumes (DESTRUCTIVE - deletes all data)
docker-compose down -v

# Start fresh
docker-compose up -d
```

---

## CI/CD Integration

### Pre-Commit Hooks (Recommended)
```bash
# Run fast unit tests
docker-compose exec backend pytest tests/api tests/crud -v

# Run integration tests
docker-compose exec backend pytest tests/integration/ -v
```

### Pull Request Checks (Required)
1. All unit tests pass
2. All integration tests pass
3. No API endpoint errors

### Deployment Checks (Critical)
1. Run full verification pipeline
2. Verify similarity detection works
3. Check all API endpoints return 200/201/204

---

## Common Issues and Solutions

### Issue: "Incorrect email or password" in tests
**Cause:** Database was cleared, initial data not created
**Solution:**
```bash
docker-compose up -d prestart
# Wait 5 seconds for it to complete
```

### Issue: Integration tests pass but manual verification fails
**Cause:** Backend container needs restart
**Solution:**
```bash
docker-compose restart backend
sleep 5
```

### Issue: "No deduplicated messages found"
**Cause:** Need to run the full pipeline first
**Solution:**
```bash
bash docs/manual-test-epic2.sh
# Wait for scraping and embeddings
bash /tmp/show-real-similarity-with-text.sh
```

### Issue: Similarity scores showing as 0 or null
**Cause:** Not enough embeddings generated yet
**Solution:** Wait 60-90 seconds for OpenAI API, then rerun deduplication

---

## Test File Organization

```
backend/tests/
├── integration/              # Docker-based integration tests
│   ├── test_epic1_channel_flow.py
│   ├── test_epic2_deduplication_flow.py
│   └── test_epic2_full_pipeline.py
├── api/                      # API endpoint unit tests
├── crud/                     # Database operation tests
├── services/                 # Service layer tests
├── worker/                   # Background task tests
└── fixtures/                 # Mock data for external APIs
    ├── mock_telegram_responses.json
    └── mock_openai_embeddings.json

docs/
├── manual-test-epic1.sh      # Epic 1 validation script
├── manual-test-epic2.sh      # Epic 2 full pipeline
└── TESTING-STRATEGY.md       # This document

/tmp/
├── manual-verification.sh              # Quick API check
└── show-real-similarity-with-text.sh  # Similarity proof
```

---

## Success Criteria

### Application is "Working" When:
1. ✅ All 15+ integration tests pass
2. ✅ Manual verification script passes all checks
3. ✅ Similarity detection shows real similar messages
4. ✅ API endpoints return expected data structures
5. ✅ No 500 errors in logs
6. ✅ Database queries return actual data (not empty results)

### Application is "Broken" When:
- ❌ Tests pass but API returns 500 errors
- ❌ Tests pass but endpoints return empty data
- ❌ Similarity scores are all 0 or null
- ❌ Database operations don't persist
- ❌ Authentication fails

---

## Key Takeaways

1. **Unit tests alone are NOT enough** - They can pass while the app is completely broken
2. **Integration tests are the primary quality gate** - They prove the Docker app works
3. **Manual verification is essential** - Always test major features by hand
4. **Real data proves it works** - Mock data can hide integration issues
5. **Test in the deployment environment** - Docker, not local Python

---

## Version History

- **2026-01-08**: Initial version based on Epic 1 & 2 validation
- Documented the "112 passing tests, broken app" lesson learned
- Established integration tests as primary quality gate
- Created verification pipeline for major changes
