from __future__ import annotations

import base64
import html
import os
import sqlite3
import uuid
from datetime import date
from pathlib import Path
from typing import List

import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field


# -----------------------------
# 基本設定
# -----------------------------
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
ITEM_IMAGE_DIR = DATA_DIR / "item_images"
CHECK_IMAGE_DIR = DATA_DIR / "check_images"
DB_PATH = DATA_DIR / "app.db"

ITEM_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
CHECK_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

st.set_page_config(
    page_title="忘れ物チェックAI",
    page_icon="🎒",
    layout="wide",
)


# -----------------------------
# DB
# -----------------------------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                image_path TEXT NOT NULL,
                schedule_type TEXT NOT NULL,
                target_date TEXT,
                weekday INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS checks (
                check_date TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (check_date, item_id),
                FOREIGN KEY (item_id) REFERENCES items(id)
            );
            """
        )


def add_item(
    name: str,
    image_path: str,
    schedule_type: str,
    target_date: str | None,
    weekday: int | None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO items
                (name, image_path, schedule_type, target_date, weekday)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, image_path, schedule_type, target_date, weekday),
        )


def get_due_items(target: date) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                i.*,
                COALESCE(c.confirmed, 0) AS confirmed,
                COALESCE(c.confidence, 0) AS confidence,
                COALESCE(c.reason, '') AS reason
            FROM items AS i
            LEFT JOIN checks AS c
                ON c.item_id = i.id
                AND c.check_date = ?
            WHERE
                i.active = 1
                AND (
                    i.schedule_type = 'daily'
                    OR (
                        i.schedule_type = 'date'
                        AND i.target_date = ?
                    )
                    OR (
                        i.schedule_type = 'weekly'
                        AND i.weekday = ?
                    )
                )
            ORDER BY i.created_at ASC
            """,
            (target.isoformat(), target.isoformat(), target.weekday()),
        ).fetchall()


def get_all_items() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM items
            WHERE active = 1
            ORDER BY created_at DESC
            """
        ).fetchall()


def save_check(
    check_date: date,
    item_id: int,
    confirmed: bool,
    confidence: float = 1.0,
    reason: str = "手動で変更",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO checks
                (check_date, item_id, confirmed, confidence, reason)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(check_date, item_id)
            DO UPDATE SET
                confirmed = excluded.confirmed,
                confidence = excluded.confidence,
                reason = excluded.reason,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                check_date.isoformat(),
                item_id,
                int(confirmed),
                float(confidence),
                reason,
            ),
        )


def deactivate_item(item_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE items SET active = 0 WHERE id = ?",
            (item_id,),
        )


# -----------------------------
# 画像・表示
# -----------------------------
def save_uploaded_image(uploaded_file, folder: Path) -> str:
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"

    filename = f"{uuid.uuid4().hex}{suffix}"
    path = folder / filename
    path.write_bytes(uploaded_file.getvalue())
    return str(path)


def mime_type_from_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
    }.get(suffix, "image/jpeg")


def mime_type_from_upload(uploaded_file) -> str:
    if uploaded_file.type:
        return uploaded_file.type
    return mime_type_from_path(uploaded_file.name)


def image_as_data_uri(path: str) -> str:
    image_path = Path(path)
    mime = mime_type_from_path(path)
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def schedule_label(item: sqlite3.Row) -> str:
    if item["schedule_type"] == "daily":
        return "毎日"
    if item["schedule_type"] == "weekly":
        return f"毎週{WEEKDAYS[item['weekday']]}曜日"
    return f"{item['target_date']}のみ"


def render_item_card(item: sqlite3.Row) -> None:
    confirmed = bool(item["confirmed"])
    border_color = "#6EEB83" if confirmed else "#FF5252"
    status_text = "確認済み" if confirmed else "未確認"
    status_icon = "✅" if confirmed else "⚠️"

    safe_name = html.escape(item["name"])
    safe_reason = html.escape(item["reason"] or "まだ判定していません")
    image_uri = image_as_data_uri(item["image_path"])

    st.markdown(
        f"""
        <div style="
            border: 5px solid {border_color};
            border-radius: 18px;
            padding: 12px;
            margin-bottom: 8px;
            min-height: 315px;
            background: white;
            box-shadow: 0 3px 10px rgba(0,0,0,0.08);
        ">
            <img src="{image_uri}" style="
                width: 100%;
                height: 180px;
                object-fit: contain;
                border-radius: 10px;
                background: #f7f7f7;
            ">
            <div style="font-size: 1.15rem; font-weight: 700; margin-top: 10px;">
                {safe_name}
            </div>
            <div style="font-size: 0.98rem; font-weight: 700; color: {border_color};">
                {status_icon} {status_text}
            </div>
            <div style="font-size: 0.78rem; color: #666; margin-top: 5px;">
                {safe_reason}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------
# Geminiの返却形式
# -----------------------------
class ItemMatch(BaseModel):
    item_id: int = Field(description="照合対象の持ち物ID")
    found: bool = Field(description="確認写真内に同じ持ち物が写っているか")
    confidence: float = Field(
        ge=0,
        le=1,
        description="判定の確信度。0から1",
    )
    reason: str = Field(description="日本語による短い判定理由")


class VerificationResult(BaseModel):
    results: List[ItemMatch]


def get_api_key() -> str | None:
    try:
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return os.environ.get("GEMINI_API_KEY")


def get_model_name() -> str:
    try:
        return st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")
    except Exception:
        return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def verify_items_with_ai(
    check_photo_bytes: bytes,
    check_photo_mime: str,
    items: list[sqlite3.Row],
) -> VerificationResult:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEYが設定されていません。"
            ".streamlit/secrets.tomlを確認してください。"
        )

    client = genai.Client(api_key=api_key)

    prompt = """
あなたは学校の持ち物確認アプリの画像照合AIです。

最初の画像は、ユーザーが今回確認のために撮影した「確認写真」です。
その後には、各持ち物の「登録時の参照画像」が、
item_idと持ち物名を示す文章に続いて入力されます。

各参照画像について、確認写真の中に同じ持ち物が実際に写っているか判定してください。

重要なルール:
- 種類が同じだけでは「同じ持ち物」と断定しないでください。
- 表紙、色、形、文字、模様などをできる限り比較してください。
- 一部しか見えない、重なっている、暗いなどで判断が難しい場合は、
  found=falseまたは低いconfidenceにしてください。
- 見つからない物を推測でfound=trueにしないでください。
- 全item_idについて必ず1件ずつ結果を返してください。
- reasonは短い日本語にしてください。
"""

    contents: list = [
        prompt,
        "【確認写真】",
        types.Part.from_bytes(
            data=check_photo_bytes,
            mime_type=check_photo_mime,
        ),
    ]

    for item in items:
        image_path = Path(item["image_path"])
        contents.extend(
            [
                f"【参照画像】item_id={item['id']} / 持ち物名={item['name']}",
                types.Part.from_bytes(
                    data=image_path.read_bytes(),
                    mime_type=mime_type_from_path(str(image_path)),
                ),
            ]
        )

    response = client.models.generate_content(
        model=get_model_name(),
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=VerificationResult,
        ),
    )

    if not response.text:
        raise RuntimeError("AIから判定結果が返りませんでした。")

    return VerificationResult.model_validate_json(response.text)


# -----------------------------
# 画面
# -----------------------------
init_db()

st.title("🎒 忘れ物チェックAI")
st.caption("登録画像と確認写真を照合して、持ち物を色分けします。")

page = st.sidebar.radio(
    "メニュー",
    ["今日・指定日の確認", "持ち物を登録", "登録内容を管理"],
)


if page == "持ち物を登録":
    st.header("持ち物を登録")

    with st.form("register_item", clear_on_submit=True):
        name = st.text_input(
            "持ち物名",
            placeholder="例：数学の教科書、筆箱、iPad",
        )

        reference_image = st.file_uploader(
            "登録画像",
            type=["jpg", "jpeg", "png", "webp"],
            help="できれば正面から、明るい場所で撮影してください。",
        )

        schedule_ui = st.radio(
            "必要になるタイミング",
            ["指定した日だけ", "毎週", "毎日"],
            horizontal=True,
        )

        selected_date = None
        selected_weekday = None

        if schedule_ui == "指定した日だけ":
            selected_date = st.date_input(
                "必要な日",
                value=date.today(),
            )
        elif schedule_ui == "毎週":
            weekday_name = st.selectbox("曜日", WEEKDAYS)
            selected_weekday = WEEKDAYS.index(weekday_name)

        submitted = st.form_submit_button(
            "登録する",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if not name.strip():
            st.error("持ち物名を入力してください。")
        elif reference_image is None:
            st.error("登録画像を選んでください。")
        else:
            image_path = save_uploaded_image(reference_image, ITEM_IMAGE_DIR)

            if schedule_ui == "指定した日だけ":
                schedule_type = "date"
                target_date_value = selected_date.isoformat()
                weekday_value = None
            elif schedule_ui == "毎週":
                schedule_type = "weekly"
                target_date_value = None
                weekday_value = selected_weekday
            else:
                schedule_type = "daily"
                target_date_value = None
                weekday_value = None

            add_item(
                name=name.strip(),
                image_path=image_path,
                schedule_type=schedule_type,
                target_date=target_date_value,
                weekday=weekday_value,
            )
            st.success(f"「{name.strip()}」を登録しました。")


elif page == "登録内容を管理":
    st.header("登録内容を管理")
    items = get_all_items()

    if not items:
        st.info("まだ持ち物が登録されていません。")
    else:
        for item in items:
            col1, col2, col3 = st.columns([1, 2.5, 1])

            with col1:
                st.image(item["image_path"], width=150)

            with col2:
                st.subheader(item["name"])
                st.write(schedule_label(item))

            with col3:
                if st.button(
                    "削除",
                    key=f"delete_{item['id']}",
                    use_container_width=True,
                ):
                    deactivate_item(item["id"])
                    st.rerun()

            st.divider()


else:
    target_date = st.date_input(
        "確認する日",
        value=date.today(),
    )

    st.subheader(f"{target_date.strftime('%Y年%m月%d日')} の持ち物")
    items = get_due_items(target_date)

    if not items:
        st.info(
            "この日に必要な持ち物が登録されていません。"
            "左の「持ち物を登録」から追加してください。"
        )
        st.stop()

    confirmed_count = sum(bool(item["confirmed"]) for item in items)
    st.progress(
        confirmed_count / len(items),
        text=f"{confirmed_count} / {len(items)} 個を確認済み",
    )

    st.markdown("### 確認写真を撮影・選択")

upload_tab, camera_tab = st.tabs(
["🖼️ 写真を選ぶ（おすすめ）", "📷 カメラで撮る"]
)

with upload_tab:
uploaded_photo = st.file_uploader(
"スマホに保存されている確認写真を選んでください",
type=["jpg", "jpeg", "png", "webp"],
key="check_photo_upload",
)

with camera_tab:
camera_photo = st.camera_input(
"持ち物を並べて撮影してください",
key="check_photo_camera",
)

if uploaded_photo is not None:
check_photo = uploaded_photo
else:
check_photo = camera_photo

if check_photo is not None:
    st.image(
        check_photo,
        caption="今回の確認写真",
        width=450,
    )

        if st.button(
            "AIで持ち物を確認する",
            type="primary",
            use_container_width=True,
        ):
            if len(items) > 12:
                st.error(
                    "一度に照合できる試作版の上限は12個です。"
                    "持ち物を分けて確認してください。"
                )
            else:
                try:
                    with st.spinner("登録画像と照合しています…"):
                        check_bytes = check_photo.getvalue()
                        check_mime = mime_type_from_upload(check_photo)

                        # 確認用画像を履歴としてローカル保存
                        check_path = (
                            CHECK_IMAGE_DIR
                            / f"{target_date.isoformat()}_{uuid.uuid4().hex}.jpg"
                        )
                        check_path.write_bytes(check_bytes)

                        result = verify_items_with_ai(
                            check_photo_bytes=check_bytes,
                            check_photo_mime=check_mime,
                            items=items,
                        )

                        returned_ids = set()

                        for match in result.results:
                            returned_ids.add(match.item_id)

                            # 低確信度なら、安全側に倒して未確認にする
                            is_confirmed = (
                                match.found and match.confidence >= 0.60
                            )

                            save_check(
                                check_date=target_date,
                                item_id=match.item_id,
                                confirmed=is_confirmed,
                                confidence=match.confidence,
                                reason=(
                                    f"{match.reason} "
                                    f"（確信度 {match.confidence:.0%}）"
                                ),
                            )

                        # AIが返さなかった物は未確認にする
                        for item in items:
                            if item["id"] not in returned_ids:
                                save_check(
                                    check_date=target_date,
                                    item_id=item["id"],
                                    confirmed=False,
                                    confidence=0,
                                    reason="AIから判定結果が返りませんでした",
                                )

                    st.success("確認が完了しました。")
                    st.rerun()

                except Exception as error:
                    st.error(f"AI判定に失敗しました：{error}")
                    st.info(
                        "APIキー、インターネット接続、モデル名を確認してください。"
                    )

    st.markdown("### 判定結果")
    st.caption(
        "黄緑＝確認済み、赤＝未確認。"
        "AIが間違えた場合は下のボタンで手動修正できます。"
    )

    # AI実行後の最新状態を読み直す
    items = get_due_items(target_date)

    columns_per_row = 3
    for start in range(0, len(items), columns_per_row):
        cols = st.columns(columns_per_row)

        for offset, item in enumerate(items[start:start + columns_per_row]):
            with cols[offset]:
                render_item_card(item)

                left, right = st.columns(2)

                with left:
                    if st.button(
                        "✅ 確認済み",
                        key=f"yes_{target_date}_{item['id']}",
                        use_container_width=True,
                    ):
                        save_check(
                            target_date,
                            item["id"],
                            True,
                            1.0,
                            "手動で確認済みに変更",
                        )
                        st.rerun()

                with right:
                    if st.button(
                        "↩️ 未確認",
                        key=f"no_{target_date}_{item['id']}",
                        use_container_width=True,
                    ):
                        save_check(
                            target_date,
                            item["id"],
                            False,
                            1.0,
                            "手動で未確認に変更",
                        )
                        st.rerun()
