import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import json
import os
from streamlit_autorefresh import st_autorefresh

# === KONŠTANTY ===
BASE_URL = "https://api.hyperliquid.xyz/info"
HEADERS = {"content-type": "application/json"}
REFRESH_INTERVAL = 300  # sekúnd = 5 minút
DASHBOARD_FILE = "dashboards.json"
DATA_DIR = "data"

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
        return None

# === DASHBOARD CREATOR ===
def create_dashboard(name, wallet1, wallet2):
    st.session_state.dashboards[name] = {"wallets": [wallet1, wallet2]}
    st.session_state.dataframes[name] = pd.DataFrame(columns=["timestamp", "wallet", "value", "total"])
    save_dashboards(st.session_state.dashboards)
    save_dashboard_data(name, st.session_state.dataframes[name])

# === SIDEBAR ===
st.sidebar.header("➕ Add New Dashboard")
name = st.sidebar.text_input("Dashboard name")
w1 = st.sidebar.text_input("Wallet 1 address")
w2 = st.sidebar.text_input("Wallet 2 address")

if st.sidebar.button("Add Dashboard"):
    if not (name and w1 and w2):
        st.sidebar.error("Please fill in all fields!")
    elif name in st.session_state.dashboards:
        st.sidebar.warning(f"Dashboard '{name}' already exists — not added again.")
        st.stop()
    else:
        create_dashboard(name, w1, w2)
        st.sidebar.success(f"✅ Dashboard '{name}' created!")

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

        # načítaj historické dáta (ak ešte nie sú v pamäti)
        df = st.session_state.dataframes.get(name)
        if df is None or df.empty:
            df = load_dashboard_data(name)
            st.session_state.dataframes[name] = df

        # Získanie hodnôt walletiek
        values = []
        for w in wallets:
            val = get_wallet_value(w)
            if val:
                values.append(val)

        total = sum(values) if len(values) == 2 else 0

        # Uloženie nového bodu
        if len(values) == 2:
            new_rows = []
            new_rows.append({"timestamp": pd.Timestamp.now(), "wallet": "total", "value": total, "total": total})
            for i, w in enumerate(wallets):
                new_rows.append({"timestamp": pd.Timestamp.now(), "wallet": w, "value": values[i], "total": total})
            df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
            st.session_state.dataframes[name] = df
            save_dashboard_data(name, df)  # uložiť priebežne

        # Inicializácia štartovej hodnoty
        if "start_total" not in info or info["start_total"] == 0:
            info["start_total"] = total
            save_dashboards(st.session_state.dashboards)

        pct_change = ((total - info["start_total"]) / info["start_total"]) * 100 if info["start_total"] else 0

        # === HORNÝ RIADOK ===
        top_col1, top_col2 = st.columns([6, 1])
        with top_col1:
            st.subheader(f"🧭 {name}")
        with top_col2:
            if st.button(f"🗑️ Delete", key=f"del_{name}"):
                delete_dashboard(name)

        # === TRI METRIKY NAD GRAFOM ===
        if info.get("start_total", 0) > 0:
            start_val = info["start_total"]
            curr_val = total
            pct_change = ((curr_val - start_val) / start_val) * 100

            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("💰 Start Value (USD)", f"${start_val:,.2f}")
            with m2:
                st.metric("📈 Current Value (USD)", f"${curr_val:,.2f}")
            with m3:
                st.metric("📊 Change (%)", f"{pct_change:+.2f}%")
        else:
            st.warning("No valid data yet to compute metrics.")

        # === GRAF ===
        if not df.empty:
            fig = go.Figure()

            # Wallet 1
            df1 = df[df["wallet"] == wallets[0]]
            if not df1.empty:
                fig.add_trace(go.Scatter(
                    x=df1["timestamp"], y=df1["value"],
                    mode="lines+markers", name="Wallet 1", line=dict(width=3)
                ))

            # Wallet 2
            df2 = df[df["wallet"] == wallets[1]]
            if not df2.empty:
                fig.add_trace(go.Scatter(
                    x=df2["timestamp"], y=df2["value"],
                    mode="lines+markers", name="Wallet 2", line=dict(width=3)
                ))

            # Total
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
                xaxis=dict(
                    title=dict(text="Time", font=dict(size=18)),
                    tickfont=dict(size=14)
                ),
                yaxis=dict(
                    title=dict(text="USD Value", font=dict(size=18)),
                    tickfont=dict(size=14)
                ),
                legend=dict(font=dict(size=14)),
            )

            st.plotly_chart(fig, use_container_width=True)

            # WALLET INFO
            st.markdown("### 💼 Wallets")
            st.markdown(f"**🪙 Wallet 1:** `{wallets[0]}`")
            st.markdown(f"**🪙 Wallet 2:** `{wallets[1]}`")

        st.markdown("---")

