import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import json
import os
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# === KONŠTANTY ===
BASE_URL = "https://api.hyperliquid.xyz/info"
HEADERS = {"content-type": "application/json"}
REFRESH_INTERVAL = 300  # sekúnd = 5 minút
DASHBOARD_FILE = "dashboards.json"
DATA_DIR = "data"
DELETE_PIN = "6000"  # 🔒 bezpečnostný PIN

# === NOTES ===
NOTES_DIR = "notes"

st.set_page_config(page_title="Hyperliquid Live Wallet Dashboards", layout="wide")

# === ZABEZPEČI, ŽE EXISTUJÚ PRIEČINKY ===
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(NOTES_DIR, exist_ok=True)

# === NOTES HELPERS ===
def _safe_filename(name: str) -> str:
    # jednoduché "sanitize" názvu, aby sa z toho dal spraviť filename
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).rstrip()
    return safe if safe else "dashboard"

def notes_path(dash_name: str) -> str:
    return os.path.join(NOTES_DIR, f"{_safe_filename(dash_name)}.txt")

def load_notes(dash_name: str) -> str:
    p = notes_path(dash_name)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def save_notes(dash_name: str, text: str) -> None:
    p = notes_path(dash_name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text or "")

# === LOAD / SAVE DASHBOARDY ===
def load_dashboards():
    if os.path.exists(DASHBOARD_FILE):
        with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
            try:
                dashboards = json.load(f)
            except json.JSONDecodeError:
                return {}

        # ✅ MIGRÁCIA: starý formát wallets:[w1,w2] -> nový formát wallet:w1
        changed = False
        for name, info in dashboards.items():
            if "wallet" not in info and "wallets" in info and isinstance(info["wallets"], list) and len(info["wallets"]) > 0:
                info["wallet"] = info["wallets"][0]
                changed = True

        if changed:
            save_dashboards(dashboards)

        return dashboards
    return {}

def save_dashboards(dashboards):
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(dashboards, f, indent=2)

# === LOAD / SAVE DATAFRAME ===
def load_dashboard_data(name):
    path = os.path.join(DATA_DIR, f"{name}.csv")
    if os.path.exists(path):
        return pd.read_csv(path, parse_dates=["timestamp"])
    else:
        return pd.DataFrame(columns=["timestamp", "wallet", "value", "total"])

def save_dashboard_data(name, df):
    path = os.path.join(DATA_DIR, f"{name}.csv")
    df.to_csv(path, index=False)

# === DELETE DASHBOARD ===
def delete_dashboard(name):
    if name in st.session_state.dashboards:
        del st.session_state.dashboards[name]
    if name in st.session_state.dataframes:
        del st.session_state.dataframes[name]
    save_dashboards(st.session_state.dashboards)

    # zmaž CSV
    path = os.path.join(DATA_DIR, f"{name}.csv")
    if os.path.exists(path):
        os.remove(path)

    # (voliteľné) zmaž aj poznámky
    npath = notes_path(name)
    if os.path.exists(npath):
        os.remove(npath)

    st.success(f"🗑️ Dashboard '{name}' deleted.")
    st.rerun()

# === SESSION INIT ===
if "dashboards" not in st.session_state:
    st.session_state.dashboards = load_dashboards()
if "dataframes" not in st.session_state:
    st.session_state.dataframes = {}

# === API FUNKCIE ===
def get_wallet_value(wallet):
    payload = {"type": "clearinghouseState", "user": wallet}
    try:
        r = requests.post(BASE_URL, json=payload, headers=HEADERS, timeout=10)
        data = r.json()
        return float(data["marginSummary"]["accountValue"])
    except Exception:
        return 0.0

def get_wallet_volume(wallet, start_timestamp):
    """Zistí trading volume od zadaného timestampu (ms)."""
    try:
        payload = {"type": "userFills", "user": wallet}
        r = requests.post(BASE_URL, json=payload, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return 0.0

        trades = r.json()
        if not isinstance(trades, list) or len(trades) == 0:
            return 0.0

        total_volume = 0.0
        for trade in trades:
            try:
                trade_time = int(trade.get("time", 0))
                if trade_time >= start_timestamp:
                    price = float(trade.get("px", 0))
                    size = float(trade.get("sz", 0))
                    total_volume += price * size
            except Exception:
                continue
        return round(total_volume, 2)
    except Exception as e:
        print(f"⚠️ Error getting volume for {wallet}: {e}")
        return 0.0

def _extract_position_size(position: dict) -> float:
    """
    Skúsi vyčítať size zo známych kľúčov.
    Na Hyperliquid je často 'szi' (signed size). Ak to nebude, skúsi alternatívy.
    """
    for k in ["szi", "sz", "size", "positionSize"]:
        if k in position:
            try:
                return float(position.get(k, 0))
            except Exception:
                pass
    return 0.0

def get_open_positions(wallet):
    """Načíta otvorené pozície pre jednu walletku a rozlíši LONG/SHORT."""
    payload = {"type": "clearinghouseState", "user": wallet}
    try:
        r = requests.post(BASE_URL, json=payload, headers=HEADERS, timeout=10)
        data = r.json()

        positions = []
        asset_positions = data.get("assetPositions", [])

        def handle_position_obj(position: dict):
            if not position:
                return
            try:
                pv = float(position.get("positionValue", 0))
            except Exception:
                pv = 0.0

            # berieme aj SHORT pozície => abs(pv) > 0
            if abs(pv) <= 0:
                return

            size = _extract_position_size(position)
            side = "LONG" if size > 0 else "SHORT" if size < 0 else "FLAT"

            try:
                upnl = float(position.get("unrealizedPnl", 0))
            except Exception:
                upnl = 0.0

            positions.append({
                "Token": position.get("coin"),
                "Side": side,
                "Position Value (USD)": round(abs(pv), 2),
                "Size": round(size, 6),
                "Unrealized PnL (USD)": round(upnl, 2)
            })

        if isinstance(asset_positions, list):  # nový formát
            for pos in asset_positions:
                position = pos.get("position")
                handle_position_obj(position)
        elif isinstance(asset_positions, dict):  # starý formát
            for _, pos in asset_positions.items():
                position = pos.get("position")
                handle_position_obj(position)

        # zoradenie: LONGs hore, potom SHORTs, a podľa Position Value
        side_order = {"LONG": 0, "SHORT": 1, "FLAT": 2}
        positions.sort(key=lambda x: (side_order.get(x["Side"], 99), -x["Position Value (USD)"]))
        return positions

    except Exception as e:
        print(f"⚠️ Error fetching positions for {wallet}: {e}")
        return []

# === DASHBOARD CREATOR ===
def create_dashboard(name, wallet, volume_start_ts, start_value):
    st.session_state.dashboards[name] = {
        "wallet": wallet,
        "volume_start_ts": volume_start_ts,
        "start_total": start_value
    }
    st.session_state.dataframes[name] = pd.DataFrame(columns=["timestamp", "wallet", "value", "total"])
    save_dashboards(st.session_state.dashboards)
    save_dashboard_data(name, st.session_state.dataframes[name])

# === SIDEBAR ===
st.sidebar.header("➕ Add New Dashboard")
name = st.sidebar.text_input("Dashboard name")
wallet = st.sidebar.text_input("Wallet address")

st.sidebar.markdown("#### 💰 Start Value")
start_value = st.sidebar.number_input("Start value (USD)", min_value=0.0, step=100.0)

st.sidebar.markdown("#### 📆 Volume Tracking Start")
start_date = st.sidebar.date_input("Start date", datetime.now())
start_time = st.sidebar.time_input("Start time", datetime.now().time())

if st.sidebar.button("Add Dashboard"):
    if not (name and wallet):
        st.sidebar.error("Please fill in all fields!")
    elif name in st.session_state.dashboards:
        st.sidebar.warning(f"Dashboard '{name}' already exists — not added again.")
        st.stop()
    else:
        dt = datetime.combine(start_date, start_time)
        volume_start_ts = int(dt.timestamp() * 1000)
        create_dashboard(name, wallet, volume_start_ts, start_value)
        st.sidebar.success(f"✅ Dashboard '{name}' created! Tracking volume since {dt.strftime('%Y-%m-%d %H:%M:%S')}")

# === HLAVNÝ NADPIS ===
st.title("📊 Hyperliquid Live Wallet Dashboards")

# === PERIODICKÝ REFRESH ===
st_autorefresh(interval=REFRESH_INTERVAL * 1000, key="data_refresh")

# === RENDER DASHBOARDOV ===
if not st.session_state.dashboards:
    st.info("ℹ️ Add a dashboard from the sidebar to start tracking wallets.")
else:
    for name, info in list(st.session_state.dashboards.items()):
        wallet_addr = info.get("wallet") or (info.get("wallets", [None])[0])
        if not wallet_addr:
            continue

        start_ts = int(info.get("volume_start_ts", 0)) if info.get("volume_start_ts") else 0

        df = st.session_state.dataframes.get(name)
        if df is None or df.empty:
            df = load_dashboard_data(name)
            st.session_state.dataframes[name] = df

        value = get_wallet_value(wallet_addr)
        total = value  # ✅ pri 1 wallete je TOTAL = accountValue
        total_volume = get_wallet_volume(wallet_addr, start_ts)

        if value != 0.0:
            now = pd.Timestamp.now()
            new_rows = [
                {"timestamp": now, "wallet": "total", "value": total, "total": total},
                {"timestamp": now, "wallet": wallet_addr, "value": value, "total": total},
            ]
            df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
            st.session_state.dataframes[name] = df
            save_dashboard_data(name, df)

        # === HEADER ROW: TITLE + NOTES POPOVER + DELETE ===
        top_col1, top_col_notes, top_col2 = st.columns([6, 2, 1])

        with top_col1:
            st.subheader(f"🧭 {name}")

        # ✅ Notes popover (vyskakovacie okno)
        with top_col_notes:
            with st.popover("📝 Notes"):
                existing = load_notes(name)
                text = st.text_area(
                    "Poznámky k dashboardu",
                    value=existing,
                    height=220,
                    placeholder="Napíš si sem poznámky…",
                    key=f"notes_{name}",
                )
                if st.button("💾 Save", key=f"save_notes_{name}"):
                    save_notes(name, text)
                    st.success("Saved ✅")

        with top_col2:
            with st.expander("🗑️ Delete Dashboard", expanded=False):
                pin = st.text_input("Enter PIN to confirm delete", type="password", key=f"pin_{name}")
                if st.button(f"Confirm Delete", key=f"del_{name}"):
                    if pin == DELETE_PIN:
                        delete_dashboard(name)
                    else:
                        st.error("❌ Incorrect PIN. Dashboard not deleted.")

        m1, m2, m3, m4 = st.columns(4)
        start_val = info.get("start_total", 0)
        curr_val = total
        pct_change = ((curr_val - start_val) / start_val) * 100 if start_val > 0 else 0

        with m1:
            st.metric("💰 Start Value (USD)", f"${start_val:,.2f}")
        with m2:
            st.metric("📈 Current Value (USD)", f"${curr_val:,.2f}")
        with m3:
            st.metric("📊 Change (%)", f"{pct_change:+.2f}%")
        with m4:
            st.metric("🔄 Volume Since Start (USD)", f"${total_volume:,.2f}")
            if start_ts:
                st.caption(f"Since: {datetime.fromtimestamp(start_ts / 1000).strftime('%Y-%m-%d %H:%M:%S')}")

        if not df.empty:
            fig = go.Figure()

            dfi = df[df["wallet"] == wallet_addr]
            if not dfi.empty:
                fig.add_trace(go.Scatter(
                    x=dfi["timestamp"], y=dfi["value"],
                    mode="lines+markers", name="Wallet", line=dict(width=3)
                ))

            df_total = df[df["wallet"] == "total"]
            if not df_total.empty:
                fig.add_trace(go.Scatter(
                    x=df_total["timestamp"], y=df_total["total"],
                    mode="lines+markers", name="TOTAL", line=dict(width=5, color="gold")
                ))

            fig.update_layout(
                height=450,
                margin=dict(l=30, r=20, t=40, b=60),
                font=dict(size=16),
                xaxis=dict(title=dict(text="Time", font=dict(size=18)), tickfont=dict(size=14)),
                yaxis=dict(title=dict(text="USD Value", font=dict(size=18)), tickfont=dict(size=14)),
                legend=dict(font=dict(size=14)),
            )
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("### 💼 Wallet")
        st.markdown(f"**🪙 Wallet:** `{wallet_addr}`")

        # === OPEN POSITIONS (LONG+SHORT spolu) ===
        st.markdown("### 📈 Open Positions (LONG zelená, SHORT červená)")

        positions = get_open_positions(wallet_addr)
        if positions:
            pos_df = pd.DataFrame(positions)
            pos_df.index = range(1, len(pos_df) + 1)

            # ---- styling ----
            def style_pnl(val):
                try:
                    v = float(val)
                    color = "green" if v > 0 else "red" if v < 0 else "white"
                    return f"color: {color}; font-weight: bold;"
                except Exception:
                    return ""

            def style_side(val):
                if str(val).upper() == "LONG":
                    return "color: green; font-weight: bold;"
                if str(val).upper() == "SHORT":
                    return "color: red; font-weight: bold;"
                return "font-weight: bold;"

            def style_bold(_):
                return "font-weight: bold;"

            # Formátovanie
            pos_df["Position Value (USD)"] = pos_df["Position Value (USD)"].apply(lambda x: f"{float(x):,.2f}")
            pos_df["Unrealized PnL (USD)"] = pos_df["Unrealized PnL (USD)"].apply(lambda x: f"{float(x):,.2f}")
            pos_df["Size"] = pos_df["Size"].apply(lambda x: f"{float(x):,.6f}")

            styled_df = (
                pos_df.style
                .applymap(style_bold)
                .applymap(style_side, subset=["Side"])
                .applymap(style_pnl, subset=["Unrealized PnL (USD)"])
                .set_table_styles(
                    [
                        {"selector": "th", "props": [("font-weight", "bold"), ("font-size", "16px")]},
                        {"selector": "td", "props": [("font-size", "15px")]}
                    ]
                )
            )

            st.dataframe(styled_df, use_container_width=True)

            # Totals
            total_pnl = sum(float(x["Unrealized PnL (USD)"]) for x in positions)
            total_long_value = sum(x["Position Value (USD)"] for x in positions if x["Side"] == "LONG")
            total_short_value = sum(x["Position Value (USD)"] for x in positions if x["Side"] == "SHORT")

            pnl_color = "green" if total_pnl > 0 else "red" if total_pnl < 0 else "white"
            st.markdown(
                f"<p style='font-weight:bold;font-size:16px;'>"
                f"Long Exposure: <span style='color:green;'>${total_long_value:,.2f}</span> &nbsp;|&nbsp; "
                f"Short Exposure: <span style='color:red;'>${total_short_value:,.2f}</span>"
                f"</p>",
                unsafe_allow_html=True
            )
            st.markdown(
                f"<p style='font-weight:bold;font-size:16px;color:{pnl_color};'>"
                f"Total Unrealized PnL: {total_pnl:+,.2f} USD</p>",
                unsafe_allow_html=True
            )
        else:
            st.info("No open positions.")

        st.markdown("---")
