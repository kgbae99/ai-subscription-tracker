from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    Response,
    session,
)
import traceback
import logging
import sqlite3
import os
import sys
import webbrowser
import threading
import csv
import io
import shutil
import calendar
from datetime import date, datetime, timedelta

# =====================
# 설정
# =====================
EXCHANGE_RATE = 1350
RECOMMEND_DELETE_THRESHOLD = 8000
D7_WARNING_DAYS = 7
DB_NAME = "subscriptions.db"
SECRET_KEY = "subscription-app-secret-key"
DEFAULT_MONTHLY_BUDGET = 50000
DEFAULT_APP_PIN = "1234"
BACKUP_DIR_NAME = "backups"

CATEGORY_OPTIONS = [
    "AI",
    "생산성",
    "디자인",
    "영상",
    "음악",
    "개발",
    "클라우드",
    "교육",
    "기타",
]

SORT_OPTIONS = {
    "expiry_asc": "만기 빠른 순",
    "expiry_desc": "만기 늦은 순",
    "price_desc": "가격 높은 순",
    "price_asc": "가격 낮은 순",
    "name_asc": "이름 오름차순",
    "name_desc": "이름 내림차순",
    "newest": "최신 추가 순",
    "oldest": "오래된 순",
}

CATEGORY_COLORS = {
    "AI": "#7c3aed",
    "생산성": "#2563eb",
    "디자인": "#db2777",
    "영상": "#ea580c",
    "음악": "#16a34a",
    "개발": "#0891b2",
    "클라우드": "#4f46e5",
    "교육": "#ca8a04",
    "기타": "#6b7280",
}

CATEGORY_ICONS = {
    "AI": "🤖",
    "생산성": "📋",
    "디자인": "🎨",
    "영상": "🎬",
    "음악": "🎵",
    "개발": "💻",
    "클라우드": "☁️",
    "교육": "📚",
    "기타": "📦",
}


def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
)
app.secret_key = os.environ.get("SECRET_KEY", SECRET_KEY)
app.permanent_session_lifetime = timedelta(minutes=30)


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = get_app_dir()
DB_FILE = os.path.join(APP_DIR, DB_NAME)
BACKUP_DIR = os.path.join(APP_DIR, BACKUP_DIR_NAME)
LOG_FILE = os.path.join(APP_DIR, "app_error.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)

def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table_name, column_name, alter_sql):
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    if column_name not in columns:
        conn.execute(alter_sql)


def get_setting(key, default=""):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, str(value)),
        )
        conn.commit()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('monthly', 'yearly')),
                category TEXT NOT NULL DEFAULT '기타',
                name TEXT NOT NULL,
                price INTEGER NOT NULL DEFAULT 0,
                original_price INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'KRW',
                expiry TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

        ensure_column(
            conn,
            "subscriptions",
            "category",
            "ALTER TABLE subscriptions ADD COLUMN category TEXT NOT NULL DEFAULT '기타'"
        )
        ensure_column(
            conn,
            "subscriptions",
            "is_archived",
            "ALTER TABLE subscriptions ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0"
        )
        ensure_column(
            conn,
            "subscriptions",
            "auto_renew",
            "ALTER TABLE subscriptions ADD COLUMN auto_renew INTEGER NOT NULL DEFAULT 0"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL,
                subscription_name TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                paid_at TEXT NOT NULL,
                new_expiry TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        defaults = {
            "monthly_budget": str(DEFAULT_MONTHLY_BUDGET),
            "app_pin": DEFAULT_APP_PIN,
            "pin_enabled": "0",
        }

        for key, value in defaults.items():
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                    (key, value),
                )

        conn.commit()


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def backup_database():
    if not os.path.exists(DB_FILE):
        return None

    ensure_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"subscriptions_backup_{timestamp}.db")
    shutil.copy2(DB_FILE, backup_path)

    files = sorted(
        [
            os.path.join(BACKUP_DIR, name)
            for name in os.listdir(BACKUP_DIR)
            if name.lower().endswith(".db")
        ],
        key=os.path.getmtime,
        reverse=True,
    )

    for old_file in files[10:]:
        try:
            os.remove(old_file)
        except OSError:
            pass

    return backup_path


def get_recent_backups():
    if not os.path.exists(BACKUP_DIR):
        return []

    files = []
    for name in os.listdir(BACKUP_DIR):
        if name.lower().endswith(".db"):
            path = os.path.join(BACKUP_DIR, name)
            files.append(
                {
                    "name": name,
                    "size_kb": max(1, os.path.getsize(path) // 1024),
                    "modified_at": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

    files.sort(key=lambda x: x["modified_at"], reverse=True)
    return files[:5]


def to_int(value, default=0):
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def parse_date_yyyy_mm_dd(s: str):
    if not s:
        return None
    s = str(s).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def days_until(expiry_str: str):
    d = parse_date_yyyy_mm_dd(expiry_str)
    if not d:
        return None
    return (d - date.today()).days


def normalize_category(category: str):
    c = (category or "").strip()
    return c if c in CATEGORY_OPTIONS else "기타"


def normalize_subscription_form(service_type, category, name, price, currency, expiry):
    normalized_type = service_type if service_type in ("monthly", "yearly") else "monthly"
    normalized_category = normalize_category(category)
    normalized_name = (name or "").strip()
    normalized_price = max(0, to_int(price, 0))
    normalized_currency = (currency or "KRW").upper().strip()
    normalized_expiry = (expiry or "").strip()

    if normalized_currency not in ("KRW", "USD"):
        normalized_currency = "KRW"

    if normalized_expiry and parse_date_yyyy_mm_dd(normalized_expiry) is None:
        normalized_expiry = ""

    errors = []

    if not normalized_name:
        errors.append("서비스명을 입력해 주세요.")
    if len(normalized_name) > 100:
        errors.append("서비스명은 100자 이하로 입력해 주세요.")
    if not str(price).strip():
        errors.append("가격을 입력해 주세요.")

    converted_price = normalized_price
    if normalized_currency == "USD":
        converted_price = normalized_price * EXCHANGE_RATE

    return {
        "type": normalized_type,
        "category": normalized_category,
        "name": normalized_name,
        "price": converted_price,
        "original_price": normalized_price,
        "currency": normalized_currency,
        "expiry": normalized_expiry,
        "errors": errors,
    }


def add_one_month(d: date) -> date:
    month = d.month + 1
    year = d.year
    if month > 12:
        month = 1
        year += 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, max_day))


def process_auto_renewals() -> int:
    """만기일이 지난 자동결제 구독을 1개월 연장하고 결제 이력을 기록합니다."""
    today = date.today()
    renewed = 0
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM subscriptions
            WHERE auto_renew = 1 AND is_archived = 0 AND type = 'monthly'
              AND expiry != '' AND expiry IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            expiry_date = parse_date_yyyy_mm_dd(row["expiry"])
            if expiry_date is None or expiry_date > today:
                continue
            new_expiry = add_one_month(expiry_date)
            # 만기일이 오늘보다 여전히 이전이면 오늘 이후가 될 때까지 반복 연장
            while new_expiry <= today:
                new_expiry = add_one_month(new_expiry)
            new_expiry_str = new_expiry.strftime("%Y-%m-%d")
            conn.execute(
                "UPDATE subscriptions SET expiry = ? WHERE id = ?",
                (new_expiry_str, row["id"]),
            )
            conn.execute(
                """
                INSERT INTO payment_history
                (subscription_id, subscription_name, amount, paid_at, new_expiry)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["name"],
                    row["price"],
                    today.strftime("%Y-%m-%d"),
                    new_expiry_str,
                ),
            )
            renewed += 1
        if renewed:
            conn.commit()
    return renewed


def get_payment_history(limit: int = 30) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT ph.id, ph.subscription_id, ph.subscription_name,
                   ph.amount, ph.paid_at, ph.new_expiry,
                   s.category
            FROM payment_history ph
            LEFT JOIN subscriptions s ON s.id = ph.subscription_id
            ORDER BY ph.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        category = row["category"] or "기타"
        result.append({
            "id": row["id"],
            "subscription_id": row["subscription_id"],
            "subscription_name": row["subscription_name"],
            "amount": row["amount"],
            "paid_at": row["paid_at"],
            "new_expiry": row["new_expiry"],
            "category_icon": CATEGORY_ICONS.get(category, CATEGORY_ICONS["기타"]),
            "category_color": CATEGORY_COLORS.get(category, CATEGORY_COLORS["기타"]),
        })
    return result


def toggle_auto_renew(sub_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscriptions SET auto_renew = CASE WHEN auto_renew = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (sub_id,),
        )
        conn.commit()


def serialize_subscription(row):
    expiry = (row["expiry"] or "").strip()
    due = days_until(expiry)
    category = row["category"] if row["category"] else "기타"

    return {
        "id": row["id"],
        "type": row["type"],
        "category": category,
        "category_color": CATEGORY_COLORS.get(category, CATEGORY_COLORS["기타"]),
        "category_icon": CATEGORY_ICONS.get(category, CATEGORY_ICONS["기타"]),
        "name": row["name"],
        "price": row["price"],
        "original_price": row["original_price"],
        "currency": row["currency"],
        "expiry": expiry,
        "created_at": row["created_at"],
        "is_archived": int(row["is_archived"]) == 1,
        "auto_renew": int(row["auto_renew"]) == 1 if "auto_renew" in row.keys() else False,
        "due_days": due,
        "is_expired": due is not None and due < 0,
        "is_d7": due is not None and 0 <= due <= D7_WARNING_DAYS,
    }


def get_monthly_budget():
    return to_int(get_setting("monthly_budget", DEFAULT_MONTHLY_BUDGET), DEFAULT_MONTHLY_BUDGET)


def set_monthly_budget(value):
    budget = max(0, to_int(value, DEFAULT_MONTHLY_BUDGET))
    set_setting("monthly_budget", budget)


def is_pin_enabled():
    return get_setting("pin_enabled", "0") == "1"


def get_app_pin():
    return get_setting("app_pin", DEFAULT_APP_PIN)


def set_app_pin(pin_text: str):
    pin = str(pin_text).strip()
    if len(pin) < 4:
        return False
    set_setting("app_pin", pin)
    return True


def set_pin_enabled(enabled: bool):
    set_setting("pin_enabled", "1" if enabled else "0")


def is_unlocked():
    return session.get("pin_ok") is True or not is_pin_enabled()


@app.before_request
def require_pin_if_enabled():
    allowed = {"login", "logout", "static"}
    if request.endpoint in allowed:
        return
    if not is_unlocked():
        return redirect(url_for("login"))


def get_all_subscriptions(search_text="", category="", sub_type="", archived_mode="active"):
    conditions = []
    params = []

    if search_text.strip():
        conditions.append("name LIKE ?")
        params.append(f"%{search_text.strip()}%")

    if category.strip() and category in CATEGORY_OPTIONS:
        conditions.append("category = ?")
        params.append(category.strip())

    if sub_type.strip() in ("monthly", "yearly"):
        conditions.append("type = ?")
        params.append(sub_type.strip())

    if archived_mode == "archived":
        conditions.append("is_archived = 1")
    else:
        conditions.append("is_archived = 0")

    where_sql = "WHERE " + " AND ".join(conditions) if conditions else ""

    query = f"""
        SELECT * FROM subscriptions
        {where_sql}
        ORDER BY id DESC
    """

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    return [serialize_subscription(row) for row in rows]


def get_subscription_by_id(sub_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?",
            (sub_id,),
        ).fetchone()
    return serialize_subscription(row) if row else None


def create_subscription(data):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions
            (type, category, name, price, original_price, currency, expiry, created_at, is_archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                data["type"],
                data["category"],
                data["name"],
                data["price"],
                data["original_price"],
                data["currency"],
                data["expiry"],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()


def update_subscription(sub_id, data):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE subscriptions
            SET type = ?, category = ?, name = ?, price = ?, original_price = ?, currency = ?, expiry = ?
            WHERE id = ?
            """,
            (
                data["type"],
                data["category"],
                data["name"],
                data["price"],
                data["original_price"],
                data["currency"],
                data["expiry"],
                sub_id,
            ),
        )
        conn.commit()


def delete_subscription(sub_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        conn.commit()


def archive_subscription(sub_id):
    with get_conn() as conn:
        conn.execute("UPDATE subscriptions SET is_archived = 1 WHERE id = ?", (sub_id,))
        conn.commit()


def restore_subscription(sub_id):
    with get_conn() as conn:
        conn.execute("UPDATE subscriptions SET is_archived = 0 WHERE id = ?", (sub_id,))
        conn.commit()


def clear_all(mode="active"):
    with get_conn() as conn:
        if mode == "archived":
            conn.execute("DELETE FROM subscriptions WHERE is_archived = 1")
        else:
            conn.execute("DELETE FROM subscriptions WHERE is_archived = 0")
        conn.commit()


def sort_by_mode(subs, sort_mode):
    def expiry_key_value(s):
        due = s.get("due_days")
        return 10**9 if due is None else due

    if sort_mode == "expiry_desc":
        return sorted(subs, key=lambda s: (expiry_key_value(s), s["name"]), reverse=True)
    if sort_mode == "price_desc":
        return sorted(subs, key=lambda s: (-s["price"], s["name"]))
    if sort_mode == "price_asc":
        return sorted(subs, key=lambda s: (s["price"], s["name"]))
    if sort_mode == "name_asc":
        return sorted(subs, key=lambda s: s["name"].lower())
    if sort_mode == "name_desc":
        return sorted(subs, key=lambda s: s["name"].lower(), reverse=True)
    if sort_mode == "oldest":
        return sorted(subs, key=lambda s: s["id"])
    if sort_mode == "newest":
        return sorted(subs, key=lambda s: s["id"], reverse=True)

    return sorted(subs, key=lambda s: (s.get("due_days") is None, expiry_key_value(s), s["name"].lower()))


def calculate_totals(subscriptions):
    monthly = sum(s["price"] for s in subscriptions if s["type"] == "monthly")
    yearly = sum(s["price"] for s in subscriptions if s["type"] == "yearly")
    annual_equivalent = yearly + (monthly * 12)
    return monthly, yearly, annual_equivalent


def calculate_stats(subscriptions, monthly_budget):
    monthly_list = [s for s in subscriptions if s["type"] == "monthly"]
    yearly_list = [s for s in subscriptions if s["type"] == "yearly"]
    expired_count = sum(1 for s in subscriptions if s["is_expired"])
    d7_count = sum(1 for s in subscriptions if s["is_d7"])
    recommend_count = sum(1 for s in monthly_list if s["price"] >= RECOMMEND_DELETE_THRESHOLD)

    top_monthly = sorted(monthly_list, key=lambda x: x["price"], reverse=True)[:5]

    monthly_total = sum(s["price"] for s in monthly_list)
    budget_left = monthly_budget - monthly_total
    budget_percent = int((monthly_total / monthly_budget) * 100) if monthly_budget > 0 else 0

    monthly_category_totals = {}
    for s in monthly_list:
        monthly_category_totals[s["category"]] = monthly_category_totals.get(s["category"], 0) + s["price"]

    monthly_category_chart = []
    for category, value in sorted(monthly_category_totals.items(), key=lambda x: x[1], reverse=True):
        monthly_category_chart.append(
            {
                "category": category,
                "value": value,
                "color": CATEGORY_COLORS.get(category, CATEGORY_COLORS["기타"]),
                "icon": CATEGORY_ICONS.get(category, CATEGORY_ICONS["기타"]),
            }
        )

    max_monthly_category_value = max((item["value"] for item in monthly_category_chart), default=0)

    month_totals = {}
    month_monthly_totals = {}
    month_yearly_totals = {}

    for s in subscriptions:
        created_at = (s.get("created_at") or "")[:7]
        if not created_at:
            continue
        month_totals[created_at] = month_totals.get(created_at, 0) + s["price"]
        if s["type"] == "monthly":
            month_monthly_totals[created_at] = month_monthly_totals.get(created_at, 0) + s["price"]
        else:
            month_yearly_totals[created_at] = month_yearly_totals.get(created_at, 0) + s["price"]

    month_report = []
    for month in sorted(month_totals.keys()):
        month_report.append(
            {
                "month": month,
                "total": month_totals.get(month, 0),
                "monthly": month_monthly_totals.get(month, 0),
                "yearly": month_yearly_totals.get(month, 0),
            }
        )

    max_month_report_value = max((item["total"] for item in month_report), default=0)

    return {
        "total_count": len(subscriptions),
        "monthly_count": len(monthly_list),
        "yearly_count": len(yearly_list),
        "expired_count": expired_count,
        "d7_count": d7_count,
        "recommend_count": recommend_count,
        "top_monthly": top_monthly,
        "monthly_total": monthly_total,
        "monthly_budget": monthly_budget,
        "budget_left": budget_left,
        "budget_percent": budget_percent,
        "is_budget_over": monthly_total > monthly_budget,
        "monthly_category_chart": monthly_category_chart,
        "max_monthly_category_value": max_monthly_category_value,
        "month_report": month_report,
        "max_month_report_value": max_month_report_value,
    }


def export_subscriptions_csv():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, type, category, name, price, original_price, currency, expiry, created_at, is_archived
            FROM subscriptions
            ORDER BY id DESC
            """
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "type", "category", "name", "price_krw", "original_price",
        "currency", "expiry", "created_at", "is_archived"
    ])

    for row in rows:
        writer.writerow([
            row["id"], row["type"], row["category"], row["name"], row["price"],
            row["original_price"], row["currency"], row["expiry"], row["created_at"], row["is_archived"]
        ])

    csv_text = output.getvalue()
    output.close()
    return csv_text


def import_subscriptions_csv(file_storage):
    if not file_storage or not file_storage.filename:
        return 0, ["CSV 파일을 선택해 주세요."]

    try:
        raw = file_storage.read()
        text = None

        for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            return 0, ["CSV 인코딩을 읽을 수 없습니다."]

        reader = csv.DictReader(io.StringIO(text))
        count = 0
        errors = []

        for i, row in enumerate(reader, start=2):
            data = normalize_subscription_form(
                service_type=(row.get("type") or "monthly").strip(),
                category=(row.get("category") or "기타").strip(),
                name=(row.get("name") or "").strip(),
                price=row.get("original_price") or row.get("price_krw") or row.get("price") or "0",
                currency=(row.get("currency") or "KRW").strip().upper(),
                expiry=(row.get("expiry") or "").strip(),
            )

            if data["errors"]:
                errors.append(f"{i}행: " + ", ".join(data["errors"]))
                continue

            create_subscription(data)
            count += 1

        return count, errors

    except Exception as e:
        return 0, [f"CSV 가져오기 중 오류: {e}"]


@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_pin_enabled():
        session["pin_ok"] = True
        return redirect(url_for("subscribe"))

    if request.method == "POST":
        pin = (request.form.get("pin") or "").strip()
        if pin == get_app_pin():
            session.permanent = True
            session["pin_ok"] = True
            flash("잠금이 해제되었습니다.", "success")
            return redirect(url_for("subscribe"))
        flash("PIN이 올바르지 않습니다.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("pin_ok", None)
    flash("잠금 화면으로 돌아갔습니다.", "success")
    return redirect(url_for("login"))

@app.errorhandler(Exception)
def handle_exception(e):
    logging.error("예외 발생: %s", str(e))
    logging.error(traceback.format_exc())
    return (
        render_template(
            "error.html",
            error_message="예상치 못한 오류가 발생했습니다. app_error.log 파일을 확인해 주세요."
        ),
        500,
    )
    
@app.route("/")
def home():
    return redirect(url_for("subscribe"))


@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    if request.method == "POST":
        form_data = normalize_subscription_form(
            request.form.get("type", "monthly"),
            request.form.get("category", "기타"),
            request.form.get("name", ""),
            request.form.get("price", ""),
            request.form.get("currency", "KRW"),
            request.form.get("expiry", ""),
        )

        if form_data["errors"]:
            for error in form_data["errors"]:
                flash(error, "error")
            return redirect(url_for("subscribe"))

        create_subscription(form_data)
        flash("구독 정보가 추가되었습니다.", "success")
        return redirect(url_for("subscribe"))

    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    sub_type = request.args.get("sub_type", "").strip()
    sort_mode = request.args.get("sort", "expiry_asc").strip()
    archived_mode = request.args.get("view", "active").strip()

    if sort_mode not in SORT_OPTIONS:
        sort_mode = "expiry_asc"
    if archived_mode not in ("active", "archived"):
        archived_mode = "active"

    renewed_count = process_auto_renewals()
    if renewed_count:
        flash(f"{renewed_count}개 구독이 자동 결제되어 갱신되었습니다.", "success")

    subscriptions = get_all_subscriptions(q, category, sub_type, archived_mode)
    subscriptions = sort_by_mode(subscriptions, sort_mode)

    monthly_list = [s for s in subscriptions if s["type"] == "monthly"]
    yearly_list = [s for s in subscriptions if s["type"] == "yearly"]

    total_monthly, total_yearly, total_annual = calculate_totals(subscriptions)
    monthly_budget = get_monthly_budget()
    stats = calculate_stats(subscriptions, monthly_budget)
    recent_backups = get_recent_backups()
    payment_history = get_payment_history()

    return render_template(
        "subscribe.html",
        monthly_list=monthly_list,
        yearly_list=yearly_list,
        total_monthly=total_monthly,
        total_yearly=total_yearly,
        total_annual=total_annual,
        recommend_threshold=RECOMMEND_DELETE_THRESHOLD,
        d7_warning_days=D7_WARNING_DAYS,
        stats=stats,
        q=q,
        exchange_rate=EXCHANGE_RATE,
        category_options=CATEGORY_OPTIONS,
        selected_category=category,
        selected_sub_type=sub_type,
        sort_mode=sort_mode,
        sort_options=SORT_OPTIONS,
        archived_mode=archived_mode,
        recent_backups=recent_backups,
        pin_enabled=is_pin_enabled(),
        payment_history=payment_history,
    )


@app.route("/edit/<int:sub_id>", methods=["GET", "POST"])
def edit(sub_id):
    sub = get_subscription_by_id(sub_id)
    if not sub:
        flash("수정할 항목을 찾을 수 없습니다.", "error")
        return redirect(url_for("subscribe"))

    if request.method == "POST":
        form_data = normalize_subscription_form(
            request.form.get("type", "monthly"),
            request.form.get("category", "기타"),
            request.form.get("name", ""),
            request.form.get("price", ""),
            request.form.get("currency", "KRW"),
            request.form.get("expiry", ""),
        )

        if form_data["errors"]:
            for error in form_data["errors"]:
                flash(error, "error")
            return render_template(
                "edit.html",
                sub={
                    **sub,
                    "type": request.form.get("type", sub["type"]),
                    "category": request.form.get("category", sub["category"]),
                    "name": request.form.get("name", sub["name"]),
                    "original_price": to_int(request.form.get("price", sub["original_price"])),
                    "currency": request.form.get("currency", sub["currency"]),
                    "expiry": request.form.get("expiry", sub["expiry"]),
                },
                category_options=CATEGORY_OPTIONS,
            )

        update_subscription(sub_id, form_data)
        flash("구독 정보가 수정되었습니다.", "success")
        return redirect(url_for("subscribe"))

    return render_template("edit.html", sub=sub, category_options=CATEGORY_OPTIONS)


@app.route("/delete/<int:sub_id>", methods=["POST"])
def delete(sub_id):
    delete_subscription(sub_id)
    flash("구독 정보가 완전히 삭제되었습니다.", "success")
    return redirect(url_for("subscribe"))


@app.route("/archive/<int:sub_id>", methods=["POST"])
def archive(sub_id):
    archive_subscription(sub_id)
    flash("구독 항목이 보관되었습니다.", "success")
    return redirect(url_for("subscribe"))


@app.route("/restore/<int:sub_id>", methods=["POST"])
def restore(sub_id):
    restore_subscription(sub_id)
    flash("보관 항목이 복원되었습니다.", "success")
    return redirect(url_for("subscribe"))


@app.route("/clear", methods=["POST"])
def clear():
    mode = request.form.get("mode", "active")
    clear_all(mode)
    flash("선택된 목록이 삭제되었습니다.", "success")
    return redirect(url_for("subscribe", view=mode))


@app.route("/auto-renew/<int:sub_id>", methods=["POST"])
def toggle_auto_renew_route(sub_id):
    toggle_auto_renew(sub_id)
    return redirect(url_for("subscribe"))


@app.route("/budget", methods=["POST"])
def budget():
    set_monthly_budget(request.form.get("monthly_budget", DEFAULT_MONTHLY_BUDGET))
    flash("월 예산이 저장되었습니다.", "success")
    return redirect(url_for("subscribe"))


@app.route("/security", methods=["POST"])
def security():
    pin_enabled_form = request.form.get("pin_enabled") == "on"
    new_pin = (request.form.get("new_pin") or "").strip()
    current_pin = (request.form.get("current_pin") or "").strip()

    if new_pin:
        if is_pin_enabled() and current_pin != get_app_pin():
            flash("현재 PIN이 올바르지 않습니다.", "error")
            return redirect(url_for("subscribe"))
        if not set_app_pin(new_pin):
            flash("PIN은 최소 4자리 이상이어야 합니다.", "error")
            return redirect(url_for("subscribe"))

    set_pin_enabled(pin_enabled_form)
    flash("보안 설정이 저장되었습니다.", "success")
    return redirect(url_for("subscribe"))


@app.route("/report")
def report():
    subscriptions = get_all_subscriptions("", "", "", "active")
    subscriptions = sort_by_mode(subscriptions, "expiry_asc")
    total_monthly, total_yearly, total_annual = calculate_totals(subscriptions)
    monthly_budget = get_monthly_budget()
    stats = calculate_stats(subscriptions, monthly_budget)

    monthly_list = [s for s in subscriptions if s["type"] == "monthly"]
    yearly_list = [s for s in subscriptions if s["type"] == "yearly"]

    return render_template(
        "report.html",
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        monthly_list=monthly_list,
        yearly_list=yearly_list,
        total_monthly=total_monthly,
        total_yearly=total_yearly,
        total_annual=total_annual,
        stats=stats,
    )


@app.route("/export")
def export_csv():
    csv_text = export_subscriptions_csv()
    filename = f"subscriptions_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/import", methods=["POST"])
def import_csv():
    file = request.files.get("csv_file")
    imported_count, errors = import_subscriptions_csv(file)

    if imported_count:
        flash(f"{imported_count}개 항목을 가져왔습니다.", "success")
    if errors:
        for err in errors[:10]:
            flash(err, "error")
        if len(errors) > 10:
            flash(f"추가 오류 {len(errors) - 10}건이 더 있습니다.", "error")
    if imported_count == 0 and not errors:
        flash("가져온 데이터가 없습니다.", "error")

    return redirect(url_for("subscribe"))


@app.route("/user/<username>")
def user(username):
    return f"안녕하세요, {username}님"


def open_browser():
    webbrowser.open("http://127.0.0.1:5000/subscribe")


if __name__ == "__main__":
    init_db()
    backup_database()

    port = int(os.environ.get("PORT", 5000))
    is_cloud = "PORT" in os.environ  # Render/기타 클라우드 플랫폼은 PORT 환경변수를 지정해줌

    if not is_cloud:
        threading.Timer(1, open_browser).start()

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
else:
    # gunicorn 등 WSGI 서버가 app을 import해서 실행하는 경우
    init_db()
    backup_database()