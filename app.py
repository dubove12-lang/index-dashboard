
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import json
import os
import time
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# === KONŠTANTY ===
BASE_URL = "https://api.hyperliquid.xyz/info"
HEADERS = {"content-type": "application/json"}
REFRESH_INTERVAL = 300  # sekúnd = 5 minút
DASHBOARD_FILE = "dashboards.json"
DATA_DIR = "data"
DELETE_PIN = "6000"  # 🔒 bezpečnostný PIN

st.set_page_config(page_title="Hyperliquid Live Wallet Dashboards", layout="wide")

# === ZABEZPEČI, ŽE EXISTUJE DATA PRIEČINOK ===
os.makedirs(DATA_DIR, exist_ok=True)

# === LOAD / SAVE DASHBOARDY ===
def load_dashboards():
    if os.path.exists(DASHBOARD_FILE):
        with open(DASHBOARD_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_dashboards(dashboards):
    with open(DASHBOARD_FILE, "w") as f:
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
    path = os.path.join(DATA_DIR, f"{name}.csv")
    if os.path.exists(path):
        os.remove(path)
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
        return 0.0  # 👈 tu opravene — vratí 0 namiesto None


# === TRADING VOLUME FUNKCIA ===
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


# === DASHBOARD CREATOR ===
def create_dashboard(name, wallet1, wallet2, volume_start_ts):
    st.session_state.dashboards[name] = {
        "wallets": [wallet1, wallet2],
        "volume_start_ts": volume_start_ts,
        "start_total": 0
    }
    st.session_state.dataframes[name] = pd.DataFrame(columns=["timestamp", "wallet", "value", "total"])
    save_dashboards(st.session_state.dashboards)
    save_dashboard_data(name, st.session_state.dataframes[name])

# === SIDEBAR ===
st.sidebar.header("➕ Add New Dashboard")
name = st.sidebar.text_input("Dashboard name")
w1 = st.sidebar.text_input("Wallet 1 address")
w2 = st.sidebar.text_input("Wallet 2 address")

# výber dátumu a času pre volume
st.sidebar.markdown("#### 📆 Volume Tracking Start")
start_date = st.sidebar.date_input("Start date", datetime.now())
start_time = st.sidebar.time_input("Start time", datetime.now().time())

if st.sidebar.button("Add Dashboard"):
    if not (name and w1 and w2):
        st.sidebar.error("Please fill in all fields!")
    elif name in st.session_state.dashboards:
        st.sidebar.warning(f"Dashboard '{name}' already exists — not added again.")
        st.stop()
    else:
        dt = datetime.combine(start_date, start_time)
        volume_start_ts = int(dt.timestamp() * 1000)
        create_dashboard(name, w1, w2, volume_start_ts)
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
        wallets = info["wallets"]

        # 🔧 fix – konverzia timestampu na int (aby fungoval po refreši)
        start_ts = int(info.get("volume_start_ts", 0)) if info.get("volume_start_ts") else 0

        # načítaj historické dáta
        df = st.session_state.dataframes.get(name)
        if df is None or df.empty:
            df = load_dashboard_data(name)
            st.session_state.dataframes[name] = df

        # Získanie hodnôt walletiek
        values = [get_wallet_value(w) for w in wallets]
        total = sum(values)

        # 🔹 Volume od vybraného času
        vol1 = get_wallet_volume(wallets[0], start_ts)
        vol2 = get_wallet_volume(wallets[1], start_ts)
        total_volume = vol1 + vol2

        # Uloženie nového bodu
        if any(values):
            new_rows = [{"timestamp": pd.Timestamp.now(), "wallet": "total", "value": total, "total": total}]
            for i, w in enumerate(wallets):
                new_rows.append({"timestamp": pd.Timestamp.now(), "wallet": w, "value": values[i], "total": total})
            df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
            st.session_state.dataframes[name] = df
            save_dashboard_data(name, df)

        # Inicializácia štartovej hodnoty
        if info.get("start_total", 0) == 0 and total > 0:
            info["start_total"] = total
            save_dashboards(st.session_state.dashboards)

        # === HORNÝ RIADOK ===
        top_col1, top_col2 = st.columns([6, 1])
        with top_col1:
            st.subheader(f"🧭 {name}")
        with top_col2:
            with st.expander("🗑️ Delete Dashboard", expanded=False):
                pin = st.text_input("Enter PIN to confirm delete", type="password", key=f"pin_{name}")
                if st.button(f"Confirm Delete", key=f"del_{name}"):
                    if pin == DELETE_PIN:
                        delete_dashboard(name)
                    else:
                        st.error("❌ Incorrect PIN. Dashboard not deleted.")

        # === METRIKY NAD GRAFOM ===
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

        # === GRAF ===
        if not df.empty:
            fig = go.Figure()

            for i, w in enumerate(wallets):
                dfi = df[df["wallet"] == w]
                if not dfi.empty:
                    fig.add_trace(go.Scatter(
                        x=dfi["timestamp"], y=dfi["value"],
                        mode="lines+markers", name=f"Wallet {i+1}", line=dict(width=3)
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

        st.markdown("### 💼 Wallets")
        st.markdown(f"**🪙 Wallet 1:** `{wallets[0]}`")
        st.markdown(f"**🪙 Wallet 2:** `{wallets[1]}`")

        st.markdown("---")
