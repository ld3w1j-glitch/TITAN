from __future__ import annotations

import json
import hashlib
import hmac
import math
import os
import re
import secrets
import smtplib
import ssl
import sqlite3
import zipfile
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from html import escape as html_escape
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from flask import (
    Flask, abort, flash, g, redirect, render_template, request,
    send_file, send_from_directory, session, url_for
)
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
VOLUME_DIR = Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")) if os.getenv("RAILWAY_VOLUME_MOUNT_PATH") else None
DB_PATH = Path(os.getenv("DB_PATH", str((VOLUME_DIR / "titan.db") if VOLUME_DIR else (BASE_DIR / "titan.db"))))
UPLOAD_DIR = Path(os.getenv("UPLOAD_PATH", str((VOLUME_DIR / "uploads") if VOLUME_DIR else (BASE_DIR / "uploads"))))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "titan-local-" + secrets.token_hex(16))
app.config.update(
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("RAILWAY_ENVIRONMENT") is not None,
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

ALLOWED_IMAGES = {"png", "jpg", "jpeg", "webp"}
VERIFICATION_TTL_MINUTES = 10
VERIFICATION_MAX_ATTEMPTS = 5
VERIFICATION_RESEND_SECONDS = 60


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.endpoint in {
        "login", "register", "verify_email", "resend_verification",
        "forgot_password", "reset_password", "resend_password_reset",
    }:
        response.headers["Cache-Control"] = "no-store"
    return response


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def password_requirements(password: str, name: str = "", email: str = "") -> dict[str, bool]:
    lowered = password.casefold()
    obvious = ("123456", "abcdef", "qwerty", "senha", "password", "admin", "titan")
    personal_parts = [part.casefold() for part in re.findall(r"[\wÀ-ÿ]+", name) if len(part) >= 3]
    email_name = email.split("@", 1)[0].casefold()
    if len(email_name) >= 3:
        personal_parts.append(email_name)
    return {
        "length": len(password) >= 10,
        "lower": any(char.islower() for char in password),
        "upper": any(char.isupper() for char in password),
        "number": any(char.isdigit() for char in password),
        "symbol": any(not char.isalnum() and not char.isspace() for char in password),
        "not_obvious": (
            not any(fragment in lowered for fragment in obvious)
            and not any(part in lowered for part in personal_parts)
            and len(set(lowered)) >= 6
        ),
    }


def password_errors(password: str, name: str = "", email: str = "") -> list[str]:
    checks = password_requirements(password, name, email)
    labels = {
        "length": "pelo menos 10 caracteres",
        "lower": "uma letra minúscula",
        "upper": "uma letra maiúscula",
        "number": "um número",
        "symbol": "um símbolo",
        "not_obvious": "não usar nome, e-mail ou sequência óbvia",
    }
    return [labels[key] for key, passed in checks.items() if not passed]


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        visible = local[:1] + "*" * max(1, len(local) - 1)
    else:
        visible = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{visible}@{domain}"


def safe_next_url(value: str | None) -> str | None:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return None


def verification_code_hash(user_id: int, code: str) -> str:
    key = str(app.secret_key).encode("utf-8")
    payload = f"{user_id}:{code}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def password_reset_code_hash(user_id: int, code: str) -> str:
    key = str(app.secret_key).encode("utf-8")
    payload = f"password-reset:{user_id}:{code}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def issue_verification_code(db: sqlite3.Connection, user_id: int) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utc_now()
    expires = now + timedelta(minutes=VERIFICATION_TTL_MINUTES)
    db.execute(
        """INSERT INTO email_verifications(user_id,code_hash,expires_at,attempts,last_sent_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash,
           expires_at=excluded.expires_at,attempts=0,last_sent_at=excluded.last_sent_at""",
        (user_id, verification_code_hash(user_id, code), expires.isoformat(), 0, now.isoformat()),
    )
    return code


def issue_password_reset_code(db: sqlite3.Connection, user_id: int) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utc_now()
    expires = now + timedelta(minutes=VERIFICATION_TTL_MINUTES)
    db.execute(
        """INSERT INTO password_resets(user_id,code_hash,expires_at,attempts,last_sent_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash,
           expires_at=excluded.expires_at,attempts=0,last_sent_at=excluded.last_sent_at""",
        (user_id, password_reset_code_hash(user_id, code), expires.isoformat(), 0, now.isoformat()),
    )
    return code


def _send_verification_email(name: str, email: str, code: str, purpose: str = "verify") -> bool:
    is_reset = purpose == "reset"
    default_mode = "smtp" if os.getenv("RAILWAY_ENVIRONMENT") else "console"
    mode = os.getenv("MAIL_MODE", default_mode).strip().lower()
    if mode == "console":
        label = "recuperação de senha" if is_reset else "confirmação de e-mail"
        app.logger.warning("TITAN DEV — código de %s para %s: %s", label, email, code)
        return True
    if mode != "smtp":
        app.logger.error("MAIL_MODE inválido. Use 'smtp' ou 'console'.")
        return False

    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("SMTP_FROM_EMAIL", username).strip()
    from_name = os.getenv("SMTP_FROM_NAME", "Projeto TITAN").strip()
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    default_port = 465 if use_ssl else 587
    try:
        port = int(os.getenv("SMTP_PORT", str(default_port)))
    except ValueError:
        app.logger.error("SMTP_PORT precisa ser um número.")
        return False
    if not host or not from_email:
        app.logger.error("Configuração SMTP incompleta: informe SMTP_HOST e SMTP_FROM_EMAIL.")
        return False

    message = EmailMessage()
    message["Subject"] = (
        f"{code} é seu código para redefinir a senha TITAN"
        if is_reset else f"{code} é seu código de confirmação TITAN"
    )
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = email
    if is_reset:
        message.set_content(
            f"Olá, {name}!\n\nSeu código para redefinir a senha do Projeto TITAN é: {code}\n\n"
            f"Ele expira em {VERIFICATION_TTL_MINUTES} minutos. Se você não solicitou a troca, ignore este e-mail."
        )
        email_title = "Redefina sua senha"
        email_intro = "Use o código abaixo para criar uma nova senha com segurança:"
        email_warning = "Se você não solicitou a troca de senha, ignore esta mensagem e sua senha continuará igual."
    else:
        message.set_content(
            f"Olá, {name}!\n\nSeu código de confirmação do Projeto TITAN é: {code}\n\n"
            f"Ele expira em {VERIFICATION_TTL_MINUTES} minutos. Se você não criou esta conta, ignore este e-mail."
        )
        email_title = "Confirme seu e-mail"
        email_intro = "Use o código abaixo para liberar sua conta:"
        email_warning = "Se você não criou esta conta, ignore esta mensagem."
    safe_name = html_escape(name)
    message.add_alternative(
        f"""<!doctype html><html><body style="margin:0;background:#0b0f15;color:#eef2f6;font-family:Arial,sans-serif">
        <div style="max-width:520px;margin:0 auto;padding:36px 22px">
          <div style="color:#f39a2f;font-size:28px;font-weight:900;letter-spacing:2px">TITAN</div>
          <div style="margin-top:22px;padding:28px;background:#151d27;border:1px solid #303b47;border-radius:16px">
            <h1 style="margin:0 0 12px;font-size:23px">{email_title}</h1>
            <p style="color:#a8b3bf;line-height:1.6">Olá, {safe_name}! {email_intro}</p>
            <div style="margin:22px 0;padding:16px;text-align:center;background:#0c1219;border-radius:12px;color:#ffad4c;font-size:34px;font-weight:900;letter-spacing:9px">{code}</div>
            <p style="color:#a8b3bf;font-size:13px">O código expira em {VERIFICATION_TTL_MINUTES} minutos. {email_warning}</p>
          </div>
        </div></body></html>""",
        subtype="html",
    )

    context = ssl.create_default_context()
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        app.logger.exception("Não foi possível enviar o código de confirmação por SMTP.")
        return False


def send_verification_email(name: str, email: str, code: str) -> bool:
    """Fronteira segura: nenhuma falha do provedor de e-mail derruba o cadastro."""
    try:
        return _send_verification_email(name, email, code)
    except Exception:
        app.logger.exception("Falha inesperada ao preparar ou enviar o e-mail de confirmação.")
        return False


def send_password_reset_email(name: str, email: str, code: str) -> bool:
    """O reset de senha também nunca pode derrubar a aplicação por falha SMTP."""
    try:
        return _send_verification_email(name, email, code, purpose="reset")
    except Exception:
        app.logger.exception("Falha inesperada ao preparar ou enviar o e-mail de recuperação.")
        return False


def db_conn() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_db() -> None:
    with db_conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            email_verified INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS email_verifications(
            user_id INTEGER PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_sent_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS password_resets(
            user_id INTEGER PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_sent_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS settings(
            user_id INTEGER PRIMARY KEY,
            age INTEGER DEFAULT 27,
            height REAL DEFAULT 1.80,
            start_weight REAL DEFAULT 65,
            goal_weight REAL DEFAULT 70,
            final_goal REAL DEFAULT 85,
            calories INTEGER DEFAULT 2800,
            protein INTEGER DEFAULT 130,
            carbs INTEGER DEFAULT 380,
            fat INTEGER DEFAULT 85,
            water REAL DEFAULT 2.5,
            weekly_target REAL DEFAULT 0.30,
            sex TEXT DEFAULT 'male',
            activity_level TEXT DEFAULT 'sedentary',
            goal_type TEXT DEFAULT 'gain',
            training_days INTEGER DEFAULT 0,
            appetite_level TEXT DEFAULT 'low',
            meals_per_day INTEGER DEFAULT 4,
            budget_monthly REAL DEFAULT 0,
            restrictions TEXT DEFAULT '',
            bmr REAL DEFAULT 0,
            tdee REAL DEFAULT 0,
            onboarding_completed INTEGER DEFAULT 0,
            calculation_version TEXT DEFAULT 'TITAN-1.0',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS foods(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            serving REAL NOT NULL DEFAULT 100,
            unit TEXT NOT NULL DEFAULT 'g',
            calories REAL NOT NULL,
            protein REAL NOT NULL DEFAULT 0,
            carbs REAL NOT NULL DEFAULT 0,
            fat REAL NOT NULL DEFAULT 0,
            fiber REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS meals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            meal_type TEXT NOT NULL DEFAULT 'Refeição',
            food_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(food_id) REFERENCES foods(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS weights(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            weight REAL NOT NULL,
            UNIQUE(user_id, day),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS measurements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            arm REAL, chest REAL, waist REAL, abdomen REAL,
            hip REAL, thigh REAL, calf REAL, shoulders REAL,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS habits(
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            water REAL DEFAULT 0,
            sleep REAL DEFAULT 0,
            trained INTEGER DEFAULT 0,
            appetite INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, day),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS photos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            angle TEXT NOT NULL,
            filename TEXT NOT NULL,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS exercises(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            muscle TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            image_filename TEXT DEFAULT '',
            video_url TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS workouts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            exercise_id INTEGER NOT NULL,
            sets INTEGER DEFAULT 3,
            reps INTEGER DEFAULT 10,
            load REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(exercise_id) REFERENCES exercises(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS plan_settings(
            user_id INTEGER PRIMARY KEY,
            days INTEGER DEFAULT 30,
            meals_per_day INTEGER DEFAULT 2,
            completed_days INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plan_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            food_id INTEGER,
            name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'g',
            daily_qty REAL NOT NULL DEFAULT 0,
            package_qty REAL NOT NULL DEFAULT 1,
            package_price REAL NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT 'Marmitas',
            current_stock REAL NOT NULL DEFAULT 0,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(food_id) REFERENCES foods(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS stores(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS store_prices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            store_id INTEGER NOT NULL,
            plan_item_id INTEGER NOT NULL,
            package_price REAL NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, store_id, plan_item_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(store_id) REFERENCES stores(id) ON DELETE CASCADE,
            FOREIGN KEY(plan_item_id) REFERENCES plan_items(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS calendar_meals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            time TEXT NOT NULL,
            title TEXT NOT NULL,
            food_id INTEGER,
            quantity REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(food_id) REFERENCES foods(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            time TEXT NOT NULL,
            days TEXT NOT NULL DEFAULT 'Todos os dias',
            enabled INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)

        # Migração compatível com bancos das versões anteriores.
        existing = {row["name"] for row in db.execute("PRAGMA table_info(settings)").fetchall()}
        additions = {
            "sex": "TEXT DEFAULT 'male'",
            "activity_level": "TEXT DEFAULT 'sedentary'",
            "goal_type": "TEXT DEFAULT 'gain'",
            "training_days": "INTEGER DEFAULT 0",
            "appetite_level": "TEXT DEFAULT 'low'",
            "meals_per_day": "INTEGER DEFAULT 4",
            "budget_monthly": "REAL DEFAULT 0",
            "restrictions": "TEXT DEFAULT ''",
            "bmr": "REAL DEFAULT 0",
            "tdee": "REAL DEFAULT 0",
            "onboarding_completed": "INTEGER DEFAULT 0",
            "calculation_version": "TEXT DEFAULT 'TITAN-1.0'",
        }
        for column, definition in additions.items():
            if column not in existing:
                db.execute(f"ALTER TABLE settings ADD COLUMN {column} {definition}")

        # Contas criadas antes da verificação por e-mail permanecem válidas.
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "email_verified" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")


init_db()


def today() -> str:
    return date.today().isoformat()


def current_user_id() -> int:
    return int(session["user_id"])


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(24)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def load_user_and_check_csrf():
    user_id = session.get("user_id")
    if user_id:
        with db_conn() as db:
            account = db.execute("SELECT id,name,email,email_verified FROM users WHERE id=?", (user_id,)).fetchone()
        if account and account["email_verified"]:
            g.user = account
        else:
            session.pop("user_id", None)
            if account:
                session["pending_user_id"] = account["id"]
            g.user = None
    else:
        g.user = None

    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, "Formulário expirado. Atualize a página e tente novamente.")

    # Antes de liberar os módulos, todo usuário realiza a avaliação inicial.
    if g.user and request.endpoint not in {
        "onboarding", "onboarding_result", "logout", "health", "static"
    }:
        with db_conn() as db:
            profile = db.execute(
                "SELECT onboarding_completed FROM settings WHERE user_id=?",
                (g.user["id"],)
            ).fetchone()
        if profile and not profile["onboarding_completed"]:
            return redirect(url_for("onboarding"))


def seed_user(db: sqlite3.Connection, user_id: int) -> None:
    db.execute("INSERT INTO settings(user_id) VALUES(?)", (user_id,))
    db.execute("INSERT INTO plan_settings(user_id) VALUES(?)", (user_id,))
    foods = [
        ("Arroz cozido",100,"g",130,2.7,28,0.3,1.6),
        ("Feijão cozido",100,"g",76,4.8,13.6,0.5,8.5),
        ("Peito de frango",100,"g",165,31,0,3.6,0),
        ("Ovo inteiro",50,"g",72,6.3,0.4,4.8,0),
        ("Leite integral",200,"ml",122,6.4,9.4,6.6,0),
        ("Banana",100,"g",89,1.1,23,0.3,2.6),
        ("Aveia",40,"g",152,5.1,27,2.8,4.2),
        ("Pasta de amendoim",30,"g",177,7.5,6,15,1.8),
        ("Macarrão cozido",100,"g",157,5.8,30.9,0.9,1.8),
        ("Azeite",10,"ml",88,0,0,10,0),
        ("Pão de forma",50,"g",132,4.5,24.5,1.8,2),
    ]
    db.executemany("""INSERT INTO foods(user_id,name,serving,unit,calories,protein,carbs,fat,fiber)
                      VALUES(?,?,?,?,?,?,?,?,?)""", [(user_id,*f) for f in foods])
    food_ids = {r["name"]: r["id"] for r in db.execute("SELECT id,name FROM foods WHERE user_id=?", (user_id,))}
    plan = [
        (food_ids["Arroz cozido"],"Arroz cru","g",180,5000,32.90,"Marmitas",0,"Rende aproximadamente 500 g cozido por dia"),
        (food_ids["Feijão cozido"],"Feijão cru","g",100,1000,9.99,"Marmitas",0,"Rende aproximadamente 250 a 300 g cozido por dia"),
        (food_ids["Peito de frango"],"Peito de frango cru","g",450,1000,21.90,"Marmitas",0,"Ajuste conforme a perda no preparo"),
        (food_ids["Leite integral"],"Leite integral","ml",1000,1000,5.80,"Café e ceia",0,""),
        (food_ids["Aveia"],"Aveia","g",100,1000,15.00,"Café e lanche",0,""),
        (food_ids["Banana"],"Banana","un",2,12,10.00,"Café e lanche",0,"Preço por dúzia; vínculo nutricional é aproximado"),
        (food_ids["Ovo inteiro"],"Ovos","un",2,30,28.00,"Lanche",0,"Preço por bandeja"),
        (food_ids["Pão de forma"],"Pão de forma","fatia",4,20,9.00,"Lanche",0,"Ajuste o número de fatias do pacote"),
        (food_ids["Pasta de amendoim"],"Pasta de amendoim","g",30,1000,35.00,"Lanche",0,""),
    ]
    db.executemany("""INSERT INTO plan_items(user_id,food_id,name,unit,daily_qty,package_qty,package_price,category,current_stock,notes)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""", [(user_id,*x) for x in plan])
    exercises = [
        ("Agachamento livre","Pernas e glúteos","Mantenha o tronco firme, joelhos acompanhando a direção dos pés e amplitude controlada."),
        ("Supino reto","Peito e tríceps","Escápulas apoiadas, pés firmes e barra descendo com controle até próximo ao peito."),
        ("Remada baixa","Costas e bíceps","Puxe com os cotovelos, evite balançar o tronco e controle a volta."),
        ("Desenvolvimento","Ombros","Mantenha abdômen contraído e evite arquear excessivamente a lombar."),
        ("Levantamento terra romeno","Posterior de coxa","Leve o quadril para trás com coluna neutra e joelhos levemente flexionados."),
        ("Rosca direta","Bíceps","Mantenha os cotovelos próximos ao corpo e evite impulso."),
    ]
    db.executemany("INSERT INTO exercises(user_id,name,muscle,description) VALUES(?,?,?,?)", [(user_id,*x) for x in exercises])


def get_settings(db: sqlite3.Connection, user_id: int):
    return db.execute("SELECT * FROM settings WHERE user_id=?", (user_id,)).fetchone()


def daily_totals(db: sqlite3.Connection, user_id: int, day: str) -> dict:
    row = db.execute("""
        SELECT COALESCE(SUM(f.calories*m.quantity/f.serving),0) calories,
               COALESCE(SUM(f.protein*m.quantity/f.serving),0) protein,
               COALESCE(SUM(f.carbs*m.quantity/f.serving),0) carbs,
               COALESCE(SUM(f.fat*m.quantity/f.serving),0) fat,
               COALESCE(SUM(f.fiber*m.quantity/f.serving),0) fiber
        FROM meals m JOIN foods f ON f.id=m.food_id
        WHERE m.user_id=? AND m.day=?
    """, (user_id, day)).fetchone()
    return dict(row)


def weight_predictions(weights, milestones=(70,75,80,85)) -> tuple[list[dict], float | None]:
    if len(weights) < 2:
        return ([{"goal": g, "text": "Registre pelo menos dois pesos em datas diferentes."} for g in milestones], None)
    first, last = weights[0], weights[-1]
    days = (date.fromisoformat(last["day"]) - date.fromisoformat(first["day"])).days
    if days <= 0:
        return ([{"goal": g, "text": "Ainda não há intervalo suficiente."} for g in milestones], None)
    weekly_rate = (last["weight"] - first["weight"]) / days * 7
    result = []
    for goal in milestones:
        if last["weight"] >= goal:
            result.append({"goal": goal, "text": "Meta já alcançada."})
        elif weekly_rate <= 0.03:
            result.append({"goal": goal, "text": "Sem tendência positiva suficiente para estimar."})
        else:
            weeks = (goal - last["weight"]) / weekly_rate
            target_date = date.fromisoformat(last["day"]) + timedelta(days=round(weeks*7))
            result.append({"goal": goal, "text": f"Estimativa: {target_date.strftime('%d/%m/%Y')} ({weeks:.1f} semanas)"})
    return result, weekly_rate


def automatic_insights(db: sqlite3.Connection, user_id: int, settings, weights) -> list[dict]:
    since = (date.today() - timedelta(days=6)).isoformat()
    nutrition = db.execute("""
      SELECT COUNT(DISTINCT m.day) logged_days,
             COALESCE(SUM(f.calories*m.quantity/f.serving),0) calories,
             COALESCE(SUM(f.protein*m.quantity/f.serving),0) protein
      FROM meals m JOIN foods f ON f.id=m.food_id
      WHERE m.user_id=? AND m.day>=?
    """, (user_id, since)).fetchone()
    habits = db.execute("""SELECT AVG(water) water, AVG(sleep) sleep, AVG(appetite) appetite,
                            SUM(trained) trained, COUNT(*) days
                            FROM habits WHERE user_id=? AND day>=?""", (user_id, since)).fetchone()
    insights = []
    logged = nutrition["logged_days"] or 0
    if logged:
        avg_cal = nutrition["calories"] / logged
        avg_pro = nutrition["protein"] / logged
        if avg_cal < settings["calories"] * .85:
            insights.append({"level":"warn","title":"Calorias abaixo da meta","text":f"Sua média nos dias registrados foi {avg_cal:.0f} kcal. Faltaram cerca de {settings['calories']-avg_cal:.0f} kcal por dia."})
        else:
            insights.append({"level":"good","title":"Boa consistência calórica","text":f"Média de {avg_cal:.0f} kcal nos dias registrados nesta semana."})
        if avg_pro < settings["protein"] * .85:
            insights.append({"level":"warn","title":"Proteína pode melhorar","text":f"Média de {avg_pro:.0f} g; sua meta configurada é {settings['protein']} g."})
        else:
            insights.append({"level":"good","title":"Proteína bem encaminhada","text":f"Média de {avg_pro:.0f} g nos dias registrados."})
    else:
        insights.append({"level":"info","title":"Comece pelo registro","text":"Registre as refeições de alguns dias para o TITAN analisar sua alimentação."})
    if habits and habits["days"]:
        if habits["sleep"] and habits["sleep"] < 7:
            insights.append({"level":"warn","title":"Sono abaixo de 7 horas","text":f"Média registrada de {habits['sleep']:.1f} horas. Recuperação também influencia treino e apetite."})
        if habits["water"] and habits["water"] < settings["water"] * .8:
            insights.append({"level":"info","title":"Hidratação abaixo da meta","text":f"Média de {habits['water']:.1f} L para uma meta de {settings['water']:.1f} L."})
    predictions, rate = weight_predictions(weights)
    if rate is not None:
        if rate > .7:
            insights.append({"level":"warn","title":"Peso subindo rapidamente","text":f"Tendência aproximada de {rate:.2f} kg/semana. Verifique se a evolução está confortável e sustentável."})
        elif rate > .05:
            insights.append({"level":"good","title":"Tendência de ganho detectada","text":f"A tendência entre seus registros é de aproximadamente {rate:.2f} kg/semana."})
    return insights[:5]



def round_step(value: float, step: int = 5) -> int:
    return int(round(value / step) * step)


def first_stage_goal(weight: float, final_goal: float, goal_type: str) -> float:
    if goal_type == "gain":
        checkpoint = math.floor(weight / 5) * 5 + 5
        return min(final_goal, checkpoint)
    if goal_type == "loss":
        checkpoint = math.ceil(weight / 5) * 5 - 5
        return max(final_goal, checkpoint)
    return weight


def calculate_initial_plan(form) -> dict:
    age = int(form["age"])
    height_cm = float(form["height_cm"])
    weight = float(form["weight"])
    sex = form["sex"]
    activity_level = form["activity_level"]
    goal_type = form["goal_type"]
    training_days = int(form["training_days"])
    appetite_level = form["appetite_level"]
    meals_per_day = int(form["meals_per_day"])
    pace = form["pace"]
    budget_monthly = float(form.get("budget_monthly") or 0)
    restrictions = form.get("restrictions", "").strip()

    if not 18 <= age <= 90:
        raise ValueError("O cálculo automático desta versão é destinado a adultos entre 18 e 90 anos.")
    if not 130 <= height_cm <= 230 or not 35 <= weight <= 300:
        raise ValueError("Confira a altura e o peso informados.")
    if sex not in {"male", "female"}:
        raise ValueError("Selecione a referência metabólica.")
    if goal_type not in {"gain", "maintain", "loss"}:
        raise ValueError("Selecione um objetivo válido.")

    activity_factors = {
        "sedentary": 1.20,
        "light": 1.375,
        "moderate": 1.55,
        "high": 1.725,
        "very_high": 1.90,
    }
    factor = activity_factors.get(activity_level, 1.20)
    # Considera também a frequência de treino planejada, sem somar duas vezes:
    # usa o maior fator entre a rotina declarada e a frequência semanal.
    training_factors = {0: 1.20, 1: 1.25, 2: 1.30, 3: 1.375, 4: 1.45, 5: 1.55, 6: 1.65, 7: 1.725}
    factor = max(factor, training_factors.get(training_days, 1.20))

    # Equação de Mifflin-St Jeor para estimar o gasto energético de repouso.
    sex_constant = 5 if sex == "male" else -161
    bmr = 10 * weight + 6.25 * height_cm - 5 * age + sex_constant
    tdee = bmr * factor

    adjustments = {
        "gain": {"slow": 200, "moderate": 300, "fast": 450},
        "maintain": {"slow": 0, "moderate": 0, "fast": 0},
        "loss": {"slow": -300, "moderate": -450, "fast": -650},
    }
    weekly_rates = {
        "gain": {"slow": .20, "moderate": .35, "fast": .50},
        "maintain": {"slow": 0, "moderate": 0, "fast": 0},
        "loss": {"slow": .25, "moderate": .50, "fast": .75},
    }
    adjustment = adjustments[goal_type].get(pace, adjustments[goal_type]["moderate"])
    weekly_target = weekly_rates[goal_type].get(pace, weekly_rates[goal_type]["moderate"])

    # Para apetite baixo, evita começar com um salto excessivo de calorias.
    if goal_type == "gain" and appetite_level == "low":
        adjustment = min(adjustment, 300)
        weekly_target = min(weekly_target, .35)

    calories = max(1200, round_step(tdee + adjustment, 50))
    if goal_type == "gain":
        protein_factor = 1.8 if training_days >= 3 else 1.6
        fat_factor = .9
    elif goal_type == "loss":
        protein_factor = 2.0 if training_days >= 2 else 1.7
        fat_factor = .8
    else:
        protein_factor = 1.6 if training_days >= 2 else 1.4
        fat_factor = .9

    protein = max(70, round_step(weight * protein_factor, 5))
    fat = max(50, round_step(weight * fat_factor, 5))
    carbs = max(80, round_step((calories - protein * 4 - fat * 9) / 4, 5))
    water = round(min(4.5, max(2.0, weight * .035 + (.3 if training_days >= 4 else 0))), 1)

    if goal_type == "maintain":
        final_goal = weight
    else:
        final_goal = float(form["final_goal"])
        if goal_type == "gain" and final_goal <= weight:
            raise ValueError("Para ganhar peso, a meta final precisa ser maior que o peso atual.")
        if goal_type == "loss" and final_goal >= weight:
            raise ValueError("Para reduzir peso, a meta final precisa ser menor que o peso atual.")
        if not 35 <= final_goal <= 300:
            raise ValueError("Confira a meta final informada.")

    goal_weight = first_stage_goal(weight, final_goal, goal_type)
    difference = abs(final_goal - weight)
    estimated_weeks = difference / weekly_target if weekly_target > 0 else 0

    return {
        "age": age,
        "height": height_cm / 100,
        "weight": weight,
        "sex": sex,
        "activity_level": activity_level,
        "goal_type": goal_type,
        "training_days": training_days,
        "appetite_level": appetite_level,
        "meals_per_day": meals_per_day,
        "budget_monthly": budget_monthly,
        "restrictions": restrictions,
        "bmr": round(bmr),
        "tdee": round(tdee),
        "calories": calories,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
        "water": water,
        "weekly_target": weekly_target,
        "goal_weight": goal_weight,
        "final_goal": final_goal,
        "estimated_weeks": estimated_weeks,
        "pace": pace,
    }


def reminder_times(meals_per_day: int) -> list[str]:
    schedules = {
        3: ["08:00", "13:00", "20:00"],
        4: ["07:30", "12:00", "16:00", "20:30"],
        5: ["07:30", "10:30", "13:30", "17:00", "20:30"],
        6: ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"],
    }
    return schedules.get(meals_per_day, schedules[4])


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGES


def save_user_image(file_storage, prefix: str) -> str:
    if not file_storage or not file_storage.filename:
        return ""
    if not allowed_image(file_storage.filename):
        raise ValueError("Formato de imagem não permitido. Use JPG, PNG ou WEBP.")
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    user_folder = UPLOAD_DIR / str(current_user_id())
    user_folder.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.{ext}")
    file_storage.save(user_folder / filename)
    return filename


@app.route("/health")
def health():
    return {"status": "ok", "database": DB_PATH.exists()}


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        password_confirm = request.form.get("password_confirm", "")
        if len(name) < 2 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            flash("Preencha um nome e um e-mail válido.")
            return render_template("register.html", form_name=name, form_email=email)
        errors = password_errors(password, name, email)
        if errors:
            flash("A senha ainda precisa de: " + ", ".join(errors) + ".")
            return render_template("register.html", form_name=name, form_email=email)
        if password != password_confirm:
            flash("A confirmação da senha não corresponde.")
            return render_template("register.html", form_name=name, form_email=email)
        try:
            with db_conn() as db:
                cur = db.execute("INSERT INTO users(name,email,password_hash,created_at,email_verified) VALUES(?,?,?,?,0)",
                                 (name, email, generate_password_hash(password), datetime.now().isoformat(timespec="seconds")))
                user_id = cur.lastrowid
                seed_user(db, user_id)
                code = issue_verification_code(db, user_id)
                db.commit()
        except sqlite3.IntegrityError:
            flash("Este e-mail já está cadastrado.")
            return render_template("register.html", form_name=name, form_email=email)
        except sqlite3.Error:
            app.logger.exception("Falha de banco de dados durante a criação da conta.")
            flash("Não foi possível criar a conta no banco de dados. Tente novamente em instantes.")
            return render_template("register.html", form_name=name, form_email=email)
        except Exception:
            app.logger.exception("Falha inesperada durante a criação da conta.")
            flash("Não foi possível concluir o cadastro. Nenhuma senha foi enviada por e-mail.")
            return render_template("register.html", form_name=name, form_email=email)
        session.clear()
        session["pending_user_id"] = user_id
        csrf_token()
        if send_verification_email(name, email, code):
            flash("Enviamos um código de 6 dígitos para confirmar seu e-mail.")
        else:
            flash("Sua conta foi criada, mas o envio falhou. Verifique a configuração de e-mail e use Reenviar código.")
        return redirect(url_for("verify_email"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        with db_conn() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], request.form["password"]):
            flash("E-mail ou senha incorretos.")
            return render_template("login.html")
        if not user["email_verified"]:
            should_send = False
            with db_conn() as db:
                verification = db.execute(
                    "SELECT * FROM email_verifications WHERE user_id=?", (user["id"],)
                ).fetchone()
                if not verification or parse_utc(verification["expires_at"]) <= utc_now():
                    code = issue_verification_code(db, user["id"])
                    db.commit()
                    should_send = True
            session.clear()
            session["pending_user_id"] = user["id"]
            csrf_token()
            if should_send:
                if send_verification_email(user["name"], user["email"], code):
                    flash("Enviamos um novo código para confirmar seu e-mail.")
                else:
                    flash("Não foi possível enviar o código agora. Tente reenviar em instantes.")
            else:
                flash("Sua conta ainda precisa da confirmação por e-mail.")
            return redirect(url_for("verify_email"))
        session.clear()
        session["user_id"] = user["id"]
        csrf_token()
        return redirect(safe_next_url(request.args.get("next")) or url_for("dashboard"))
    return render_template("login.html")


@app.route("/esqueci-senha", methods=["GET", "POST"])
def forgot_password():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        mail_job = None
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            try:
                with db_conn() as db:
                    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                    if user and user["email_verified"]:
                        reset = db.execute(
                            "SELECT * FROM password_resets WHERE user_id=?", (user["id"],)
                        ).fetchone()
                        can_send = True
                        if reset:
                            elapsed = (utc_now() - parse_utc(reset["last_sent_at"])).total_seconds()
                            can_send = elapsed >= VERIFICATION_RESEND_SECONDS
                        if can_send:
                            code = issue_password_reset_code(db, user["id"])
                            db.commit()
                            mail_job = (user["name"], user["email"], code)
            except Exception:
                app.logger.exception("Falha ao iniciar a recuperação de senha.")
        if mail_job:
            send_password_reset_email(*mail_job)
        session["password_reset_email"] = email
        flash("Se existir uma conta confirmada com esse e-mail, enviamos um código de recuperação.")
        return redirect(url_for("reset_password"))
    return render_template("forgot_password.html")


@app.route("/redefinir-senha", methods=["GET", "POST"])
def reset_password():
    if g.user:
        return redirect(url_for("dashboard"))
    email = session.get("password_reset_email", "")
    if not email:
        flash("Informe seu e-mail para iniciar a recuperação.")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        new_password = request.form.get("password", "")
        confirmation = request.form.get("password_confirm", "")
        with db_conn() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            reset = db.execute(
                "SELECT * FROM password_resets WHERE user_id=?", (user["id"],)
            ).fetchone() if user else None

        if not user or not reset:
            flash("Código inválido ou expirado. Solicite um novo código.")
        elif parse_utc(reset["expires_at"]) <= utc_now():
            flash("Esse código expirou. Solicite um novo código.")
        elif reset["attempts"] >= VERIFICATION_MAX_ATTEMPTS:
            flash("Limite de tentativas atingido. Solicite um novo código.")
        elif not re.fullmatch(r"\d{6}", code):
            flash("Digite os 6 números do código.")
        elif not secrets.compare_digest(reset["code_hash"], password_reset_code_hash(user["id"], code)):
            attempts = reset["attempts"] + 1
            with db_conn() as db:
                db.execute("UPDATE password_resets SET attempts=? WHERE user_id=?", (attempts, user["id"]))
                db.commit()
            remaining = max(0, VERIFICATION_MAX_ATTEMPTS - attempts)
            flash(f"Código incorreto. Restam {remaining} tentativa(s).")
        else:
            errors = password_errors(new_password, user["name"], user["email"])
            if errors:
                flash("A nova senha ainda precisa de: " + ", ".join(errors) + ".")
            elif new_password != confirmation:
                flash("A confirmação da nova senha não corresponde.")
            elif check_password_hash(user["password_hash"], new_password):
                flash("A nova senha precisa ser diferente da senha anterior.")
            else:
                with db_conn() as db:
                    db.execute(
                        "UPDATE users SET password_hash=? WHERE id=?",
                        (generate_password_hash(new_password), user["id"]),
                    )
                    db.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))
                    db.commit()
                session.clear()
                flash("Senha alterada com segurança. Entre usando sua nova senha.")
                return redirect(url_for("login"))

    return render_template(
        "reset_password.html",
        masked_email=mask_email(email),
        ttl_minutes=VERIFICATION_TTL_MINUTES,
    )


@app.post("/redefinir-senha/reenviar")
def resend_password_reset():
    if g.user:
        return redirect(url_for("dashboard"))
    email = session.get("password_reset_email", "")
    if not email:
        return redirect(url_for("forgot_password"))
    mail_job = None
    try:
        with db_conn() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if user and user["email_verified"]:
                reset = db.execute(
                    "SELECT * FROM password_resets WHERE user_id=?", (user["id"],)
                ).fetchone()
                can_send = True
                if reset:
                    elapsed = (utc_now() - parse_utc(reset["last_sent_at"])).total_seconds()
                    can_send = elapsed >= VERIFICATION_RESEND_SECONDS
                if can_send:
                    code = issue_password_reset_code(db, user["id"])
                    db.commit()
                    mail_job = (user["name"], user["email"], code)
    except Exception:
        app.logger.exception("Falha ao reenviar código de recuperação.")
    if mail_job:
        send_password_reset_email(*mail_job)
    flash("Se o endereço estiver apto, um novo código foi enviado. Aguarde antes de tentar novamente.")
    return redirect(url_for("reset_password"))


@app.route("/verificar-email", methods=["GET", "POST"])
def verify_email():
    if g.user:
        return redirect(url_for("dashboard"))
    user_id = session.get("pending_user_id")
    if not user_id:
        flash("Entre na sua conta para continuar a confirmação.")
        return redirect(url_for("login"))

    with db_conn() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        verification = db.execute(
            "SELECT * FROM email_verifications WHERE user_id=?", (user_id,)
        ).fetchone()
    if not user:
        session.clear()
        return redirect(url_for("register"))
    if user["email_verified"]:
        session.clear()
        session["user_id"] = user["id"]
        csrf_token()
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if not verification:
            flash("Não há código ativo. Solicite um novo código.")
        elif parse_utc(verification["expires_at"]) <= utc_now():
            flash("Esse código expirou. Solicite um novo código.")
        elif verification["attempts"] >= VERIFICATION_MAX_ATTEMPTS:
            flash("Limite de tentativas atingido. Solicite um novo código.")
        elif not re.fullmatch(r"\d{6}", code):
            flash("Digite os 6 números do código.")
        elif secrets.compare_digest(verification["code_hash"], verification_code_hash(user_id, code)):
            with db_conn() as db:
                db.execute("UPDATE users SET email_verified=1 WHERE id=?", (user_id,))
                db.execute("DELETE FROM email_verifications WHERE user_id=?", (user_id,))
                db.commit()
            session.clear()
            session["user_id"] = user_id
            csrf_token()
            flash("E-mail confirmado com segurança. Agora vamos calcular seu plano inicial.")
            return redirect(url_for("onboarding"))
        else:
            attempts = verification["attempts"] + 1
            with db_conn() as db:
                db.execute("UPDATE email_verifications SET attempts=? WHERE user_id=?", (attempts, user_id))
                db.commit()
            remaining = max(0, VERIFICATION_MAX_ATTEMPTS - attempts)
            flash(f"Código incorreto. Restam {remaining} tentativa(s).")

    return render_template(
        "verify_email.html",
        masked_email=mask_email(user["email"]),
        ttl_minutes=VERIFICATION_TTL_MINUTES,
    )


@app.post("/verificar-email/reenviar")
def resend_verification():
    if g.user:
        return redirect(url_for("dashboard"))
    user_id = session.get("pending_user_id")
    if not user_id:
        return redirect(url_for("login"))
    with db_conn() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        verification = db.execute(
            "SELECT * FROM email_verifications WHERE user_id=?", (user_id,)
        ).fetchone()
        if not user:
            session.clear()
            return redirect(url_for("register"))
        if verification:
            elapsed = (utc_now() - parse_utc(verification["last_sent_at"])).total_seconds()
            if elapsed < VERIFICATION_RESEND_SECONDS:
                wait = max(1, int(VERIFICATION_RESEND_SECONDS - elapsed))
                flash(f"Aguarde {wait} segundo(s) antes de pedir outro código.")
                return redirect(url_for("verify_email"))
        code = issue_verification_code(db, user_id)
        db.commit()
    if send_verification_email(user["name"], user["email"], code):
        flash("Um novo código foi enviado. O código anterior não é mais válido.")
    else:
        flash("Não foi possível enviar o código. Confira a configuração SMTP e tente novamente.")
    return redirect(url_for("verify_email"))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))



@app.route("/avaliacao", methods=["GET", "POST"])
@login_required
def onboarding():
    user_id = current_user_id()
    with db_conn() as db:
        s = get_settings(db, user_id)
        if request.method == "POST":
            try:
                plan = calculate_initial_plan(request.form)
            except (ValueError, KeyError, TypeError) as exc:
                flash(str(exc) if str(exc) else "Confira as respostas do questionário.")
                return render_template("onboarding.html", s=s, hide_nav=True)

            db.execute("""UPDATE settings SET
                age=?,height=?,start_weight=?,goal_weight=?,final_goal=?,calories=?,protein=?,carbs=?,fat=?,water=?,weekly_target=?,
                sex=?,activity_level=?,goal_type=?,training_days=?,appetite_level=?,meals_per_day=?,budget_monthly=?,restrictions=?,
                bmr=?,tdee=?,onboarding_completed=1,calculation_version='TITAN-1.0'
                WHERE user_id=?""", (
                plan["age"], plan["height"], plan["weight"], plan["goal_weight"], plan["final_goal"],
                plan["calories"], plan["protein"], plan["carbs"], plan["fat"], plan["water"], plan["weekly_target"],
                plan["sex"], plan["activity_level"], plan["goal_type"], plan["training_days"], plan["appetite_level"],
                plan["meals_per_day"], plan["budget_monthly"], plan["restrictions"], plan["bmr"], plan["tdee"], user_id
            ))
            db.execute("""INSERT INTO weights(user_id,day,weight) VALUES(?,?,?)
                          ON CONFLICT(user_id,day) DO UPDATE SET weight=excluded.weight""",
                       (user_id, today(), plan["weight"]))
            db.execute("UPDATE plan_settings SET meals_per_day=? WHERE user_id=?",
                       (plan["meals_per_day"], user_id))

            # Cria horários iniciais sem apagar lembretes personalizados.
            db.execute("DELETE FROM reminders WHERE user_id=? AND title LIKE 'Refeição % (TITAN)'", (user_id,))
            for index, time_value in enumerate(reminder_times(plan["meals_per_day"]), start=1):
                db.execute("INSERT INTO reminders(user_id,title,time,days,enabled) VALUES(?,?,?,?,1)",
                           (user_id, f"Refeição {index} (TITAN)", time_value, "Todos os dias"))
            db.commit()
            session["show_onboarding_result"] = True
            return redirect(url_for("onboarding_result"))
    return render_template("onboarding.html", s=s, hide_nav=True)


@app.get("/avaliacao/resultado")
@login_required
def onboarding_result():
    with db_conn() as db:
        s = get_settings(db, current_user_id())
    if not s["onboarding_completed"]:
        return redirect(url_for("onboarding"))
    activity_names = {
        "sedentary": "Sedentário",
        "light": "Levemente ativo",
        "moderate": "Moderadamente ativo",
        "high": "Muito ativo",
        "very_high": "Extremamente ativo",
    }
    goal_names = {"gain": "Ganhar peso e massa muscular", "maintain": "Manter o peso", "loss": "Reduzir peso"}
    weeks = abs(s["final_goal"] - s["start_weight"]) / s["weekly_target"] if s["weekly_target"] else 0
    daily_budget = s["budget_monthly"] / 30 if s["budget_monthly"] else 0
    meal_budget = daily_budget / s["meals_per_day"] if daily_budget else 0
    tips = []
    if s["goal_type"] == "gain" and s["appetite_level"] == "low":
        tips.append("Como seu apetite é baixo, o plano começa com superávit moderado e prioriza alimentos mais densos e opções líquidas.")
    if s["training_days"] < 2 and s["goal_type"] == "gain":
        tips.append("Para favorecer ganho muscular, registre e progrida nos treinos de força; apenas aumentar calorias não garante que o peso ganho seja músculo.")
    if s["budget_monthly"]:
        tips.append(f"Seu limite inicial é de aproximadamente R$ {daily_budget:.2f} por dia e R$ {meal_budget:.2f} por refeição.")
    tips.append("Depois de 14 dias de registros, o TITAN compara peso e consumo real para sugerir ajustes graduais.")
    return render_template("onboarding_result.html", s=s, weeks=weeks, tips=tips,
                           activity_name=activity_names.get(s["activity_level"], s["activity_level"]),
                           goal_name=goal_names.get(s["goal_type"], s["goal_type"]),
                           daily_budget=daily_budget, meal_budget=meal_budget, hide_nav=True)


@app.route("/")
@login_required
def dashboard():
    user_id = current_user_id()
    day = request.args.get("day", today())
    with db_conn() as db:
        s = get_settings(db, user_id)
        totals = daily_totals(db, user_id, day)
        meals = db.execute("""SELECT m.*,f.name,f.serving,f.unit,f.calories,f.protein,f.carbs,f.fat,f.fiber
                              FROM meals m JOIN foods f ON f.id=m.food_id
                              WHERE m.user_id=? AND m.day=? ORDER BY m.id DESC""", (user_id, day)).fetchall()
        foods = db.execute("SELECT * FROM foods WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
        weights = db.execute("SELECT day,weight FROM weights WHERE user_id=? ORDER BY day", (user_id,)).fetchall()
        latest_weight = weights[-1]["weight"] if weights else s["start_weight"]
        habit = db.execute("SELECT * FROM habits WHERE user_id=? AND day=?", (user_id, day)).fetchone()
        workouts = db.execute("""SELECT w.*,e.name exercise FROM workouts w JOIN exercises e ON e.id=w.exercise_id
                               WHERE w.user_id=? AND w.day=? ORDER BY w.id DESC""", (user_id, day)).fetchall()
        insights = automatic_insights(db, user_id, s, weights)
        predictions, weekly_rate = weight_predictions(weights)
    bmi = latest_weight / max(.1, s["height"] ** 2)
    denominator = max(.1, s["goal_weight"] - s["start_weight"])
    progress = max(0, min(100, (latest_weight - s["start_weight"]) / denominator * 100))
    return render_template("dashboard.html", s=s, totals=totals, meals=meals, foods_all=foods,
                           day=day, weights=weights, latest_weight=latest_weight, habit=habit,
                           workouts=workouts, insights=insights, predictions=predictions,
                           weekly_rate=weekly_rate, bmi=bmi, progress=progress)


@app.route("/foods", methods=["GET", "POST"])
@login_required
def foods():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            db.execute("""INSERT INTO foods(user_id,name,serving,unit,calories,protein,carbs,fat,fiber)
                          VALUES(?,?,?,?,?,?,?,?,?)""", (
                user_id, request.form["name"].strip(), float(request.form["serving"]),
                request.form["unit"], float(request.form["calories"]),
                float(request.form.get("protein") or 0), float(request.form.get("carbs") or 0),
                float(request.form.get("fat") or 0), float(request.form.get("fiber") or 0)
            ))
            db.commit()
            flash("Alimento cadastrado com informações nutricionais.")
            return redirect(url_for("foods"))
        rows = db.execute("SELECT * FROM foods WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    return render_template("foods.html", foods=rows)


@app.post("/foods/edit/<int:item_id>")
@login_required
def edit_food(item_id):
    with db_conn() as db:
        db.execute("""UPDATE foods SET name=?,serving=?,unit=?,calories=?,protein=?,carbs=?,fat=?,fiber=?
                      WHERE id=? AND user_id=?""", (
            request.form["name"].strip(), float(request.form["serving"]), request.form["unit"],
            float(request.form["calories"]), float(request.form.get("protein") or 0),
            float(request.form.get("carbs") or 0), float(request.form.get("fat") or 0),
            float(request.form.get("fiber") or 0), item_id, current_user_id()
        ))
        db.commit()
    flash("Informações do alimento atualizadas.")
    return redirect(url_for("foods"))


@app.post("/foods/delete/<int:item_id>")
@login_required
def delete_food(item_id):
    with db_conn() as db:
        used = db.execute("SELECT COUNT(*) n FROM meals WHERE user_id=? AND food_id=?", (current_user_id(), item_id)).fetchone()["n"]
        if used:
            flash("Este alimento já possui refeições registradas e não pode ser excluído.")
        else:
            db.execute("DELETE FROM foods WHERE id=? AND user_id=?", (item_id, current_user_id()))
            db.commit()
            flash("Alimento excluído.")
    return redirect(url_for("foods"))


@app.post("/meal")
@login_required
def add_meal():
    with db_conn() as db:
        food = db.execute("SELECT id FROM foods WHERE id=? AND user_id=?", (request.form["food_id"], current_user_id())).fetchone()
        if not food:
            abort(404)
        db.execute("INSERT INTO meals(user_id,day,meal_type,food_id,quantity) VALUES(?,?,?,?,?)", (
            current_user_id(), request.form["day"], request.form.get("meal_type", "Refeição"),
            food["id"], float(request.form["quantity"])
        ))
        db.commit()
    return redirect(url_for("dashboard", day=request.form["day"]))


@app.post("/meal/delete/<int:item_id>")
@login_required
def delete_meal(item_id):
    day = request.form["day"]
    with db_conn() as db:
        db.execute("DELETE FROM meals WHERE id=? AND user_id=?", (item_id, current_user_id()))
        db.commit()
    return redirect(url_for("dashboard", day=day))


@app.post("/habit")
@login_required
def save_habit():
    with db_conn() as db:
        db.execute("""INSERT INTO habits(user_id,day,water,sleep,trained,appetite) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(user_id,day) DO UPDATE SET water=excluded.water,sleep=excluded.sleep,
                      trained=excluded.trained,appetite=excluded.appetite""", (
            current_user_id(), request.form["day"], float(request.form.get("water") or 0),
            float(request.form.get("sleep") or 0), 1 if request.form.get("trained") else 0,
            int(request.form.get("appetite") or 0)
        ))
        db.commit()
    flash("Hábitos do dia atualizados.")
    return redirect(url_for("dashboard", day=request.form["day"]))


@app.route("/progress", methods=["GET", "POST"])
@login_required
def progress():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "weight":
                db.execute("INSERT OR REPLACE INTO weights(user_id,day,weight) VALUES(?,?,?)",
                           (user_id, request.form["day"], float(request.form["weight"])))
            elif action == "measure":
                fields = [request.form.get(x) or None for x in ("arm","chest","waist","abdomen","hip","thigh","calf","shoulders")]
                db.execute("""INSERT INTO measurements(user_id,day,arm,chest,waist,abdomen,hip,thigh,calf,shoulders,notes)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (user_id, request.form["day"], *fields, request.form.get("notes", "")))
            elif action == "photo":
                try:
                    filename = save_user_image(request.files.get("photo"), "evolucao")
                except ValueError as exc:
                    flash(str(exc))
                    return redirect(url_for("progress"))
                db.execute("INSERT INTO photos(user_id,day,angle,filename,notes) VALUES(?,?,?,?,?)",
                           (user_id, request.form["day"], request.form["angle"], filename, request.form.get("notes", "")))
            db.commit()
            flash("Evolução registrada.")
            return redirect(url_for("progress"))
        weights = db.execute("SELECT * FROM weights WHERE user_id=? ORDER BY day", (user_id,)).fetchall()
        measures = db.execute("SELECT * FROM measurements WHERE user_id=? ORDER BY day DESC,id DESC", (user_id,)).fetchall()
        photos = db.execute("SELECT * FROM photos WHERE user_id=? ORDER BY day DESC,id DESC", (user_id,)).fetchall()
        predictions, weekly_rate = weight_predictions(weights)
    return render_template("progress.html", today=today(), weights=weights, measures=measures,
                           photos=photos, predictions=predictions, weekly_rate=weekly_rate)


@app.get("/uploads/<int:user_id>/<path:filename>")
@login_required
def uploaded_file(user_id, filename):
    if user_id != current_user_id():
        abort(403)
    return send_from_directory(UPLOAD_DIR / str(user_id), filename)


@app.post("/photo/delete/<int:item_id>")
@login_required
def delete_photo(item_id):
    with db_conn() as db:
        photo = db.execute("SELECT * FROM photos WHERE id=? AND user_id=?", (item_id, current_user_id())).fetchone()
        if photo:
            path = UPLOAD_DIR / str(current_user_id()) / photo["filename"]
            path.unlink(missing_ok=True)
            db.execute("DELETE FROM photos WHERE id=?", (item_id,))
            db.commit()
    flash("Foto removida.")
    return redirect(url_for("progress"))


@app.route("/workouts", methods=["GET", "POST"])
@login_required
def workouts():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "exercise":
                try:
                    image_filename = save_user_image(request.files.get("image"), "exercicio") if request.files.get("image") else ""
                except ValueError as exc:
                    flash(str(exc))
                    return redirect(url_for("workouts"))
                db.execute("""INSERT INTO exercises(user_id,name,muscle,description,image_filename,video_url)
                              VALUES(?,?,?,?,?,?)""", (user_id, request.form["name"], request.form.get("muscle", ""),
                              request.form.get("description", ""), image_filename, request.form.get("video_url", "")))
            else:
                exercise = db.execute("SELECT id FROM exercises WHERE id=? AND user_id=?", (request.form["exercise_id"], user_id)).fetchone()
                if not exercise:
                    abort(404)
                db.execute("""INSERT INTO workouts(user_id,day,exercise_id,sets,reps,load,notes)
                              VALUES(?,?,?,?,?,?,?)""", (user_id, request.form["day"], exercise["id"],
                              int(request.form["sets"]), int(request.form["reps"]), float(request.form.get("load") or 0),
                              request.form.get("notes", "")))
            db.commit()
            flash("Registro de treino atualizado.")
            return redirect(url_for("workouts"))
        exercises = db.execute("SELECT * FROM exercises WHERE user_id=? ORDER BY muscle,name", (user_id,)).fetchall()
        history = db.execute("""SELECT w.*,e.name exercise,e.muscle FROM workouts w JOIN exercises e ON e.id=w.exercise_id
                              WHERE w.user_id=? ORDER BY w.day DESC,w.id DESC LIMIT 150""", (user_id,)).fetchall()
        strength = db.execute("""SELECT e.name,MAX(w.load) max_load,COUNT(*) sessions FROM workouts w JOIN exercises e ON e.id=w.exercise_id
                               WHERE w.user_id=? GROUP BY e.id ORDER BY sessions DESC LIMIT 8""", (user_id,)).fetchall()
    return render_template("workouts.html", today=today(), exercises=exercises, workouts=history, strength=strength)


@app.post("/exercise/delete/<int:item_id>")
@login_required
def delete_exercise(item_id):
    with db_conn() as db:
        used = db.execute("SELECT COUNT(*) n FROM workouts WHERE user_id=? AND exercise_id=?", (current_user_id(), item_id)).fetchone()["n"]
        if used:
            flash("Este exercício já possui treinos registrados e não pode ser excluído.")
        else:
            ex = db.execute("SELECT image_filename FROM exercises WHERE id=? AND user_id=?", (item_id, current_user_id())).fetchone()
            if ex and ex["image_filename"]:
                (UPLOAD_DIR / str(current_user_id()) / ex["image_filename"]).unlink(missing_ok=True)
            db.execute("DELETE FROM exercises WHERE id=? AND user_id=?", (item_id, current_user_id()))
            db.commit()
    return redirect(url_for("workouts"))


def planner_data(db, user_id):
    ps = db.execute("SELECT * FROM plan_settings WHERE user_id=?", (user_id,)).fetchone()
    raw = db.execute("""SELECT p.*,f.serving food_serving,f.calories food_calories,f.protein food_protein,
                        f.carbs food_carbs,f.fat food_fat,f.unit food_unit
                        FROM plan_items p LEFT JOIN foods f ON f.id=p.food_id
                        WHERE p.user_id=? ORDER BY p.category,p.name""", (user_id,)).fetchall()
    items, total, kcal_day, protein_day, carbs_day, fat_day = [], 0, 0, 0, 0, 0
    for row in raw:
        item = dict(row)
        required = row["daily_qty"] * ps["days"]
        to_buy = max(0, required - row["current_stock"])
        packages = math.ceil(to_buy / row["package_qty"]) if row["package_qty"] > 0 else 0
        cost = packages * row["package_price"]
        factor = row["daily_qty"] / row["food_serving"] if row["food_serving"] and row["unit"] in ("g","ml") else 0
        item_kcal = (row["food_calories"] or 0) * factor
        item_protein = (row["food_protein"] or 0) * factor
        item_carbs = (row["food_carbs"] or 0) * factor
        item_fat = (row["food_fat"] or 0) * factor
        item.update(required=required,to_buy=to_buy,packages=packages,cost=cost,
                    remaining=max(0,row["current_stock"]+packages*row["package_qty"]-row["daily_qty"]*ps["completed_days"]),
                    daily_kcal=item_kcal,daily_protein=item_protein,daily_carbs=item_carbs,daily_fat=item_fat)
        items.append(item)
        total += cost; kcal_day += item_kcal; protein_day += item_protein; carbs_day += item_carbs; fat_day += item_fat
    marmitas = ps["days"] * ps["meals_per_day"]
    return ps, items, total, marmitas, kcal_day, protein_day, carbs_day, fat_day


@app.route("/planner", methods=["GET", "POST"])
@login_required
def planner():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "settings":
                db.execute("UPDATE plan_settings SET days=?,meals_per_day=?,completed_days=? WHERE user_id=?", (
                    max(1,int(request.form["days"])), max(1,int(request.form["meals_per_day"])),
                    max(0,int(request.form.get("completed_days") or 0)), user_id))
            elif action == "add":
                food_id = request.form.get("food_id") or None
                db.execute("""INSERT INTO plan_items(user_id,food_id,name,unit,daily_qty,package_qty,package_price,category,current_stock,notes)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""", (user_id,food_id,request.form["name"],request.form["unit"],
                              float(request.form["daily_qty"]),float(request.form["package_qty"]),float(request.form["package_price"]),
                              request.form["category"],float(request.form.get("current_stock") or 0),request.form.get("notes", "")))
            else:
                food_id = request.form.get("food_id") or None
                db.execute("""UPDATE plan_items SET food_id=?,name=?,unit=?,daily_qty=?,package_qty=?,package_price=?,category=?,current_stock=?,notes=?
                              WHERE id=? AND user_id=?""", (food_id,request.form["name"],request.form["unit"],float(request.form["daily_qty"]),
                              float(request.form["package_qty"]),float(request.form["package_price"]),request.form["category"],
                              float(request.form.get("current_stock") or 0),request.form.get("notes", ""),request.form["item_id"],user_id))
            db.commit(); flash("Planejamento mensal atualizado.")
            return redirect(url_for("planner"))
        ps, items, total, marmitas, kcal_day, protein_day, carbs_day, fat_day = planner_data(db, user_id)
        foods_all = db.execute("SELECT * FROM foods WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    return render_template("planner.html", ps=ps,items=items,total=total,marmitas=marmitas,
                           cost_day=total/ps["days"],cost_meal=total/marmitas if marmitas else 0,
                           kcal_day=kcal_day,protein_day=protein_day,carbs_day=carbs_day,fat_day=fat_day,foods_all=foods_all)


@app.post("/planner/delete/<int:item_id>")
@login_required
def planner_delete(item_id):
    with db_conn() as db:
        db.execute("DELETE FROM plan_items WHERE id=? AND user_id=?", (item_id,current_user_id()))
        db.commit()
    flash("Produto removido do planejamento.")
    return redirect(url_for("planner"))


@app.route("/markets", methods=["GET", "POST"])
@login_required
def markets():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "store":
                db.execute("INSERT INTO stores(user_id,name) VALUES(?,?)", (user_id,request.form["name"].strip()))
            else:
                db.execute("""INSERT INTO store_prices(user_id,store_id,plan_item_id,package_price,updated_at)
                              VALUES(?,?,?,?,?) ON CONFLICT(user_id,store_id,plan_item_id)
                              DO UPDATE SET package_price=excluded.package_price,updated_at=excluded.updated_at""", (
                    user_id,request.form["store_id"],request.form["plan_item_id"],float(request.form["package_price"]),datetime.now().isoformat(timespec="seconds")))
            db.commit(); flash("Comparador atualizado.")
            return redirect(url_for("markets"))
        stores = db.execute("SELECT * FROM stores WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
        ps, items, *_ = planner_data(db, user_id)
        prices = db.execute("""SELECT sp.*,s.name store,p.name item FROM store_prices sp
                             JOIN stores s ON s.id=sp.store_id JOIN plan_items p ON p.id=sp.plan_item_id
                             WHERE sp.user_id=? ORDER BY s.name,p.name""", (user_id,)).fetchall()
        ranking = []
        for store in stores:
            total = 0; missing = []
            for item in items:
                price = db.execute("SELECT package_price FROM store_prices WHERE user_id=? AND store_id=? AND plan_item_id=?",
                                   (user_id,store["id"],item["id"])).fetchone()
                if price:
                    total += item["packages"] * price["package_price"]
                else:
                    missing.append(item["name"])
            ranking.append({"name":store["name"],"total":total,"missing":missing,"complete":not missing})
        ranking.sort(key=lambda x: (not x["complete"], x["total"]))
    return render_template("markets.html", stores=stores,items=items,prices=prices,ranking=ranking)


@app.post("/store/delete/<int:item_id>")
@login_required
def delete_store(item_id):
    with db_conn() as db:
        db.execute("DELETE FROM stores WHERE id=? AND user_id=?", (item_id,current_user_id()))
        db.commit()
    return redirect(url_for("markets"))


@app.route("/calendar", methods=["GET", "POST"])
@login_required
def calendar_page():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "meal":
                db.execute("""INSERT INTO calendar_meals(user_id,day,time,title,food_id,quantity,notes)
                              VALUES(?,?,?,?,?,?,?)""", (user_id,request.form["day"],request.form["time"],request.form["title"],
                              request.form.get("food_id") or None,float(request.form.get("quantity") or 0),request.form.get("notes", "")))
            else:
                db.execute("INSERT INTO reminders(user_id,title,time,days,enabled) VALUES(?,?,?,?,1)",
                           (user_id,request.form["title"],request.form["time"],request.form.get("days", "Todos os dias")))
            db.commit(); flash("Agenda atualizada.")
            return redirect(url_for("calendar_page"))
        events = db.execute("""SELECT c.*,f.name food,f.serving,f.calories FROM calendar_meals c LEFT JOIN foods f ON f.id=c.food_id
                             WHERE c.user_id=? ORDER BY c.day,c.time""", (user_id,)).fetchall()
        reminders = db.execute("SELECT * FROM reminders WHERE user_id=? ORDER BY time", (user_id,)).fetchall()
        foods_all = db.execute("SELECT * FROM foods WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    reminder_json = [dict(x) for x in reminders if x["enabled"]]
    return render_template("calendar.html", today=today(),events=events,reminders=reminders,reminder_json=reminder_json,foods_all=foods_all)


@app.post("/calendar/delete/<int:item_id>")
@login_required
def delete_calendar_item(item_id):
    with db_conn() as db:
        db.execute("DELETE FROM calendar_meals WHERE id=? AND user_id=?", (item_id,current_user_id()))
        db.commit()
    return redirect(url_for("calendar_page"))


@app.post("/reminder/delete/<int:item_id>")
@login_required
def delete_reminder(item_id):
    with db_conn() as db:
        db.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (item_id,current_user_id()))
        db.commit()
    return redirect(url_for("calendar_page"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    with db_conn() as db:
        if request.method == "POST":
            db.execute("""UPDATE settings SET age=?,height=?,start_weight=?,goal_weight=?,final_goal=?,calories=?,protein=?,carbs=?,fat=?,water=?,weekly_target=?
                          WHERE user_id=?""", (int(request.form["age"]),float(request.form["height"]),float(request.form["start_weight"]),
                          float(request.form["goal_weight"]),float(request.form["final_goal"]),int(request.form["calories"]),
                          int(request.form["protein"]),int(request.form["carbs"]),int(request.form["fat"]),float(request.form["water"]),
                          float(request.form["weekly_target"]),current_user_id()))
            db.execute("UPDATE users SET name=? WHERE id=?", (request.form["name"].strip(),current_user_id()))
            db.commit(); flash("Metas e perfil atualizados.")
            return redirect(url_for("settings_page"))
        s = get_settings(db,current_user_id())
    return render_template("settings.html", s=s)


@app.get("/report.pdf")
@login_required
def daily_report():
    day = request.args.get("day", today())
    with db_conn() as db:
        s = get_settings(db,current_user_id())
        totals = daily_totals(db,current_user_id(),day)
        meals = db.execute("""SELECT m.*,f.name,f.serving,f.unit,f.calories,f.protein,f.carbs,f.fat
                              FROM meals m JOIN foods f ON f.id=m.food_id WHERE m.user_id=? AND m.day=? ORDER BY m.meal_type,m.id""",
                           (current_user_id(),day)).fetchall()
        habit = db.execute("SELECT * FROM habits WHERE user_id=? AND day=?", (current_user_id(),day)).fetchone()
    buffer=BytesIO(); pdf=canvas.Canvas(buffer,pagesize=A4); width,height=A4; y=height-1.7*cm
    pdf.setTitle(f"TITAN - Relatório {day}"); pdf.setFont("Helvetica-Bold",17); pdf.drawString(1.6*cm,y,"PROJETO TITAN - RELATÓRIO NUTRICIONAL"); y-=.8*cm
    pdf.setFont("Helvetica",10); pdf.drawString(1.6*cm,y,f"Usuário: {g.user['name']} | Data: {day}"); y-=.8*cm
    pdf.setFont("Helvetica-Bold",12); pdf.drawString(1.6*cm,y,"Resumo do dia"); y-=.55*cm
    for label,key,goal,unit in [("Calorias","calories",s["calories"],"kcal"),("Proteínas","protein",s["protein"],"g"),("Carboidratos","carbs",s["carbs"],"g"),("Gorduras","fat",s["fat"],"g")]:
        pdf.setFont("Helvetica",10); pdf.drawString(1.8*cm,y,f"{label}: {totals[key]:.1f} / {goal} {unit}"); y-=.42*cm
    y-=.25*cm; pdf.setFont("Helvetica-Bold",12); pdf.drawString(1.6*cm,y,"Alimentos e valores calculados"); y-=.55*cm
    for m in meals:
        factor=m["quantity"]/m["serving"]
        line=f"{m['meal_type']} - {m['name']}: {m['quantity']:.0f} {m['unit']} | {m['calories']*factor:.0f} kcal | P {m['protein']*factor:.1f} C {m['carbs']*factor:.1f} G {m['fat']*factor:.1f}"
        pdf.setFont("Helvetica",8.8); pdf.drawString(1.8*cm,y,line[:115]); y-=.4*cm
        if y<2.2*cm: pdf.showPage(); y=height-1.7*cm
    y-=.2*cm; pdf.setFont("Helvetica-Bold",11); pdf.drawString(1.6*cm,y,"Hábitos"); y-=.45*cm; pdf.setFont("Helvetica",9)
    pdf.drawString(1.8*cm,y,f"Água: {(habit['water'] if habit else 0):.1f} L | Sono: {(habit['sleep'] if habit else 0):.1f} h | Treino: {'Sim' if habit and habit['trained'] else 'Não'}")
    pdf.save(); buffer.seek(0)
    return send_file(buffer,as_attachment=True,download_name=f"titan_{day}.pdf",mimetype="application/pdf")


@app.get("/planner.pdf")
@login_required
def planner_pdf():
    with db_conn() as db:
        ps,items,total,marmitas,kcal_day,protein_day,carbs_day,fat_day=planner_data(db,current_user_id())
    buffer=BytesIO(); pdf=canvas.Canvas(buffer,pagesize=A4); width,height=A4; y=height-1.7*cm
    pdf.setTitle("TITAN - Lista de compras"); pdf.setFont("Helvetica-Bold",17); pdf.drawString(1.6*cm,y,"PROJETO TITAN - LISTA DE COMPRAS"); y-=.7*cm
    pdf.setFont("Helvetica",9.5); pdf.drawString(1.6*cm,y,f"{ps['days']} dias | {marmitas} marmitas | Estimativa nutricional diária vinculada: {kcal_day:.0f} kcal e {protein_day:.0f} g de proteína"); y-=.75*cm
    category=None
    for item in items:
        if y<2.4*cm: pdf.showPage(); y=height-1.7*cm
        if item["category"]!=category: category=item["category"]; pdf.setFont("Helvetica-Bold",11); pdf.drawString(1.6*cm,y,category); y-=.48*cm
        pdf.setFont("Helvetica",9); cost=f"R$ {item['cost']:.2f}".replace('.',','); pdf.drawString(1.8*cm,y,f"[ ] {item['name']}: {item['packages']} embalagem(ns) - {cost}"); y-=.36*cm
        pdf.setFillGray(.35); pdf.drawString(2.1*cm,y,f"Necessário: {item['required']:.0f} {item['unit']} | {item['daily_kcal']:.0f} kcal/dia vinculadas"); pdf.setFillGray(0); y-=.45*cm
    y-=.15*cm; pdf.setFont("Helvetica-Bold",12); pdf.drawString(1.6*cm,y,(f"TOTAL ESTIMADO: R$ {total:.2f}").replace('.',',')); pdf.save(); buffer.seek(0)
    return send_file(buffer,as_attachment=True,download_name="titan_lista_compras.pdf",mimetype="application/pdf")


@app.get("/calendar.ics")
@login_required
def calendar_ics():
    with db_conn() as db:
        events=db.execute("SELECT * FROM calendar_meals WHERE user_id=? ORDER BY day,time",(current_user_id(),)).fetchall()
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Projeto TITAN//PT-BR"]
    for event in events:
        dt=event["day"].replace('-','')+'T'+event["time"].replace(':','')+'00'
        lines += ["BEGIN:VEVENT",f"UID:titan-{event['id']}@local",f"DTSTART:{dt}",f"SUMMARY:{event['title']}",f"DESCRIPTION:{event['notes'] or ''}","END:VEVENT"]
    lines.append("END:VCALENDAR")
    buffer=BytesIO("\r\n".join(lines).encode('utf-8')); buffer.seek(0)
    return send_file(buffer,as_attachment=True,download_name="titan_refeicoes.ics",mimetype="text/calendar")


@app.get("/export.zip")
@login_required
def export_user_data():
    user_id=current_user_id()
    tables=["settings","foods","meals","weights","measurements","habits","photos","exercises","workouts","plan_settings","plan_items","stores","store_prices","calendar_meals","reminders"]
    with NamedTemporaryFile(suffix=".zip",delete=False) as tmp:
        tmp_path=Path(tmp.name)
    with zipfile.ZipFile(tmp_path,"w",zipfile.ZIP_DEFLATED) as z:
        with db_conn() as db:
            payload={"exported_at":datetime.now().isoformat(),"user":{"name":g.user["name"],"email":g.user["email"]},"data":{}}
            for table in tables:
                payload["data"][table]=[dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE user_id=?",(user_id,)).fetchall()]
            z.writestr("dados_titan.json",json.dumps(payload,ensure_ascii=False,indent=2))
        folder=UPLOAD_DIR/str(user_id)
        if folder.exists():
            for path in folder.iterdir():
                if path.is_file(): z.write(path,Path("imagens")/path.name)
    return send_file(tmp_path,as_attachment=True,download_name="backup_usuario_titan.zip",mimetype="application/zip")


@app.errorhandler(413)
def too_large(_):
    flash("A imagem excede o limite de 8 MB.")
    return redirect(request.referrer or url_for("dashboard"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
