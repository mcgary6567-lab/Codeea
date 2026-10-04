"""Deployment targets: the health probe, the container/compose/platform files, the shell and PowerShell
installers, the deploy_secure fingerprint and the SCHEDULER_ENABLED switch (docs/DEPLOYMENT.md, section 14)."""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import deploy_secure
from app.config import BASE_DIR, settings
from app.models.core import User, UserSession

SHELL_SCRIPTS = ["install.sh", "upgrade.sh", "deploy/entrypoint.sh", "deploy/backup.sh", "deploy/smoke.sh"]


# --------------------------------------------------------------------------- health
def test_health_reports_commit_key(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "commit" in body
    assert "database" not in body, "the default probe must stay cheap (no database round-trip)"


def test_health_commit_from_platform_variables(client, monkeypatch):
    for var in ("GIT_COMMIT", "RENDER_GIT_COMMIT", "RAILWAY_GIT_COMMIT_SHA", "SOURCE_COMMIT", "FLY_GIT_COMMIT"):
        monkeypatch.delenv(var, raising=False)
    assert client.get("/health").json()["commit"] is None
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "0123456789abcdef")
    assert client.get("/health").json()["commit"] == "0123456"
    monkeypatch.setenv("GIT_COMMIT", "fedcba9876543210")   # the image build arg wins
    assert client.get("/health").json()["commit"] == "fedcba9"


def test_health_db_probe(client):
    r = client.get("/health?db=1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] == "ok"


# --------------------------------------------------------------------------- platform files
def _read(rel: str) -> str:
    return (BASE_DIR / rel).read_text(encoding="utf-8")


def test_docker_compose_file():
    raw = _read("docker-compose.yml")
    try:
        import yaml
    except ImportError:  # minimal check without PyYAML
        for needle in ("services:", "db:", "app:", "volumes:", "storage:/app/storage", "pgdata:"):
            assert needle in raw, f"docker-compose.yml lacks {needle!r}"
        return
    doc = yaml.safe_load(raw)
    assert {"db", "app", "caddy", "backup", "jobs"} <= set(doc["services"])
    assert {"pgdata", "storage", "backups"} <= set(doc["volumes"])
    app_svc = doc["services"]["app"]
    assert "storage:/app/storage" in app_svc["volumes"], "the volume must mount at <repo>/storage"
    assert app_svc["environment"]["WEB_CONCURRENCY"] == "${WEB_CONCURRENCY:-1}"
    assert doc["services"]["jobs"]["environment"]["SCHEDULER_ENABLED"] == "true"
    for rel in ("deploy/Caddyfile", "deploy/backup.sh"):
        assert (BASE_DIR / rel).is_file(), f"compose bind-mounts {rel}, which is missing"


def test_dockerfile_references_existing_paths():
    df = _read("Dockerfile")
    assert "python:3.12-slim" in df
    assert 'ENTRYPOINT ["/app/deploy/entrypoint.sh"]' in df
    for rel in ("deploy/entrypoint.sh", "deploy/db_ready.py", "deploy/smoke.sh", "deploy/backup.sh", "alembic.ini", "requirements.txt"):
        assert (BASE_DIR / rel).is_file(), rel
    entry = _read("deploy/entrypoint.sh")
    assert "python deploy/db_ready.py wait" in entry
    assert "python -m alembic upgrade head" in entry
    assert "exec uvicorn app.main:app" in entry
    assert "python deploy_secure.py" in entry
    attrs = _read(".gitattributes")
    assert "*.sh" in attrs and "eol=lf" in attrs, ".gitattributes must pin LF endings for the shell scripts"


def test_fly_and_railway_files_load():
    fly = tomllib.loads(_read("fly.toml"))
    assert fly["build"]["dockerfile"] == "Dockerfile"
    assert fly["env"]["WEB_CONCURRENCY"] == "1"
    assert fly["mounts"][0]["destination"] == "/app/storage"
    assert fly["http_service"]["checks"][0]["path"] == "/health"
    railway = json.loads(_read("railway.json"))
    assert railway["build"]["builder"] == "DOCKERFILE"
    assert railway["deploy"]["numReplicas"] == 1
    assert railway["deploy"]["healthcheckPath"] == "/health"


def test_render_blueprint_fields():
    raw = _read("render.yaml")
    assert "branch: online-quran-college-app" in raw
    assert "key: BASE_URL" in raw and "key: ADMIN_EMAIL" in raw
    try:
        import yaml
    except ImportError:
        return
    doc = yaml.safe_load(raw)
    env = {e["key"]: e for e in doc["services"][0]["envVars"]}
    assert env["ADMIN_EMAIL"].get("sync") is False
    assert env["BASE_URL"]["value"].startswith("https://")


def _requirement_names(rel: str) -> set[str]:
    names = set()
    for line in _read(rel).splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(line.split(";")[0].split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower())
    return names


def test_requirements_split():
    prod = _requirement_names("requirements.txt")
    dev = _requirement_names("requirements-dev.txt")
    assert "pytest" not in prod and "pytest" in dev
    for pkg in ("fastapi", "uvicorn", "sqlalchemy", "reportlab", "openpyxl", "alembic", "psycopg"):
        assert pkg in prod, f"{pkg} is imported by the app and must stay in requirements.txt"


# --------------------------------------------------------------------------- scripts parse
@pytest.mark.parametrize("rel", SHELL_SCRIPTS)
def test_shell_scripts_parse(rel):
    assert (BASE_DIR / rel).is_file(), rel
    # read_text() would normalise CRLF away; the shebang line only survives in a container when the bytes are LF
    assert b"\r\n" not in (BASE_DIR / rel).read_bytes(), f"{rel} must keep LF line endings (see .gitattributes)"
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not installed")
    r = subprocess.run([bash, "-n", str(BASE_DIR / rel)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"bash -n {rel}: {r.stderr}"


def test_install_ps1_parses():
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        pytest.skip("PowerShell not installed")
    cmd = ("$null = [System.Management.Automation.Language.Parser]::ParseFile('install.ps1', [ref]$null, [ref]$err); "
           "exit $err.Count")
    r = subprocess.run([ps, "-NoProfile", "-Command", cmd], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"install.ps1 has {r.returncode} parse error(s): {r.stdout} {r.stderr}"


# --------------------------------------------------------------------------- deploy_secure fingerprint
def test_deploy_secure_applies_only_when_credentials_change(db, monkeypatch):
    """Two runs with the same values: the first rotates, the second is a no-op that revokes nothing.

    Everything happens inside the test session, which conftest rolls back, so the development
    database keeps its seed passwords.
    """
    monkeypatch.setenv("ADMIN_PASSWORD", "Rotate@12345")
    monkeypatch.setenv("DEMO_PASSWORD", "Demo@12345")
    monkeypatch.setenv("ADMIN_EMAIL", "")
    admin = db.query(User).filter(User.email == "admin@oqc.local").first()
    assert admin is not None
    before_hash = admin.hashed_password

    first = deploy_secure.apply(db, "Rotate@12345", "Demo@12345", "")
    assert first["applied"] is True and first["superusers"] >= 1
    assert admin.hashed_password != before_hash and admin.must_change_password is True
    marker = deploy_secure.stored_fingerprint(db)
    assert marker and marker["fingerprint"] == deploy_secure.fingerprint("Rotate@12345", "Demo@12345", "")
    assert db.query(UserSession).filter(UserSession.revoked.is_(False)).count() == 0

    # A session opened after the rotation must survive a redeploy with unchanged values.
    db.add(UserSession(user_id=admin.id, token_jti="deploy-targets-test", expires_at=datetime.utcnow() + timedelta(hours=1)))
    db.flush()
    hash_after_first = admin.hashed_password

    second = deploy_secure.apply(db, "Rotate@12345", "Demo@12345", "")
    assert second["applied"] is False and second["reason"] == "unchanged"
    assert second["applied_at"] == first["applied_at"]
    assert admin.hashed_password == hash_after_first
    assert db.query(UserSession).filter(UserSession.token_jti == "deploy-targets-test", UserSession.revoked.is_(False)).count() == 1

    # ROTATE_CREDENTIALS=true (force) and a changed value both apply again.
    third = deploy_secure.apply(db, "Rotate@12345", "Demo@12345", "", force=True)
    assert third["applied"] is True and third["sessions_revoked"] >= 1
    fourth = deploy_secure.apply(db, "Another@12345", "Demo@12345", "")
    assert fourth["applied"] is True
    assert deploy_secure.stored_fingerprint(db)["fingerprint"] == deploy_secure.fingerprint("Another@12345", "Demo@12345", "")


def test_deploy_secure_without_credentials_is_a_noop(db):
    assert deploy_secure.apply(db, "", "", "") == {"applied": False, "reason": "not configured"}


def test_fingerprint_is_order_sensitive():
    assert deploy_secure.fingerprint("ab", "c", "") != deploy_secure.fingerprint("a", "bc", "")
    assert deploy_secure.fingerprint("x", "y", "z") == deploy_secure.fingerprint("x", "y", "z")


# --------------------------------------------------------------------------- scheduler switch
def test_scheduler_not_started_when_disabled(monkeypatch):
    import app.main as main_module

    calls: list[str] = []
    monkeypatch.setattr(main_module, "start_scheduler", lambda: calls.append("start"))
    monkeypatch.setattr(main_module, "stop_scheduler", lambda: calls.append("stop"))
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)
    with TestClient(main_module.app) as c:
        assert c.get("/health").status_code == 200
    assert calls == [], f"scheduler touched although SCHEDULER_ENABLED=false: {calls}"

    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    with TestClient(main_module.app):
        pass
    assert calls == ["start", "stop"]
