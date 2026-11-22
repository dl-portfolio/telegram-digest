# Security Policy

## Reporting Security Issues

If you find a vulnerability, please report it privately through GitHub's "Report a vulnerability" button on the repository's Security tab rather than opening a public issue.

## Security Best Practices

### Environment Variables

**Never commit these files:**
- `.env` (contains secrets)
- `*.session` (Telegram session files)
- `.venv/` (Python virtual environment)
- `__pycache__/` (Python bytecode)
- `postgres-data/` (database files)

**Always use `.env.example` as a template.**

### Required Changes Before Production

1. **Generate secure keys:**
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **Change these values in `.env`:**
   - `SECRET_KEY` - JWT signing key
   - `POSTGRES_PASSWORD` - Database password
   - `FIRST_SUPERUSER_PASSWORD` - Admin password

3. **Get API credentials:**
   - Telegram: [my.telegram.org/apps](https://my.telegram.org/apps)
   - OpenAI: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)

### Default Credentials (CHANGE THESE!)

The application ships with default credentials for local development:

- **Admin Email:** `admin@example.com`
- **Admin Password:** `changethis`
- **Database Password:** `changethis`
- **Secret Key:** `changethis`

⚠️ **These MUST be changed before deployment to any non-local environment.**

### Credential Storage

- Store secrets in `.env` file (git-ignored)
- Use environment variables in production (Docker secrets, Kubernetes secrets, etc.)
- Never hardcode credentials in source code
- Never commit API keys to version control

### What's Safe to Commit

✅ **Safe:**
- `.env.example` (template with placeholders)
- Configuration files without secrets
- Database schemas
- Test fixtures with mock data
- Documentation

❌ **Never commit:**
- `.env` (actual credentials)
- `*.session` (Telegram authentication)
- API keys or tokens
- Passwords or secret keys
- Database dumps with real data

### Session Files

Telegram session files (`*.session`) contain authentication tokens and must be protected:

1. Never commit to version control (`.gitignore` includes `*.session`)
2. Store in secure location with restricted permissions
3. Rotate sessions periodically
4. Delete sessions when no longer needed

### Database Security

- Use strong passwords for PostgreSQL
- Restrict database access to localhost in development
- Use connection pooling with authentication
- Enable SSL/TLS for database connections in production
- Regular backups with encryption

### API Security

The application includes:
- JWT authentication for the channel, message and deduplication endpoints
- Superuser-only access to the deduplication job and stats endpoints (`/api/v1/deduplication/embeddings/generate`, `/jobs`, `/stats`), which act on every user's data and spend OpenAI credit
- Superuser-only access to the Telegram login endpoints (`/api/v1/telegram/*`), which drive the server's single shared Telegram session
- Password hashing with bcrypt
- CORS configuration
- Rate limiting (via Telegram API)
- Input validation with Pydantic

### Docker Security

- Don't run containers as root (already configured)
- Use specific image versions (not `latest`)
- Scan images for vulnerabilities
- Keep base images updated
- Use Docker secrets for production

### Monitoring

Enable monitoring for:
- Failed authentication attempts
- API rate limit hits
- Database connection errors
- Unusual scraping patterns
- Error rates and exceptions

## Verification Checklist

Before deploying or sharing:

- [ ] `.env` file is not committed
- [ ] `.env.example` has no real credentials
- [ ] All default passwords changed
- [ ] Secure random keys generated
- [ ] API keys configured in environment
- [ ] `.gitignore` properly configured
- [ ] Session files not in repository
- [ ] Database files not committed
- [ ] Logs don't contain sensitive data

## Git Safety Check

Run before committing:

```bash
# Check what will be committed
git status

# Verify .gitignore is working
git check-ignore -v .env telegram_sessions/*.session

# Search for potential secrets (should return nothing sensitive)
git grep -i "api_key\|secret\|password" -- ':!.env.example' ':!SECURITY.md'
```

## Additional Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Docker Security Best Practices](https://docs.docker.com/develop/security-best-practices/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
