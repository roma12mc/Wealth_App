import streamlit as st
import json
import math
import os
from math import ceil
from datetime import datetime, timedelta, date
import pandas as pd
import plotly.express as px
import cohere

cohere_api_key = st.secrets["Textgen"]
co = cohere.Client(cohere_api_key)

st.set_page_config(
    page_title="Wealthy",
    page_icon="🏦",
    layout="centered",
    initial_sidebar_state="auto"
)

# ----------------------
# Basic JSON helpers
# ----------------------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ----------------------
# App state rerun helper
# ----------------------
if "needs_rerun" in st.session_state and st.session_state.needs_rerun:
    st.session_state.needs_rerun = False
    st.rerun()

# --- App state setup ---
if "onboarded" not in st.session_state:
    st.session_state.onboarded = False
if "user_data" not in st.session_state:
    st.session_state.user_data = {"vision": "", "goals": "", "relationship": ""}

# --- Files ---
USER_FILE = "user_profile.json"
GOALS_FILE = "goals.json"
ACCOUNTS_FILE = "accounts.json"
TRANSACTIONS_FILE = "transactions.json"
STANDING_ORDERS_FILE = "standing_orders.json"
AUTO_SPLIT_FILE = "auto_split.json"
BADGES_FILE = "badges.json"
STATS_FILE = "stats.json"
REMINDERS_FILE = "reminders.json"  # kept for future
REPORTS_FILE = "monthly_reports.json"

def load_reports():
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_reports(reports):
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2)
# --- User helpers ---
def load_user():
    return load_json(USER_FILE, None)

def save_user(user):
    save_json(USER_FILE, user)

def update_user_profile(new_data: dict):
    """Update user profile data safely and prepare for AI integration."""
    user_data = st.session_state.get("user_data", {})
    user_data.update(new_data)
    st.session_state.user_data = user_data

# --- Goals helpers ---
def load_goals():
    return load_json(GOALS_FILE, [])

def save_goals(goals):
    save_json(GOALS_FILE, goals)

# --- Accounts helpers ---
def save_accounts(accounts):
    save_json(ACCOUNTS_FILE, accounts)

def load_accounts():
    data = load_json(ACCOUNTS_FILE, [])
    if not isinstance(data, list):
        return []
    for acc in data:
        acc.setdefault("balance", 0.0)
        acc.setdefault("allocated", 0.0)
        acc.setdefault("name", "Unnamed")
    return data

# --- Auto-split helpers ---
def load_auto_split():
    return load_json(AUTO_SPLIT_FILE, {"enabled": False, "ratios": {}})

def save_auto_split(data):
    save_json(AUTO_SPLIT_FILE, data)

def update_streak():
    today = datetime.now().date()
    last_date = st.session_state.last_visit_date

    if last_date is None:
        # First app open - start streak at 1
        st.session_state.current_streak = 1
        st.session_state.last_visit_date = today
        return

    days_diff = (today - last_date).days

    if days_diff == 0:
        # Already visited today, no change
        return
    elif days_diff == 1:
        # Consecutive day, increment streak
        st.session_state.current_streak += 1
        st.session_state.last_visit_date = today
    else:
        # Missed day(s), reset streak
        st.session_state.current_streak = 1
        st.session_state.last_visit_date = today

# --- Transactions helpers ---
def load_transactions():
    txs = load_json(TRANSACTIONS_FILE, [])
    # backcompat: ensure fields
    for tx in txs:
        tx.setdefault("type", "Expense")
        tx.setdefault("account", "Main (Needs)")
        tx.setdefault("category", "Other")
        tx.setdefault("note", "")
        tx.setdefault("timestamp", datetime.now().isoformat())
        tx["amount"] = float(tx.get("amount", 0.0))
    return txs

def save_transactions(txs):
    save_json(TRANSACTIONS_FILE, txs)

# Apply / revert (basic single-account version; auto-split handled separately where used)
def apply_transaction_simple(tx, accounts):
    acc_name = tx.get("account", "Main (Needs)")
    amt = float(tx.get("amount", 0.0))
    acc = next((a for a in accounts if a["name"] == acc_name), None)
    if acc is None:
        return False, f"Account '{acc_name}' not found."
    if tx.get("type", "Expense") == "Income":
        acc["balance"] = float(acc.get("balance", 0.0)) + amt
    else:
        if float(acc.get("balance", 0.0)) < amt:
            return False, f"Insufficient funds in {acc_name}."
        acc["balance"] = float(acc.get("balance", 0.0)) - amt
    save_accounts(accounts)
    return True, "applied"

def revert_transaction_simple(tx, accounts):
    acc_name = tx.get("account", "Main (Needs)")
    amt = float(tx.get("amount", 0.0))
    acc = next((a for a in accounts if a["name"] == acc_name), None)
    if acc is None:
        return False, f"Account '{acc_name}' not found for revert."
    if tx.get("type", "Expense") == "Income":
        acc["balance"] = float(acc.get("balance", 0.0)) - amt
    else:
        acc["balance"] = float(acc.get("balance", 0.0)) + amt
    save_accounts(accounts)
    return True, "reverted"

def format_euro(amount: float) -> str:
    """
    Formats a float as a Euro currency string.
    Example: 1234.5 -> '€1,234.50'
    """
    try:
        # Ensure amount is numeric
        amount = float(amount)
        # Format with two decimals and thousands separator
        return f"€{amount:,.2f}"
    except (ValueError, TypeError):
        return "€0.00"
    
def build_prompt(vision, goals, relationship):
    return (
        f"My vision: {vision}\n"
        f"My financial goals: {goals}\n"
        f"My relationship to money: {relationship}\n"
        "Please provide 3 short, clear, and motivating financial tips, "
        "each tip exactly one sentence, formatted in a numbered list like this:\n"
        "1) Tip one.\n"
        "2) Tip two.\n"
        "3) Tip three."
    )


def get_personalized_tips(prompt):
    response = co.chat(
        model="command-a-03-2025",
        message=prompt,
        max_tokens=150,
        temperature=0.7,
    )
    return response.text.strip()

# --- Try to load existing profile at startup (so onboarding is skipped if file exists) ---
_existing = load_user()
if _existing:
    st.session_state.user_data = _existing
    st.session_state.onboarded = True

# --- Sidebar navigation ---
if "current_page" not in st.session_state:
    st.session_state.current_page = "Profile"
st.sidebar.markdown("🏦  **Wealthy** ")
page = st.sidebar.radio(
    "Navigation",
    ["Profile", "Goals", "Dashboard", "Transactions", "Accounts", "Reports"],
    index=["Profile", "Goals", "Dashboard", "Transactions", "Accounts", "Reports"].index(st.session_state.current_page),
)

if page != st.session_state.current_page:
    st.session_state.current_page = page

# --- Onboarding ---
def onboarding():
    st.title("Welcome to Wealthy 🏦")
    st.write("**Before we start, answer three quick questions to personalize your experience:**")
    st.caption("*By sharing your vision and financial goals, Wealthy can tailor tips, insights, and challenges to help unlock your unique path to financial independence faster.*")
    
    vision = st.text_input(
        "1️⃣ What do you hope to achieve with this app?",
        placeholder="My dream is to be financially independent"
    )

    goals_text = st.text_area(
        "2️⃣ What are your 3 main financial goals?",
        placeholder="Use a numbered list like 1) Buy a house. 2) Retire at 60. 3) Invest $200/month."
        
    )

    relationship = st.text_input(
        "3️⃣ What do you want your relationship to money to be like?",
        placeholder="Healthy, mindful, and stress-free"
    )

    if st.button("Save & Continue"):
        if not vision or not goals_text or not relationship:
            st.error("Please fill out all fields before continuing.")
        else:
            prompt = build_prompt(vision, goals_text, relationship)
            ai_tips = get_personalized_tips(prompt)
            user = {
                "vision": vision,
                "goals": goals_text,
                "relationship": relationship,
                "personalized_tips": ai_tips.split('\n'),
                "badges": []
            }
            st.session_state.user_data = user
            save_user(user)
            st.session_state.onboarded = True
            st.success("🎉 Onboarding complete! Here are some personalized tips:")
            st.title("Starter Tips:")
            for tip in st.session_state.user_data["personalized_tips"]:
                st.write("- " + tip)

# ---------------------------
# Notification / Badges (Day 11)
# ---------------------------
def load_badges():
    return load_json(BADGES_FILE, {})

def save_badges(b):
    save_json(BADGES_FILE, b)

def load_stats():
    return load_json(STATS_FILE, {"longest_streak":0, "goals_completed":0})

def save_stats(s):
    save_json(STATS_FILE, s)

def goal_daily_totals(goal_name, transactions):
    daily = {}
    for tx in transactions:
        goal_field = tx.get("goal") or ""
        note = tx.get("note","")
        # Simple heuristic: tx directly targeted the goal if tx["goal"] == goal_name or note contains pattern
        if goal_field == goal_name or f"[Goal] {goal_name}" in note:
            try:
                d = datetime.fromisoformat(tx["timestamp"]).date()
            except Exception:
                d = date.today()
            daily[d] = daily.get(d, 0) + float(tx.get("amount",0))
    return daily

def compute_streak_for_goal(goal_name, transactions):
    daily = goal_daily_totals(goal_name, transactions)
    if not daily:
        return 0, None
    today_dt = date.today()
    streak = 0
    current = today_dt
    last_date = None
    while True:
        if current in daily:
            streak += 1
            last_date = current
            current = current - timedelta(days=1)
        else:
            break
    return streak, last_date

def award_badge(badges, badge_id, title, description):
    if badge_id not in badges:
        badges[badge_id] = {"title": title, "description": description, "awarded_at": datetime.now().isoformat()}
        save_badges(badges)
        st.success(f"🏆 Badge unlocked: {title} — {description}")
        st.balloons()

def gather_nudges(transactions, goals):
    nudges = []
    # 1) Weekly inactivity
    last_week_cutoff = date.today() - timedelta(days=7)
    had_recent = False
    for tx in transactions:
        try:
            d = datetime.fromisoformat(tx["timestamp"]).date()
            if d >= last_week_cutoff:
                had_recent = True
                break
        except:
            continue
    if not had_recent:
        nudges.append({
            "type": "no_activity_week",
            "message": "You haven’t logged any transactions in the last 7 days. Even small check-ins matter.",
            "action_label": "Add Transaction",
            "action": {"open_tab": "Transactions"}
        })

    # 2) Overspending nudge
    expenses = [tx for tx in transactions if tx.get("type") == "Expense"]
    if expenses:
        weekly_totals = {}
        for tx in expenses:
            try:
                d = datetime.fromisoformat(tx["timestamp"]).date()
            except:
                continue
            week = d.isocalendar()[1]
            weekly_totals[week] = weekly_totals.get(week, 0) + float(tx.get("amount",0))
        if weekly_totals:
            avg_week = sum(weekly_totals.values()) / len(weekly_totals)
            this_week = date.today().isocalendar()[1]
            this_week_total = weekly_totals.get(this_week, 0)
            if avg_week > 0 and this_week_total > avg_week * 3:
                nudges.append({
                    "type": "overspending",
                    "message": f"This week’s expenses (€{this_week_total:.2f}) are much higher than your average (€{avg_week:.2f}).",
                    "action_label": "Open Dashboard",
                    "action": {"open_tab": "Dashboard"}
                })

    # 3) Goal inactivity simplified (7 days)
    for g in goals:
        gname = g.get("name")
        streak, last = compute_streak_for_goal(gname, transactions)
        last_date = last or None
        days_since = (date.today() - last_date).days if last_date else None
        if days_since is None or days_since >= 7:
            nudges.append({
                "type": "missed_contribution",
                "message": f"You haven’t contributed to '{gname}' for {days_since if days_since else 'many'} days. Keep momentum!",
                "goal": gname,
                "action_label": "Log Contribution",
                "action": {"open_tab": "Transactions"}
            })

    return nudges

def update_goal_streaks_and_badges(transactions, goals, badges):
    updated = False
    for g in goals:
        gname = g.get("name")
        streak, last = compute_streak_for_goal(gname, transactions)
        if streak != g.get("streak_count", 0):
            g["streak_count"] = streak
            g["last_contribution_date"] = last.isoformat() if last else None
            updated = True
        if streak >= 7:
            award_badge(badges, f"streak_7_{gname}", "7-Day Saver", f"7-day streak for '{gname}'")
        if streak >= 30:
            award_badge(badges, f"streak_30_{gname}", "1-Month Streak", f"30-day streak for '{gname}'")
    if updated:
        save_goals(goals)

def check_goal_completion_badges(transactions, goals, badges):
    completed = [g for g in goals if float(g.get("current",0)) >= float(g.get("target",1e9))]
    if len(completed) >= 3:
        award_badge(badges, "three_goals", "Goal Trifecta", "Completed 3 goals")
    for g in completed:
        award_badge(badges, f"completed_{g['name']}", "Goal Achieved", f"You completed '{g['name']}'!")

def show_notifications_and_badges_on_dashboard():
    # load dynamic data
    transactions = load_transactions()
    goals = load_goals()
    badges = load_badges()
    # update streaks & badges
    update_goal_streaks_and_badges(transactions, goals, badges)
    check_goal_completion_badges(transactions, goals, badges)
    nudges = gather_nudges(transactions, goals)

    # Render badges and nudges in a compact expander on Dashboard
    with st.expander("🔔 Notifications & 🏅 Badges", expanded=False):
        # badges row (compact)
        if badges:
            st.markdown("**Badges:**")
            cols = st.columns(4)
            i = 0
            for bid, bdata in badges.items():
                col = cols[i % 4]
                with col:
                    st.markdown(f"**{bdata['title']}**")
                    st.caption(bdata['description'])
                i += 1
            st.write("---")

        if nudges:
            for n in nudges:
                st.warning(n["message"])
                if st.button(n["action_label"], key=f"nudge_{n['type']}_{n.get('goal','')}"):
                    target = n["action"].get("open_tab")
                    if target:
                        st.session_state.current_page = target
                        st.rerun()
        else:
            st.info("✅ No nudges right now — you're on track!")
# ---------------------------
# Main app pages
# ---------------------------

# Onboarding / Home
if not st.session_state.onboarded:
    onboarding()
else:
    # Profile page
    if page == "Profile":
        st.markdown("<h1 style='text-align:center;'>💼 Your Wealthy Profile</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center;'>Track, view, or update your ultimate vision, goals, and money mindset here.</p>", unsafe_allow_html=True)
        st.write("You can view or edit your answers here:")
        user = load_user() or st.session_state.user_data
        vision_val = user.get("vision", "")
        goals_val = user.get("goals", "")
        relationship_val = user.get("relationship", "")
        with st.form("profile_form"):
            vision = st.text_input("Your Vision", value=vision_val)
            goals_text = st.text_area("Your 3 Main Goals", value=goals_val)
            relationship = st.text_input("Your Relationship to Money", value=relationship_val)
            submitted = st.form_submit_button("Update Profile")
            if submitted:
                new_user = {"vision": vision, "goals": goals_text, "relationship": relationship, "personalized_tips": [],         # list to store AI-generated tips
                            "badges": [] }
                save_user(new_user)
                st.session_state.user_data = new_user
                st.success("🎉 Your Wealthy profile was updated — keep building your path to financial freedom!")

    if page == "Goals":
    

    # Centered title and intro
        st.markdown("<h1 style='text-align:center;'>Goals & Progress</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center;'>Create goals with a target amount, add contributions, and watch your consistency build momentum 🔥</p>", unsafe_allow_html=True)

        goals = load_goals()
        accounts = load_json("accounts.json", [])
        if not isinstance(accounts, list):
            accounts = []

        active_goals = [g for g in goals if float(g.get("current", 0)) < float(g.get("target", 0))]
        achieved_goals = [g for g in goals if float(g.get("current", 0)) >= float(g.get("target", 0))]

        if "show_create_goal" not in st.session_state:
            st.session_state.show_create_goal = False
        if "show_achieved_goals" not in st.session_state:
            st.session_state.show_achieved_goals = False

    # Active Goals section
        st.subheader("🎯 Active Goals")
        if active_goals:
            for i, g in enumerate(active_goals):
                name = g.get("name", "Unnamed")
                target = float(g.get("target", 0))
                current = float(g.get("current", 0))
                pct = (current / target) if target > 0 else 0
                pct_display = min(1.0, pct)

                st.markdown(f"### {name} — {format_euro(current)} / {format_euro(target)}")
                st.progress(pct_display)

                streak = g.get("streak_count", 0)
                milestones_hit = g.get("milestones_hit", [])

                if streak >= 1:
                    st.markdown(f"🔥 **Active streak:** {streak} day(s)")
                else:
                    st.markdown("💤 No active streak yet — stay consistent!")

                if milestones_hit:
                    st.markdown("🏆 **Milestones achieved:** " + ", ".join([f"{m}%" for m in milestones_hit]))

                if target > 0 and current < target:
                    remaining = max(0, target - current)
                    monthly = math.ceil(remaining / 3) if remaining > 0 else 0
                    st.write(f"Remaining: {format_euro(remaining)} — suggestion: **{format_euro(monthly)}/month** for 3 months.")
                elif target > 0 and current >= target:
                    st.success("Goal achieved — celebrate and set the next one! 🎉")

            # Manage Goal expander and editing unchanged here...

                # --- Manage Goal Section ---
            with st.expander("⚙️ Manage Goal"):
                st.write("Adjust your progress or update details:")

                # Contribution Row
                col_contrib1, col_contrib2 = st.columns([3, 1])
                with col_contrib1:
                    add_amount_str = st.text_input(
                        "Add contribution (€)",
                        placeholder="e.g. 12,26 or 12.26",
                        key=f"add_str_{i}"
                    )
                with col_contrib2:
                    if st.button("➕", key=f"add_btn_{i}"):
                        try:
                            add_amount = float(add_amount_str.replace(",", ".").strip())
                            current_value = float(goals[i].get("current", 0))
                            new_total = current_value + add_amount
                            goals[i]["current"] = new_total

                            # Handle Streaks
                            today = datetime.now().date()
                            last_date_str = goals[i].get("last_contribution_date")
                            last_date = datetime.fromisoformat(last_date_str).date() if last_date_str else None

                            if last_date == today - timedelta(days=1):
                                goals[i]["streak_count"] = goals[i].get("streak_count", 0) + 1
                            elif last_date == today:
                                pass
                            else:
                                goals[i]["streak_count"] = 1

                            goals[i]["last_contribution_date"] = today.isoformat()

                            # Allocate money (visual only)
                            alloc_acc = goals[i].get("allocated_from")
                            for acc in accounts:
                                if acc["name"] == alloc_acc:
                                    acc["allocated"] = acc.get("allocated", 0) + add_amount
                                    break
                            save_json("accounts.json", accounts)

                            # Handle Milestones
                            progress_pct = (new_total / float(goals[i]["target"])) * 100 if goals[i]["target"] > 0 else 0
                            milestones_hit = goals[i].get("milestones_hit", [])
                            new_milestones = []

                            for m in [25, 50, 75, 100]:
                                if progress_pct >= m and m not in milestones_hit:
                                    milestones_hit.append(m)
                                    new_milestones.append(m)

                            goals[i]["milestones_hit"] = milestones_hit

                            # Aggregate History (add all daily contributions)
                            history = goals[i].get("history", [])
                            today_str = today.isoformat()
                            found = False
                            for h in history:
                                if h["date"] == today_str:
                                    h["amount"] = float(h["amount"]) + add_amount
                                    found = True
                                    break
                            if not found:
                                history.append({"date": today_str, "amount": add_amount})
                            goals[i]["history"] = history

                            # Save + Feedback
                            save_goals(goals)
                            save_json("accounts.json", accounts)
                            if new_milestones:
                                st.success(f"🎉 You hit a milestone: {', '.join([str(m)+'%' for m in new_milestones])}!")
                                st.balloons()
                            else:
                                st.success(f"Allocated €{add_amount:.2f} from {alloc_acc} to {name}.")

                            st.session_state.needs_rerun = True

                        except Exception:
                            st.error("Please enter a valid amount, e.g. 12,26 or 12.26")

                st.divider()

                # Editable Fields
                edit_name = st.text_input("✏️ Edit name", value=name, key=f"edit_name_{i}")
                edit_account = st.selectbox("🏦 Allocate from account",
                                           [a["name"] for a in accounts] if accounts else ["No accounts available"],
                                           key=f"edit_acc_{i}",
                                           index=[a["name"] for a in accounts].index(g.get("allocated_from")) if g.get("allocated_from") in [a["name"] for a in accounts] else 0)
                edit_target = st.number_input(
                    "🎯 Edit target (€)",
                    min_value=0.0,
                    value=target,
                    step=10.0,
                    format="%.2f",
                    key=f"edit_target_{i}",
                )

                # Button Row
                col1, col2, col3 = st.columns([1, 1, 1])
                with col1:
                    if st.button("↩️ Reset", key=f"reset_{i}"):
                        alloc_acc = goals[i].get("allocated_from")
                        current_value = goals[i].get("current", 0)
                        for acc in accounts:
                            if acc["name"] == alloc_acc:
                                acc["allocated"] = max(0, acc.get("allocated", 0) - current_value)
                                break
                        goals[i]["current"] = 0.0
                        save_goals(goals)
                        save_json("accounts.json", accounts)
                        st.success("Progress reset.")
                        st.session_state.needs_rerun = True
                with col2:
                    if st.button("🗑️ Delete", key=f"delete_{i}"):
                        alloc_acc = goals[i].get("allocated_from")
                        current_value = goals[i].get("current", 0)
                        for acc in accounts:
                            if acc["name"] == alloc_acc:
                                acc["allocated"] = max(0, acc.get("allocated", 0) - current_value)
                                break
                        goals.pop(i)
                        save_goals(goals)
                        save_json("accounts.json", accounts)
                        st.success("Goal deleted.")
                        st.session_state.needs_rerun = True
                with col3:
                    if st.button("💾 Save", key=f"save_{i}"):
                        goals[i]["name"] = edit_name
                        goals[i]["allocated_from"] = edit_account
                        goals[i]["target"] = float(edit_target)
                        save_goals(goals)
                        save_json("accounts.json", accounts)
                        st.success("Goal updated.")
                        st.session_state.needs_rerun = True

        else:
            st.info("No active goals. You can create one below!")

    

        st.divider()

    # Buttons side by side controlling views
        col1, col2 = st.columns(2)
        with col1:
            if st.button("➕ Create a new Goal"):
                st.session_state.show_create_goal = True
                st.session_state.show_achieved_goals = False
        with col2:
            if st.button("🏁 View achieved Goals"):
                st.session_state.show_achieved_goals = True
                st.session_state.show_create_goal = False

    # Show create goal form and achieved goals list side-by-side if both selected
        if st.session_state.show_create_goal or st.session_state.show_achieved_goals:
            left_col, right_col = st.columns(2)
            if st.session_state.show_create_goal:
                with left_col:
                    with st.form("add_goal_form"):
                        st.subheader("*Create new Goal:*")
                        name = st.text_input("Goal name (e.g., 'Emergency Fund')")
                        account_choice = st.selectbox(
                            "Allocate from account",
                            [a["name"] for a in accounts] if accounts else ["No accounts available"]
                        )
                        target = st.number_input("Target amount (€)", min_value=0.0, value=500.0, step=10.0, format="%.2f")
                        submitted = st.form_submit_button("Add Goal")
                        if submitted:
                            new_goal = {
                                "name": name or "Untitled Goal",
                                "target": float(target),
                                "current": 0.0,
                                "allocated_from": account_choice,
                                "streak_count": 0,
                                "last_contribution_date": None,
                                "milestones_hit": [],
                                "history": []
                            }
                            goals.append(new_goal)
                            save_goals(goals)
                            st.success(f"Goal '{new_goal['name']}' added.")
                            st.session_state.needs_rerun = True
            if st.session_state.show_achieved_goals:
                with right_col:
                        st.subheader("🏁 Achieved Goals")
                        if not achieved_goals:
                            st.info("No goals achieved yet.")
                        else:
                            for i, g in enumerate(achieved_goals):
                                name = g.get("name", "Unnamed")
                                target = float(g.get("target", 0))
                                current = float(g.get("current", 0))
                                st.success(f"✅ {name} — {format_euro(target)} reached!")

                                edit_name = st.text_input("✏️ Edit name", value=name, key=f"ach_edit_name_{i}")
                                edit_target = st.number_input(
                                "🎯 Edit target (€)",
                                min_value=0.0,
                                value=target,
                                step=10.0,
                                format="%.2f",
                                key=f"ach_edit_target_{i}",
                            )
                            edit_account = st.selectbox("🏦 Allocate from account",
                                                    [a["name"] for a in accounts] if accounts else ["No accounts available"],
                                                    key=f"ach_edit_acc_{i}",
                                                    index=[a["name"] for a in accounts].index(g.get("allocated_from")) if g.get("allocated_from") in [a["name"] for a in accounts] else 0)
    
                            c1, c2 = st.columns(2)
                            with c1:
                                if st.button("🗑️ Delete", key=f"ach_delete_{i}"):
                                    goals.remove(g)
                                    save_goals(goals)
                                    st.success("Goal deleted.")
                                    st.session_state.needs_rerun = True
                                    
                            with c2:
                                if st.button("💾 Save", key=f"ach_save_{i}"):
                                    g["name"] = edit_name
                                    g["target"] = float(edit_target)
                                    g["allocated_from"] = edit_account
                                    save_goals(goals)
                                    st.success("Goal updated.")
                                    st.session_state.needs_rerun = True
                                
    # Dashboard page (includes notifications & existing dashboard visuals)
    if page == "Dashboard":
        # Load your actual data using your helpers:
        transactions = load_transactions()
        accounts = load_accounts()
        goals = load_goals()

# Prepare monthly income vs expense summary
        if transactions:
            df_tx = pd.DataFrame(transactions)
            df_tx['timestamp'] = pd.to_datetime(df_tx['timestamp'])
            df_tx['month'] = df_tx['timestamp'].dt.to_period('M')
            monthly_summary = df_tx.groupby(['month', 'type'])['amount'].sum().unstack(fill_value=0)
        else:
            monthly_summary = pd.DataFrame()
        # Run notifications & badges at top
        show_notifications_and_badges_on_dashboard()
        st.markdown("<h1 style='text-align:center;'>💼 Wealth Dashboard</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center;'>Your personal finance cockpit — overview, insights, and progress at a glance.</p>", unsafe_allow_html=True)
        
        # Load data for dashboard visuals
        transactions = load_transactions()
        accounts = load_accounts()
        goals = load_goals()

    # Aggregate Data safe guards
        total_contributed = 0.0
        total_target = 0.0
        total_current = 0.0
        streaks = []
        data = []

    # DataFrame guard for contributions
        if data:
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
        else:
            df = None

        # Calculate total balances and allocation % for Wealth Index
        total_balance = sum(acc.get("balance", 0) for acc in accounts)
        total_allocated = sum(acc.get("allocated", 0) for acc in accounts)
        percent_allocated = (total_allocated / total_balance * 100) if total_balance > 0 else 0
        
          # Monthly profit and loss calculation from transactions
        profit_margin = 0
        if transactions:
            df_tx = pd.DataFrame(transactions)
            df_tx['month'] = pd.to_datetime(df_tx['timestamp']).dt.to_period('M')
            monthly_summary = df_tx.groupby(['month', 'type'])['amount'].sum().unstack(fill_value=0)
            monthly_summary['Profit'] = monthly_summary.get('Income', 0) - monthly_summary.get('Expense', 0)

        col1, col2, col3, col4 = st.columns(4)
        
        # Assuming transactions is a list/dict of your transaction data already loaded
        df_tx = pd.DataFrame(transactions)
        df_tx['timestamp'] = pd.to_datetime(df_tx['timestamp'])

# Find last Sunday (end of last week)
        today = datetime.now().date()
        last_sunday = today - timedelta(days=today.weekday() + 1)

# Calculate cumulative balance at end of last week
        def signed_amount(row):
            return row['amount'] if row['type'] == 'Income' else -row['amount']

        df_tx['signed_amount'] = df_tx.apply(signed_amount, axis=1)
        prev_balance = df_tx[df_tx['timestamp'].dt.date <= last_sunday]['signed_amount'].sum()

# Current total balance from your accounts list
        current_balance = sum(acc.get("balance", 0.0) for acc in accounts)

# Calculate delta compared to end of last week
        delta_balance = current_balance - prev_balance
# Example deltas for illustration (replace with actual logic)
        col1.metric("💰 Total Balance", f"€{current_balance:,.2f}", delta=f"€{delta_balance:,.2f}")

        col2.metric("📊 Percent Allocated", f"{percent_allocated:.1f}%", delta="")  # no delta for now

# Monthly profit margin calculation with safe column access
        if not monthly_summary.empty:
            this_month = pd.Period(pd.Timestamp.now(), freq='M')
            if this_month in monthly_summary.index:
                monthly_income = monthly_summary.loc[this_month].get('Income', 0)
                monthly_expense = monthly_summary.loc[this_month].get('Expense', 0)
                monthly_profit = monthly_income - monthly_expense
                profit_margin = (monthly_profit / monthly_income * 100) if monthly_income > 0 else 0
            else:
                profit_margin = 0.0
        else:
            profit_margin = 0.0
        col3.metric("📈 Monthly Profit Margin", f"{profit_margin:.1f}%", delta="")
        
        # Load these from your persistent user data or initialize
        if "last_visit_date" not in st.session_state:
            st.session_state.last_visit_date = None
        if "current_streak" not in st.session_state:
            st.session_state.current_streak = 0


# Call the update function once per app run
        update_streak()
# Current streak length calculation (example; adapt your logic)
        col4.metric("🔥 Current Streak (days)", st.session_state.current_streak)
        
        # Helper function to normalize values between 0 and 1
        def normalize(val, max_val):
            if max_val == 0:
                return 0
            return min(val / max_val, 1.0)

# Metrics calculated elsewhere in your app:
        transactions_count = len(transactions)
        avg_goal_completion = 0
        if goals:
            avg_goal_completion = sum(min(float(g.get("current", 0)) / max(float(g.get("target", 1)), 1), 1.0) for g in goals) / len(goals)
        profit_margin_positive = max(profit_margin, 0) / 100  # scale 0..1, treat negative as 0
        streak_bonus = min(st.session_state.current_streak, 30)  # capped at 30 days

# Compute Wealth Index as additive weighted sum
        wealth_index = (
            10 +                     # base value to avoid zero start
            0.05 * transactions_count +
            20 * avg_goal_completion +
            40 * profit_margin_positive +
            5 * streak_bonus
        )
        wealth_index = min(wealth_index, 100)  # cap at 100

# Define tier and colors
        if wealth_index >= 85:
            tier = "Platinum"
            color = "#7300ff"
        elif wealth_index >= 65:
            tier = "Gold"
            color = "#ffd700"
        elif wealth_index >= 40:
            tier = "Silver"
            color = "#c0c0c0"
        else:
            tier = "Bronze"
            color = "#cd7f32"

# Display Wealth Index card with color and styling
        wealth_card_html = f"""
        <div style="
            background-color: {color};
            color: black;
            padding: 20px;
            border-radius: 15px;
            text-align: center;
            font-size: 28px;
            font-weight: 700;
            box-shadow: 2px 2px 10px rgba(0,0,0,0.3);
            margin-bottom: 20px;
            user-select: none;
            cursor: default;
        ">
            🏆 Wealth Index: {wealth_index:.1f} / 100<br>
            <small>{tier} Tier</small>
        </div>
        """

        st.markdown(wealth_card_html, unsafe_allow_html=True)

# Expander with detailed explanation
        with st.expander("ℹ️ What is the Wealth Index? Click to learn more", expanded=False):
            st.write("""
            The Wealth Index is a progressive score that grows with your personal finance journey, rewarding consistency, progress, and positive results:
    
            - **Transactions**: Every transaction you log adds to your score, encouraging regular engagement.
            - **Goal Completion**: Progress on your financial goals increases your Wealth Index.
            - **Profit Margin**: Positive monthly profit margin strongly boosts your score.
            - **Consistency Streak**: Maintaining a streak for up to 30 days gives you a bonus.
    
            This index is designed to grow steadily and fairly, without harsh penalties for occasional misses. It's capped at 100 for simplicity and divided into tiers (Bronze, Silver, Gold, Platinum) to motivate you towards higher achievements.
            """)
 
        for g in goals:
            total_target += float(g.get("target", 0))
            total_current += float(g.get("current", 0))
            streaks.append(g.get("streak_count", 0))
            for h in g.get("history", []):
                data.append({
                    "goal": g["name"],
                    "date": h.get("date"),
                    "amount": h.get("amount", 0)
                })
                total_contributed += float(h.get("amount", 0))  
    
            

            st.subheader("📊 Monthly Income and Expense Overview")
            st.dataframe(monthly_summary.style.format({"Income": "€{:.2f}", "Expense": "€{:.2f}", "Profit": "€{:.2f}"}))

            this_month = pd.Period(pd.Timestamp.now(), freq='M')
            monthly_income = monthly_summary.at[this_month, 'Income'] if this_month in monthly_summary.index else 0
            monthly_profit = monthly_summary.at[this_month, 'Profit'] if this_month in monthly_summary.index else 0
            profit_margin = (monthly_profit / monthly_income * 100) if monthly_income > 0 else 0

        if goals:
            df_goals = pd.DataFrame({
                "Goal": [g["name"] for g in goals],
                "Progress": [(float(g["current"]) / float(g["target"]) * 100) if float(g.get("target",0)) > 0 else 0 for g in goals]
            })

            fig = px.bar(df_goals, x="Goal", y="Progress", text_auto='.1f', 
                         title="Goal Completion (%)", range_y=[0,100],
                         labels={"Progress": "Progress (%)"})
            fig.update_traces(marker_color='mediumseagreen')
            fig.update_layout(yaxis=dict(ticksuffix="%"), showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
   
    if page == "Accounts":
    

        st.markdown("<h1 style='text-align:center;'>🏦 Account Overview</h1>", unsafe_allow_html=True)
        st.markdown(
            "<p style='text-align:center;'>Manage your accounts, allocations, and internal transfers.</p>",
            unsafe_allow_html=True,
        )

    # Load data
        accounts = load_json("accounts.json", [])

    # Compute totals
        total_balance = sum(a["balance"] for a in accounts)
        total_allocated = sum(a.get("allocated", 0.0) for a in accounts)
        total_free = total_balance - total_allocated

    # Summary metrics
        col1, col2, col3 = st.columns(3)
        col1.metric("💰 Total Balance", f"€{total_balance:,.2f}")
        col2.metric("📊 Allocated", f"€{total_allocated:,.2f}")
        col3.metric("🪙 Free", f"€{total_free:,.2f}")

        st.write("---")

    # Accounts grid
        st.subheader("Accounts")
        acc_cols = st.columns(len(accounts))
        for idx, acc in enumerate(accounts):
            with acc_cols[idx]:
                free_amount = acc["balance"] - acc.get("allocated", 0.0)
                st.markdown(
                    f"""
                    <div style='text-align:center; border:1px solid #444; border-radius:12px; padding:10px;'>
                        <h4>{acc['name']}</h4>
                        <p><b>Balance:</b> €{acc['balance']:,.2f}</p>
                        <p><b>Free:</b> €{free_amount:,.2f}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        st.write("---")

    # Session state for showing forms
        if "show_create_account" not in st.session_state:
            st.session_state.show_create_account = False
        if "show_edit_account" not in st.session_state:
            st.session_state.show_edit_account = False

    # Buttons side-by-side to toggle forms
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("➕ Create Account"):
                st.session_state.show_create_account = True
                st.session_state.show_edit_account = False
        with btn_col2:
            if st.button("✏️ Edit Accounts"):
                st.session_state.show_edit_account = True
                st.session_state.show_create_account = False

    # Forms side-by-side
        form_col1, form_col2 = st.columns(2)

        if st.session_state.show_create_account:
            with form_col1:
                st.subheader("➕ Create New Account")
                with st.form("create_account_form"):
                    new_name = st.text_input("Account Name")
                    new_balance = st.number_input("Starting Balance (€)", min_value=0.0, value=0.0, step=10.0)
                    if st.form_submit_button("Add Account"):
                        if new_name:
                            accounts.append({"name": new_name, "balance": new_balance, "allocated": 0.0})
                            save_json("accounts.json", accounts)
                            st.success(f"✅ Account '{new_name}' created successfully!")
                            st.session_state.needs_rerun = True
                        else:
                            st.error("Please enter a valid account name.")

        if st.session_state.show_edit_account:
            with form_col2:
                st.subheader("✏️ Edit Accounts")
                for i, acc in enumerate(accounts):
                    with st.expander(f"Edit {acc['name']}"):
                        new_name = st.text_input("Name", value=acc["name"], key=f"name_{i}")
                        new_balance = st.number_input("Balance (€)", min_value=0.0, value=acc["balance"], step=10.0, key=f"bal_{i}")
                        new_alloc = st.number_input("Allocated (€)", min_value=0.0, value=acc.get("allocated", 0.0), step=10.0, key=f"alloc_{i}")

                        col1, col2, col3 = st.columns(3)
                        with col1:
                            if st.button("💾 Save", key=f"save_{i}"):
                                accounts[i]["name"] = new_name
                                accounts[i]["balance"] = new_balance
                                accounts[i]["allocated"] = new_alloc
                                save_json("accounts.json", accounts)
                                st.success("Account updated successfully.")
                                st.session_state.needs_rerun = True
                        with col2:
                            if st.button("🗑️ Delete", key=f"delete_{i}"):
                                accounts.pop(i)
                                save_json("accounts.json", accounts)
                                st.success("Account deleted.")
                                st.session_state.needs_rerun = True
                        with col3:
                            if st.button("♻️ Reset Allocations", key=f"reset_{i}"):
                                accounts[i]["allocated"] = 0.0
                                save_json("accounts.json", accounts)
                                st.success("Allocations reset to €0.00.")
                                st.session_state.needs_rerun = True

        st.write("---")

    # Auto-Split and Transfer side-by-side
        split_col, transfer_col = st.columns(2)

        with split_col:
            st.subheader("⚙️ Auto-Split Setup")

            auto_split = load_auto_split()
            accounts = load_json("accounts.json", [])

            if accounts:
                st.write("Set how new income should be distributed across your accounts:")

                ratios = {}
                total_ratio = 0
                for acc in accounts:
                    acc_name = acc["name"]
                    ratio = st.number_input(
                        f"{acc_name} (%)",
                        min_value=0.0,
                        max_value=100.0,
                        value=float(auto_split["ratios"].get(acc_name, 0)),
                        step=1.0,
                        key=f"ratio_{acc_name}"
                    )
                    ratios[acc_name] = ratio
                    total_ratio += ratio

                st.write(f"**Total:** {total_ratio:.1f}% (should be 100%)")

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("💾 Save Auto-Split"):
                        save_auto_split({"enabled": False, "ratios": ratios})
                        st.success("Auto-split settings saved but not set as default.")
                with col2:
                    if st.button("✅ Save & Use as Default"):
                        save_auto_split({"enabled": True, "ratios": ratios})
                        st.success("Auto-split settings saved and set as default.")
            else:
                st.info("Create accounts first to enable auto-split configuration.")

        with transfer_col:
            st.subheader("🔁 Transfer Between Accounts")
            with st.form("transfer_form"):
                acc_names = [a["name"] for a in accounts]
                from_acc = st.selectbox("From", acc_names)
                to_acc = st.selectbox("To", [a for a in acc_names if a != from_acc])
                amount = st.number_input("Amount (€)", min_value=0.0, value=0.0, step=10.0)
                if st.form_submit_button("Execute Transfer"):
                    if amount > 0:
                        sender = next(a for a in accounts if a["name"] == from_acc)
                        receiver = next(a for a in accounts if a["name"] == to_acc)
                        if sender["balance"] >= amount:
                            sender["balance"] -= amount
                            receiver["balance"] += amount
                            save_json("accounts.json", accounts)
                            st.success(f"Transferred €{amount:,.2f} from {from_acc} → {to_acc}.")
                            st.session_state.needs_rerun = True
                        else:
                            st.error("Insufficient funds in source account.")

        
# --- Transactions Page ---
    if page == "Transactions":

    # --- Load Data ---
        accounts = load_json(ACCOUNTS_FILE, [])
        transactions = load_json(TRANSACTIONS_FILE, [])
        standing_orders = load_json(STANDING_ORDERS_FILE, [])
        auto_split = load_json(AUTO_SPLIT_FILE, {"enabled": False, "ratios": {}})

    # --- Execute Due Standing Orders ---
        today = date.today()
        executed_orders = False

        for order in standing_orders:
        # FIX: ensure 'next_execution' exists before accessing
            if "next_execution" not in order:
               order["next_execution"] = today.isoformat()

            next_date = datetime.strptime(order["next_execution"], "%Y-%m-%d").date()
            if next_date <= today:
                freq_days = 7 if order["frequency"] == "Weekly" else 30
                amount = order["amount"]
                tx_type = order["type"]
                note = f"(Standing) {order['note']}"
                selected_account = order["account"]

            # Apply auto-split or manual logic
                if tx_type == "Income":
                    if order.get("use_auto", False) and auto_split["enabled"]:
                        ratios = auto_split["ratios"]
                        total_ratio = sum(ratios.values())
                        if total_ratio > 0:
                            for acc in accounts:
                                acc_name = acc["name"]
                                share = ratios.get(acc_name, 0) / total_ratio
                                acc["balance"] += amount * share
                    else:
                        for acc in accounts:
                            if acc["name"] == selected_account:
                                acc["balance"] += amount
                                break
                elif tx_type == "Expense":
                    for acc in accounts:
                        if acc["name"] == selected_account:
                            if acc["balance"] >= amount:
                                acc["balance"] -= amount
                            break

                transactions.append({
                    "type": tx_type,
                    "amount": amount,
                    "note": note,
                    "timestamp": datetime.now().isoformat(),
                    "account": selected_account if not order.get("use_auto", False) else "Auto-Split",
                })

            # Update next execution date
                order["next_execution"] = (today + timedelta(days=freq_days)).isoformat()
                executed_orders = True

        if executed_orders:
            save_json(TRANSACTIONS_FILE, transactions)
            save_json(ACCOUNTS_FILE, accounts)
            save_json(STANDING_ORDERS_FILE, standing_orders)

    # --- PAGE LAYOUT ---
        st.title("💸 Transactions")
        st.write("Track income, expenses, and recurring standing orders.")

        col_txs, col_sto = st.columns(2)

    # --- Add Transaction Form ---
        with col_txs:
            with st.expander("➕ Add Transaction", expanded=True):
                tx_type = st.selectbox("Type", ["Income", "Expense"])
                amount = st.number_input("Amount (€)", min_value=0.01, format="%.2f")
                note = st.text_input("Notes", placeholder="e.g. Salary, Rent, Groceries")

                use_auto = False
                if tx_type == "Income":
                    use_auto = st.checkbox("Use Auto-Split", value=False)

                selected_account = None
                if not use_auto or tx_type == "Expense":
                    if accounts:
                        selected_account = st.selectbox(
                            "Account",
                            [a["name"] for a in accounts],
                            index=0,
                        )
                    else:
                        st.warning("No accounts available — go to Accounts page first.")

                if st.button("💾 Save Transaction"):
                    if tx_type == "Income":
                        if use_auto and auto_split["enabled"]:
                            ratios = auto_split["ratios"]
                            total_ratio = sum(ratios.values())
                            if total_ratio > 0:
                                for acc in accounts:
                                    acc_name = acc["name"]
                                    share = ratios.get(acc_name, 0) / total_ratio
                                    acc["balance"] += amount * share
                                st.success(f"Income of €{amount:.2f} auto-split across accounts.")
                            else:
                                st.error("Auto-split ratios not set up.")
                        elif selected_account:
                            for acc in accounts:
                                if acc["name"] == selected_account:
                                    acc["balance"] += amount
                                    break
                            st.success(f"Added €{amount:.2f} to {selected_account}.")
                    elif tx_type == "Expense":
                        if selected_account:
                            for acc in accounts:
                                if acc["name"] == selected_account:
                                    if acc["balance"] >= amount:
                                        acc["balance"] -= amount
                                        st.success(f"Deducted €{amount:.2f} from {selected_account}.")
                                    else:
                                        st.error("Insufficient balance.")
                                    break
    
                    tx = {
                        "type": tx_type,
                        "amount": amount,
                        "note": note,
                        "timestamp": datetime.now().isoformat(),
                        "account": selected_account if not use_auto else "Auto-Split",
                    }
                    transactions.append(tx)
    
                    save_json(TRANSACTIONS_FILE, transactions)
                    save_json(ACCOUNTS_FILE, accounts)
    
        # --- Add Standing Order Form ---
        with col_sto:
            with st.expander("📅 Add Standing Order", expanded=True):
                so_type = st.selectbox("Type", ["Income", "Expense"], key="so_type")
                so_amount = st.number_input("Amount (€)", min_value=0.01, format="%.2f", key="so_amt")
                so_note = st.text_input("Notes", placeholder="e.g. Salary, Rent", key="so_note")
                so_frequency = st.selectbox("Frequency", ["Weekly", "Monthly"])
                so_start_date = st.date_input("First Execution Date", min_value=date.today())
    
                so_use_auto = False
                if so_type == "Income":
                    so_use_auto = st.checkbox("Use Auto-Split", value=False, key="so_auto")
     
                so_account = None
                if not so_use_auto or so_type == "Expense":
                    if accounts:
                        so_account = st.selectbox(
                            "Account",
                            [a["name"] for a in accounts],
                            index=0,
                            key="so_acc"
                        )
                    else:
                        st.warning("No accounts available — create some first.")
    
                if st.button("💾 Save Standing Order"):
                    new_order = {"type": so_type,
                        "amount": so_amount,
                        "note": so_note,
                        "frequency": so_frequency,
                        "next_execution": so_start_date.isoformat(),
                        "account": so_account if not so_use_auto else "Auto-Split",
                        "use_auto": so_use_auto,
                    }
                    standing_orders.append(new_order)
                    save_json(STANDING_ORDERS_FILE, standing_orders)
                    st.success("Standing Order saved successfully!")
    
        st.write("---")

    # --- History section side-by-side ---
        col_hist_tx, col_hist_so = st.columns(2)

        with col_hist_tx:
            st.subheader("📜 Transactions History")
            if not transactions:
                st.info("No transactions yet.")
            else:
                df = pd.DataFrame(transactions)
                df["date"] = pd.to_datetime(df["timestamp"]).dt.date
                df = df.sort_values("timestamp", ascending=False)
                for i, row in df.iterrows():
                    col1, col2, col3, col4 = st.columns([2, 2, 3, 1])
                    with col1:
                        st.write(f"**{row['type']}**")
                    with col2:
                        st.write(f"€{row['amount']:.2f}")
                    with col3:
                        st.write(f"{row['account']} — {row['note']}")
                    with col4:
                        if st.button("🗑️", key=f"del_tx_{i}"):
                            accounts = load_accounts()
                            acc_name = row.get("account")
                            amt = float(row.get("amount",0.0))
                            if acc_name == "Auto-Split":
                                auto = load_auto_split()
                                ratios = auto.get("ratios", {}) if auto.get("enabled", False) else {}
                                total_ratio = sum(ratios.values()) if ratios else 0
                                if total_ratio > 0:
                                    for acc in accounts:
                                        name = acc["name"]
                                        share = ratios.get(name,0)/total_ratio if total_ratio else 0
                                        acc["balance"] = float(acc.get("balance",0.0)) - (amt * share)
                            else:
        # Manual transaction revert
                                acc = next((a for a in accounts if a["name"] == acc_name), None)
                                if acc:
                                    if tx_type == "Income":
                                        acc["balance"] = float(acc.get("balance", 0.0)) - amt
                                    else:  # Expense
                                        acc["balance"] = float(acc.get("balance", 0.0)) + amt

                            # remove tx and save
                            transactions.pop(i)
                            save_transactions(transactions)
                            save_accounts(accounts)
                            st.success("Transaction deleted and balances reverted.")
        with col_hist_so:
            st.subheader("📆 Standing Orders")
            if not standing_orders:
                st.info("No standing orders set.")
            else:
                for i, so in enumerate(standing_orders):
                    st.write(f"**{so['type']}** — €{so['amount']:.2f} every {so['frequency']} from {so['account']}")
                    st.caption(f"Next: {so['next_execution']} | Note: {so['note']}")
                    if st.button("🗑️ Delete", key=f"del_so_{i}"):
                        standing_orders.pop(i)
                        save_json(STANDING_ORDERS_FILE, standing_orders)
                        st.success("Standing order deleted.")

def aggregate_user_data():
    transactions = load_transactions()
    goals = load_goals()
    accounts = load_accounts()

    today = datetime.now()
    month = today.month - 1 if today.month > 1 else 12
    year = today.year if today.month > 1 else today.year - 1
    start_last_month = datetime(year, month, 1)
    end_last_month = datetime(today.year, today.month, 1)

    monthly_txs = [
        tx for tx in transactions
        if start_last_month <= datetime.fromisoformat(tx['timestamp']) < end_last_month
    ]

    income = sum(tx['amount'] for tx in monthly_txs if tx['type'] == 'Income')
    expenses = sum(tx['amount'] for tx in monthly_txs if tx['type'] == 'Expense')
    profit = income - expenses

    if goals:
        goal_progress_percent = sum(
            min(float(g.get('current', 0)) / float(g.get('target', 1)), 1) for g in goals
        ) / len(goals) * 100
    else:
        goal_progress_percent = 0

    total_allocated = sum(acc.get('allocated', 0) for acc in accounts)
    allocation = {}
    for acc in accounts:
        category = acc.get('name', 'Other')
        allocation[category] = round(acc.get('allocated', 0) / total_allocated * 100, 2) if total_allocated else 0

    return profit, goal_progress_percent, allocation

def display_report(report):
    # Show the basic financial summary
    st.write(f"**Profit/Loss:** €{report['profit']:.2f}")
    st.write(f"**Goal Progress:** {report['goal_progress']:.1f}%")
    st.write("**Fund Allocation:**")
    for category, percent in report['allocation'].items():
        st.write(f"- {category}: {percent}%")
    st.markdown("---")
    # Show the concise AI-generated analysis and tip
    st.write("**Analysis & Tip:**")
    st.write(report['analysis_and_tip'])

def generate_monthly_report():
    profit, goal_progress, allocation = aggregate_user_data()
    prompt = build_prompt(profit, goal_progress, allocation)
    response = co.chat(
        model="command-a-03-2025",
        message=prompt,
        max_tokens=150,
        temperature=0.7,
    )
    return {
        "profit": profit,
        "goal_progress": goal_progress,
        "allocation": allocation,
        "analysis_and_tip": response.text.strip()
    }


def reports():
    st.markdown("<h1 style='text-align: center;'>📅 Monthly Reports</h1>", unsafe_allow_html=True)
    st.caption("Get tailored insights each month to track your progress, optimize your spending, and confidently move closer to your financial goals.")
    reports = load_reports()
    current_month = datetime.now().strftime("%Y-%m")
    today = datetime.now().day

    # Calculate days until first day of next month
    next_month = datetime(datetime.now().year + (1 if datetime.now().month == 12 else 0), (datetime.now().month % 12) + 1, 1)
    days_until_next_report = (next_month - datetime.now()).days

    if current_month in reports:
        st.success(f"Showing this month's report ({current_month}). Next report will be available in {days_until_next_report} days.")
        display_report(reports[current_month])
        if st.button("Regenerate Report Now"):
            with st.spinner("Regenerating monthly report..."):
                report = generate_monthly_report()
                reports[current_month] = report
                save_reports(reports)
            st.success("Monthly report regenerated!")
            display_report(report)
    else:
        st.info(f"No report exists yet for this month ({current_month}). You can generate your monthly report now.")
        if st.button("Generate Report Now"):
            with st.spinner("Generating monthly report..."):
                report = generate_monthly_report()
                reports[current_month] = report
                save_reports(reports)
            st.success("Monthly report generated!")
            display_report(report)

    st.markdown("---")
    st.markdown("<h3 style='text-align: center;'>📊 Past Reports History</h3>", unsafe_allow_html=True)

    if reports:
        # Sort months descending for display
        months = sorted(reports.keys(), reverse=True)
        # Build DataFrame with a user-friendly month year format
        df_history = pd.DataFrame({
            "Report Month": [datetime.strptime(m, "%Y-%m").strftime("%B %Y") for m in months],
            "Key": months
        })

        st.dataframe(df_history[["Report Month"]], use_container_width=True)

        selected_index = st.selectbox("Select Report to View", range(len(months)), format_func=lambda x: df_history.iloc[x]["Report Month"])

        if selected_index is not None:
            selected_key = df_history.iloc[selected_index]["Key"]
            report = reports[selected_key]
            st.markdown(f"## Report for {selected_key} ({df_history.iloc[selected_index]['Report Month']})")
            display_report(report)
    else:
        st.info("No past reports available yet.")
# Main app routing addition
if st.session_state.onboarded:
    if page == "Reports":
        reports()