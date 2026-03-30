from flask import Flask, request, jsonify, render_template, redirect, url_for
from datetime import datetime, timedelta
from pathlib import Path
import os
import secrets
import sqlite3
import string

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
DB_FILE = Path(os.environ.get("DB_FILE", str(BASE_DIR / "licenses.db")))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "180808")

app = Flask(__name__, template_folder=str(TEMPLATES_DIR))


def get_conn():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            device_id TEXT,
            activated_at TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def now_utc():
    return datetime.utcnow()


def make_key(prefix="LIC"):
    chars = string.ascii_uppercase + string.digits
    groups = ["".join(secrets.choice(chars) for _ in range(4)) for _ in range(4)]
    return f"{prefix}-" + "-".join(groups)


def calc_expiration(plan):
    if plan == "daily":
        return now_utc() + timedelta(days=1)
    if plan == "weekly":
        return now_utc() + timedelta(days=7)
    if plan == "monthly":
        return now_utc() + timedelta(days=30)
    if plan == "test_1m":
        return now_utc() + timedelta(minutes=1)
    if plan == "test_5m":
        return now_utc() + timedelta(minutes=5)
    if plan == "test_10m":
        return now_utc() + timedelta(minutes=10)
    if plan == "lifetime":
        return None
    return None


def row_to_dict(row):
    return {
        "id": row["id"],
        "license_key": row["license_key"],
        "plan": row["plan"],
        "status": row["status"],
        "device_id": row["device_id"],
        "activated_at": row["activated_at"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
    }


def plano_valido(plan):
    return plan in (
        "daily",
        "weekly",
        "monthly",
        "test_1m",
        "test_5m",
        "test_10m",
        "lifetime",
    )


def atualizar_expiradas():
    conn = get_conn()
    cur = conn.cursor()
    agora = now_utc().isoformat()
    cur.execute(
        """
        UPDATE licenses
        SET status = 'expired'
        WHERE status = 'active'
          AND expires_at IS NOT NULL
          AND expires_at <> ''
          AND expires_at <= ?
        """,
        (agora,),
    )
    conn.commit()
    conn.close()


@app.get("/")
def home():
    return jsonify({"ok": True, "service": "newbind-license-server", "admin": "/admin"})


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.post("/activate")
def activate():
    atualizar_expiradas()
    data = request.get_json(force=True)
    license_key = (data.get("key") or "").strip()

    if not license_key:
        return jsonify({"ok": False, "error": "key é obrigatória"}), 400

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM licenses WHERE license_key = ?", (license_key,))
    lic = cur.fetchone()

    if not lic:
        conn.close()
        return jsonify({"ok": False, "error": "key inválida"}), 404

    if lic["status"] == "blocked":
        conn.close()
        return jsonify({"ok": False, "error": "licença bloqueada"}), 403

    if lic["status"] == "expired":
        conn.close()
        return jsonify({"ok": False, "error": "licença expirada"}), 403

    activated_at = lic["activated_at"]
    expires_at = lic["expires_at"]
    plan = lic["plan"]

    if not activated_at:
        activated_at_dt = now_utc()
        expires_at_dt = calc_expiration(plan)
        cur.execute(
            """
            UPDATE licenses
            SET activated_at = ?, expires_at = ?, status = 'active'
            WHERE license_key = ?
            """,
            (
                activated_at_dt.isoformat(),
                expires_at_dt.isoformat() if expires_at_dt else None,
                license_key,
            ),
        )
        conn.commit()
        activated_at = activated_at_dt.isoformat()
        expires_at = expires_at_dt.isoformat() if expires_at_dt else None

    conn.close()
    return jsonify(
        {
            "ok": True,
            "status": "active",
            "activated_at": activated_at,
            "expires_at": expires_at,
            "plan": plan,
        }
    )


@app.post("/check")
@app.post("/validate")
def validate():
    atualizar_expiradas()
    data = request.get_json(force=True)
    license_key = (data.get("key") or "").strip()

    if not license_key:
        return jsonify({"ok": False, "error": "key é obrigatória"}), 400

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM licenses WHERE license_key = ?", (license_key,))
    lic = cur.fetchone()
    conn.close()

    if not lic:
        return jsonify({"ok": False, "error": "key inválida"}), 404

    if lic["status"] == "blocked":
        return jsonify({"ok": False, "error": "licença bloqueada"}), 403

    if lic["status"] == "expired":
        return jsonify({"ok": False, "error": "licença expirada"}), 403

    return jsonify(
        {
            "ok": True,
            "status": lic["status"],
            "activated_at": lic["activated_at"],
            "expires_at": lic["expires_at"],
            "plan": lic["plan"],
        }
    )


@app.route("/admin", methods=["GET", "POST"])
def admin_page():
    atualizar_expiradas()
    error = None

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        action = request.form.get("action", "").strip()
        plan = request.form.get("plan", "").strip()
        key = request.form.get("key", "").strip()

        if password != ADMIN_PASSWORD:
            error = "Senha incorreta"
        else:
            conn = get_conn()
            cur = conn.cursor()

            if action == "create" and plano_valido(plan):
                new_key = make_key()
                cur.execute(
                    """
                    INSERT INTO licenses (license_key, plan, status, device_id, activated_at, expires_at, created_at)
                    VALUES (?, ?, 'active', NULL, NULL, NULL, ?)
                    """,
                    (new_key, plan, now_utc().isoformat()),
                )
                conn.commit()
            elif action == "block":
                cur.execute("UPDATE licenses SET status = 'blocked' WHERE license_key = ?", (key,))
                conn.commit()
            elif action == "unblock":
                cur.execute("UPDATE licenses SET status = 'active' WHERE license_key = ?", (key,))
                conn.commit()
            elif action == "reset":
                cur.execute(
                    """
                    UPDATE licenses
                    SET activated_at = NULL, expires_at = NULL, status = 'active'
                    WHERE license_key = ?
                    """,
                    (key,),
                )
                conn.commit()
            elif action == "delete":
                cur.execute("DELETE FROM licenses WHERE license_key = ?", (key,))
                conn.commit()

            conn.close()
            return redirect(url_for("admin_page"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY id DESC")
    licenses = [row_to_dict(row) for row in cur.fetchall()]
    conn.close()

    return render_template("admin.html", licenses=licenses, error=error)


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
