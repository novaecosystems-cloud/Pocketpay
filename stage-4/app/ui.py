"""
Stage 2 UI HTML Templates & Rendering Engine.
Provides SSR pages meeting all data-testid requirements for Playwright test automation.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from app.models import money_format


def render_nav(user: Optional[Dict[str, Any]]) -> str:
    if not user:
        return """
        <nav style="display:flex; justify-content:space-between; padding:12px; background:#1e293b; color:#fff;">
            <div><strong>Pocketful</strong></div>
            <div>
                <a href="/login" style="color:#38bdf8; margin-right:12px;">Login</a>
                <a href="/signup" style="color:#38bdf8;">Signup</a>
            </div>
        </nav>
        """
    return f"""
    <nav style="display:flex; justify-content:space-between; align-items:center; padding:12px; background:#1e293b; color:#fff;">
        <div style="display:flex; gap:16px; align-items:center;">
            <strong>Pocketful</strong>
            <a href="/" style="color:#cbd5e1; text-decoration:none;">Dashboard</a>
            <a href="/requests" style="color:#cbd5e1; text-decoration:none;">Requests</a>
            <a href="/split" style="color:#cbd5e1; text-decoration:none;">Split</a>
            <a href="/authorizations" style="color:#cbd5e1; text-decoration:none;">Authorizations</a>
        </div>
        <div style="display:flex; gap:12px; align-items:center;">
            <span data-testid="current-user">{user["display_name"]}</span>
            <span data-testid="current-handle">{user["handle"]}</span>
            <button data-testid="logout-button" onclick="logout()" style="background:#ef4444; color:#fff; border:none; padding:4px 8px; border-radius:4px; cursor:pointer;">Logout</button>
        </div>
    </nav>
    <script>
    function logout() {{
        document.cookie = "token=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;";
        window.location.href = "/login";
    }}
    </script>
    """


def page_frame(title: str, content: str, user: Optional[Dict[str, Any]] = None) -> str:
    nav = render_nav(user)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Pocketful</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin:0; padding:0; background:#f8fafc; color:#0f172a; }}
        .container {{ max-width: 800px; margin: 24px auto; padding: 0 16px; }}
        .card {{ background:#fff; border-radius:8px; padding:20px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,0.1); }}
        input, select, button {{ font-size:14px; padding:8px 12px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box; }}
        button {{ background:#2563eb; color:#fff; border:none; cursor:pointer; font-weight:500; }}
        button:hover {{ background:#1d4ed8; }}
        .error {{ color:#dc2626; font-size:13px; margin-top:8px; }}
        .item {{ border-bottom:1px solid #e2e8f0; padding:12px 0; }}
        .item:last-child {{ border-bottom:none; }}
    </style>
</head>
<body>
    {nav}
    <div class="container">
        {content}
    </div>
</body>
</html>
"""


def render_login(error: Optional[str] = None) -> str:
    err_html = f'<div data-testid="auth-error" class="error">{error}</div>' if error else ''
    content = f"""
    <div style="display:flex; justify-content:center; gap:24px; max-width:860px; margin:40px auto; flex-wrap:wrap;">
        <div class="card" style="width:380px;">
            <h2>Login to Pocketful</h2>
            <form id="loginForm" method="POST" action="/login">
                <div style="margin-bottom:12px;">
                    <label>Email</label><br/>
                    <input type="email" data-testid="login-email" name="email" required style="width:100%;" />
                </div>
                <div style="margin-bottom:12px;">
                    <label>Password</label><br/>
                    <input type="password" data-testid="login-password" name="password" required style="width:100%;" />
                </div>
                <button type="submit" data-testid="login-submit" style="width:100%;">Sign In</button>
                {err_html}
            </form>
        </div>

        <div class="card" style="width:420px; background:#0b0f19; border:1px solid #00f0ff; color:#fff;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; border-bottom:1px solid #1e293b; padding-bottom:8px;">
                <h3 style="margin:0; color:#00f0ff; font-size:15px;">🔑 Demo Accounts & Passwords</h3>
                <span style="font-size:10px; background:rgba(0,240,255,0.15); color:#00f0ff; padding:2px 8px; border-radius:10px; font-weight:700;">CLICK TO FILL</span>
            </div>
            <p style="font-size:12px; color:#94a3b8; margin-bottom:12px;">Click any account card below to auto-fill the login fields:</p>
            
            <div onclick="fillLogin('alice@demo.com', 'Password123!')" style="background:#111827; border:1px solid #1f2937; padding:9px 12px; border-radius:8px; margin-bottom:8px; cursor:pointer;" onmouseover="this.style.borderColor='#00f0ff'" onmouseout="this.style.borderColor='#1f2937'">
                <div style="display:flex; justify-content:space-between; font-weight:700; color:#fff; font-size:12.5px;">
                    <span>Alice Demo (@alice)</span>
                    <span style="color:#10b981;">€1,500.00</span>
                </div>
                <div style="font-size:11px; color:#94a3b8; margin-top:2px;">Email: <code style="color:#38bdf8;">alice@demo.com</code> • Pass: <code style="color:#f43f5e;">Password123!</code></div>
                <div style="font-size:10.5px; color:#64748b; margin-top:2px;">Active balance, €45.00 hold, pending bill request</div>
            </div>

            <div onclick="fillLogin('bob@demo.com', 'Password123!')" style="background:#111827; border:1px solid #1f2937; padding:9px 12px; border-radius:8px; margin-bottom:8px; cursor:pointer;" onmouseover="this.style.borderColor='#8b5cf6'" onmouseout="this.style.borderColor='#1f2937'">
                <div style="display:flex; justify-content:space-between; font-weight:700; color:#fff; font-size:12.5px;">
                    <span>Bob Demo (@bob)</span>
                    <span style="color:#10b981;">€850.00</span>
                </div>
                <div style="font-size:11px; color:#94a3b8; margin-top:2px;">Email: <code style="color:#38bdf8;">bob@demo.com</code> • Pass: <code style="color:#f43f5e;">Password123!</code></div>
                <div style="font-size:10.5px; color:#64748b; margin-top:2px;">Sent €25.00 dinner payment, pending concert request</div>
            </div>

            <div onclick="fillLogin('carol@demo.com', 'Password123!')" style="background:#111827; border:1px solid #1f2937; padding:9px 12px; border-radius:8px; cursor:pointer;" onmouseover="this.style.borderColor='#10b981'" onmouseout="this.style.borderColor='#1f2937'">
                <div style="display:flex; justify-content:space-between; font-weight:700; color:#fff; font-size:12.5px;">
                    <span>Carol Demo (@carol)</span>
                    <span style="color:#10b981;">€500.00</span>
                </div>
                <div style="font-size:11px; color:#94a3b8; margin-top:2px;">Email: <code style="color:#38bdf8;">carol@demo.com</code> • Pass: <code style="color:#f43f5e;">Password123!</code></div>
                <div style="font-size:10.5px; color:#64748b; margin-top:2px;">Fresh wallet with incoming €18.00 coffee request</div>
            </div>
        </div>
    </div>
    <script>
    function fillLogin(email, pass) {{
        document.querySelector('[data-testid="login-email"]').value = email;
        document.querySelector('[data-testid="login-password"]').value = pass;
    }}
    </script>
    """
    return page_frame("Login", content)


def render_signup(error: Optional[str] = None) -> str:
    err_html = f'<div data-testid="auth-error" class="error">{error}</div>' if error else ''
    content = f"""
    <div class="card" style="max-width:400px; margin:40px auto;">
        <h2>Create an Account</h2>
        <form id="signupForm" method="POST" action="/signup">
            <div style="margin-bottom:12px;">
                <label>Email</label><br/>
                <input type="email" data-testid="signup-email" name="email" required style="width:100%;" />
            </div>
            <div style="margin-bottom:12px;">
                <label>Password</label><br/>
                <input type="password" data-testid="signup-password" name="password" required style="width:100%;" />
            </div>
            <div style="margin-bottom:12px;">
                <label>Display Name</label><br/>
                <input type="text" data-testid="signup-display-name" name="display_name" required style="width:100%;" />
            </div>
            <button type="submit" data-testid="signup-submit" style="width:100%;">Create Account</button>
            {err_html}
        </form>
    </div>
    """
    return page_frame("Signup", content)


def render_dashboard(
    user: Dict[str, Any],
    total: int,
    available: int,
    held: int,
    currency: str,
    minor_units: int,
    payments: List[Dict[str, Any]],
    pay_error: Optional[str] = None,
    request_error: Optional[str] = None,
    pay_values: Optional[Dict[str, str]] = None
) -> str:
    pay_vals = pay_values or {"handle": "", "amount": "", "note": "", "visibility": "public"}
    bal_fmt = money_format(total, minor_units, currency)
    avail_fmt = money_format(available, minor_units, currency)
    held_fmt = money_format(held, minor_units, currency)

    held_elem = f'<div style="color:#64748b; font-size:14px;">Held: <span data-testid="wallet-held" data-amount="{held}">{held_fmt}</span></div>' if held > 0 else ''

    # Activity list
    if not payments:
        feed_content = '<div data-testid="empty-activity" style="color:#64748b; padding:16px 0;">No visible activity yet.</div>'
    else:
        items = []
        for p in payments:
            pid = p["payment_id"]
            vis = p["visibility"]
            parties = f"{p['from_handle']} -> {p['to_handle']}"
            pamt_fmt = money_format(p["amount"], minor_units, currency)
            pnote = p.get("note", "")
            items.append(f"""
            <div class="item" data-testid="activity-item-{pid}" data-visibility="{vis}">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span data-testid="activity-parties-{pid}" style="font-weight:600;">{parties}</span>
                        <div data-testid="activity-note-{pid}" style="font-size:13px; color:#475569;">{pnote}</div>
                    </div>
                    <div data-testid="activity-amount-{pid}" style="font-weight:600;">{pamt_fmt}</div>
                </div>
            </div>
            """)
        feed_content = f'<div data-testid="activity-list">{"".join(items)}</div>'

    pay_err_elem = f'<div data-testid="pay-error" class="error">{pay_error}</div>' if pay_error else ''
    req_err_elem = f'<div data-testid="request-error" class="error">{request_error}</div>' if request_error else ''

    content = f"""
    <!-- Wallet Card -->
    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
                <div style="font-size:13px; color:#64748b; text-transform:uppercase; letter-spacing:0.05em;">Available Balance</div>
                <div data-testid="wallet-available" data-amount="{available}" style="font-size:28px; font-weight:700; color:#0f172a;">{avail_fmt}</div>
                <div style="font-size:14px; color:#64748b;">Total: <span data-testid="wallet-balance" data-amount="{total}">{bal_fmt}</span></div>
                {held_elem}
            </div>
            <div>
                <button type="button" data-testid="wallet-refresh" onclick="refreshWallet()" style="background:#f1f5f9; color:#334155; border:1px solid #cbd5e1;">Refresh</button>
            </div>
        </div>
    </div>

    <!-- Forms Grid -->
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:20px;">
        <!-- Pay Form -->
        <div class="card">
            <h3>Send Payment</h3>
            <form id="payForm" onsubmit="handlePaySubmit(event)">
                <div style="margin-bottom:10px;">
                    <label style="font-size:13px;">Recipient Handle</label><br/>
                    <input type="text" data-testid="pay-handle" id="pay_handle" value="{pay_vals['handle']}" oninput="onPayFieldChanged()" required style="width:100%;" />
                </div>
                <div style="margin-bottom:10px;">
                    <label style="font-size:13px;">Amount</label><br/>
                    <input type="text" data-testid="pay-amount" id="pay_amount" value="{pay_vals['amount']}" oninput="onPayFieldChanged()" required style="width:100%;" />
                </div>
                <div style="margin-bottom:10px;">
                    <label style="font-size:13px;">Note</label><br/>
                    <input type="text" data-testid="pay-note" id="pay_note" value="{pay_vals['note']}" oninput="onPayFieldChanged()" style="width:100%;" />
                </div>
                <div style="margin-bottom:12px;">
                    <label style="font-size:13px;">Visibility</label><br/>
                    <select data-testid="pay-visibility" id="pay_visibility" onchange="onPayFieldChanged()" style="width:100%;">
                        <option value="public" {"selected" if pay_vals.get('visibility') == 'public' else ''}>public</option>
                        <option value="private" {"selected" if pay_vals.get('visibility') == 'private' else ''}>private</option>
                    </select>
                </div>
                <button type="submit" data-testid="pay-submit" style="width:100%;">Pay</button>
                <div id="payErrorContainer">{pay_err_elem}</div>
            </form>
        </div>

        <!-- Request Form -->
        <div class="card">
            <h3>Request Money</h3>
            <form id="requestForm" onsubmit="handleRequestSubmit(event)">
                <div style="margin-bottom:10px;">
                    <label style="font-size:13px;">Payer Handle</label><br/>
                    <input type="text" data-testid="request-handle" id="req_handle" required style="width:100%;" />
                </div>
                <div style="margin-bottom:10px;">
                    <label style="font-size:13px;">Amount</label><br/>
                    <input type="text" data-testid="request-amount" id="req_amount" required style="width:100%;" />
                </div>
                <div style="margin-bottom:10px;">
                    <label style="font-size:13px;">Note</label><br/>
                    <input type="text" data-testid="request-note" id="req_note" style="width:100%;" />
                </div>
                <button type="submit" data-testid="request-submit" style="width:100%; margin-top:28px;">Request</button>
                <div id="reqErrorContainer">{req_err_elem}</div>
            </form>
        </div>
    </div>

    <!-- Feed Card -->
    <div class="card">
        <h3>Activity Feed</h3>
        <div id="feedContainer">
            {feed_content}
        </div>
    </div>

    <script>
    const MINOR_UNITS = {minor_units};
    const CURRENCY = "{currency}";
    let payIdempotencyKey = localStorage.getItem("pocketful_pay_idemp_key") || crypto.randomUUID();
    let payFormSnapshot = null;

    function getCookie(name) {{
        const value = `; ${{document.cookie}}`;
        const parts = value.split(`; ${{name}}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return '';
    }}

    function onPayFieldChanged() {{
        // When form values change from previous submission snapshot, mint a new key
        const currentSnap = JSON.stringify({{
            h: document.getElementById("pay_handle").value,
            a: document.getElementById("pay_amount").value,
            n: document.getElementById("pay_note").value,
            v: document.getElementById("pay_visibility").value
        }});
        if (payFormSnapshot !== null && currentSnap !== payFormSnapshot) {{
            payIdempotencyKey = crypto.randomUUID();
            localStorage.setItem("pocketful_pay_idemp_key", payIdempotencyKey);
            payFormSnapshot = null;
        }}
    }}

    function parseAmountToMinor(val, minorUnits) {{
        val = val.trim();
        if (!val) throw new Error("Amount cannot be empty");
        if (minorUnits === 0) {{
            if (val.includes(".")) throw new Error("Decimals not permitted");
            if (!/^[0-9]+$/.test(val)) throw new Error("Invalid digits");
            return parseInt(val, 10);
        }}
        if (val.includes(".")) {{
            const parts = val.split(".");
            if (parts.length !== 2 || !/^[0-9]+$/.test(parts[0]) || !/^[0-9]+$/.test(parts[1])) throw new Error("Malformed decimal");
            if (parts[1].length > minorUnits) throw new Error("Too many decimal places");
            const frac = parts[1].padEnd(minorUnits, "0");
            return parseInt(parts[0], 10) * Math.pow(10, minorUnits) + parseInt(frac, 10);
        }} else {{
            if (!/^[0-9]+$/.test(val)) throw new Error("Invalid digits");
            return parseInt(val, 10) * Math.pow(10, minorUnits);
        }}
    }}

    function formatMoney(minor, minorUnits, curr) {{
        if (minorUnits === 0) return `${{minor}} ${{curr}}`;
        let s = minor.toString().padStart(minorUnits + 1, '0');
        return `${{s.slice(0, -minorUnits)}}.${{s.slice(-minorUnits)}} ${{curr}}`;
    }}

    async function refreshWallet() {{
        const token = getCookie("token");
        const headers = token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}};
        try {{
            const [meRes, actRes] = await Promise.all([
                fetch("/me", {{ headers }}),
                fetch("/activity?limit=50", {{ headers }})
            ]);
            if (meRes.ok) {{
                const me = await meRes.json();
                const balEl = document.querySelector('[data-testid="wallet-balance"]');
                const availEl = document.querySelector('[data-testid="wallet-available"]');
                const heldEl = document.querySelector('[data-testid="wallet-held"]');
                if (balEl) {{
                    balEl.setAttribute("data-amount", me.total);
                    balEl.textContent = formatMoney(me.total, me.minor_units, me.currency);
                }}
                if (availEl) {{
                    availEl.setAttribute("data-amount", me.available);
                    availEl.textContent = formatMoney(me.available, me.minor_units, me.currency);
                }}
                if (heldEl) {{
                    heldEl.setAttribute("data-amount", me.held);
                    heldEl.textContent = formatMoney(me.held, me.minor_units, me.currency);
                }}
            }}
            if (actRes.ok) {{
                const act = await actRes.json();
                renderActivityList(act.payments);
            }}
        }} catch(e) {{
            console.error("Refresh error", e);
        }}
    }}

    function renderActivityList(payments) {{
        const container = document.getElementById("feedContainer");
        if (!payments || payments.length === 0) {{
            container.innerHTML = '<div data-testid="empty-activity" style="color:#64748b; padding:16px 0;">No visible activity yet.</div>';
            return;
        }}
        const items = payments.map(p => `
            <div class="item" data-testid="activity-item-${{p.payment_id}}" data-visibility="${{p.visibility}}">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span data-testid="activity-parties-${{p.payment_id}}" style="font-weight:600;">${{p.from_handle}} -> ${{p.to_handle}}</span>
                        <div data-testid="activity-note-${{p.payment_id}}" style="font-size:13px; color:#475569;">${{p.note || ''}}</div>
                    </div>
                    <div data-testid="activity-amount-${{p.payment_id}}" style="font-weight:600;">${{formatMoney(p.amount, MINOR_UNITS, CURRENCY)}}</div>
                </div>
            </div>
        `).join("");
        container.innerHTML = `<div data-testid="activity-list">${{items}}</div>`;
    }}

    async function handlePaySubmit(e) {{
        e.preventDefault();
        const errContainer = document.getElementById("payErrorContainer");
        errContainer.innerHTML = "";

        const handle = document.getElementById("pay_handle").value.trim();
        const rawAmt = document.getElementById("pay_amount").value.trim();
        const note = document.getElementById("pay_note").value.trim();
        const visibility = document.getElementById("pay_visibility").value;

        let minorAmt;
        try {{
            minorAmt = parseAmountToMinor(rawAmt, MINOR_UNITS);
        }} catch (err) {{
            errContainer.innerHTML = `<div data-testid="pay-error" class="error">${{err.message}}</div>`;
            return;
        }}

        const body = {{ to_handle: handle, amount: minorAmt, note: note, visibility: visibility }};
        const token = getCookie("token");

        // Record snapshot of what was submitted
        payFormSnapshot = JSON.stringify({{ h: handle, a: rawAmt, n: note, v: visibility }});

        try {{
            const res = await fetch("/payments", {{
                method: "POST",
                headers: {{
                    "Content-Type": "application/json",
                    "Idempotency-Key": payIdempotencyKey,
                    ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}})
                }},
                body: JSON.stringify(body)
            }});

            if (!res.ok) {{
                const errData = await res.json().catch(() => ({{}}));
                const errMsg = (errData.error && errData.error.message) || `Error ${{res.status}}`;
                errContainer.innerHTML = `<div data-testid="pay-error" class="error">${{errMsg}}</div>`;
                refreshWallet();
                return;
            }}

            // Success: refresh without clearing inputs
            await refreshWallet();
        }} catch(err) {{
            errContainer.innerHTML = `<div data-testid="pay-uncertain" class="error">Network error. Please retry.</div>`;
        }}
    }}

    async function handleRequestSubmit(e) {{
        e.preventDefault();
        const errContainer = document.getElementById("reqErrorContainer");
        errContainer.innerHTML = "";

        const handle = document.getElementById("req_handle").value.trim();
        const rawAmt = document.getElementById("req_amount").value.trim();
        const note = document.getElementById("req_note").value.trim();

        let minorAmt;
        try {{
            minorAmt = parseAmountToMinor(rawAmt, MINOR_UNITS);
        }} catch (err) {{
            errContainer.innerHTML = `<div data-testid="request-error" class="error">${{err.message}}</div>`;
            return;
        }}

        const body = {{ payer_handle: handle, amount: minorAmt, note: note }};
        const token = getCookie("token");

        try {{
            const res = await fetch("/requests", {{
                method: "POST",
                headers: {{
                    "Content-Type": "application/json",
                    "Idempotency-Key": crypto.randomUUID(),
                    ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}})
                }},
                body: JSON.stringify(body)
            }});

            if (!res.ok) {{
                const errData = await res.json().catch(() => ({{}}));
                const errMsg = (errData.error && errData.error.message) || `Error ${{res.status}}`;
                errContainer.innerHTML = `<div data-testid="request-error" class="error">${{errMsg}}</div>`;
                return;
            }}
            document.getElementById("req_handle").value = "";
            document.getElementById("req_amount").value = "";
            document.getElementById("req_note").value = "";
            await refreshWallet();
        }} catch(err) {{
            errContainer.innerHTML = `<div data-testid="request-error" class="error">${{err.message}}</div>`;
        }}
    }}
    </script>
    """
    return page_frame("Dashboard", content, user)


def render_requests(
    user: Dict[str, Any],
    incoming: List[Dict[str, Any]],
    outgoing: List[Dict[str, Any]],
    currency: str,
    minor_units: int,
    error: Optional[str] = None
) -> str:
    err_elem = f'<div data-testid="request-error" class="error">{error}</div>' if error else '<div id="reqErrorContainer"></div>'

    has_incoming = len(incoming) > 0
    has_outgoing = len(outgoing) > 0

    inc_items = []
    for r in incoming:
        rid = r["request_id"]
        st = r["status"]
        amt_fmt = money_format(r["amount"], minor_units, currency)
        actions = ""
        if st == "pending":
            actions = f"""
            <button type="button" data-testid="request-pay-{rid}" onclick="payRequest('{rid}')" style="margin-right:8px;">Pay</button>
            <button type="button" data-testid="request-decline-{rid}" onclick="declineRequest('{rid}')" style="background:#64748b;">Decline</button>
            """
        inc_items.append(f"""
        <div class="item" data-testid="request-item-{rid}" data-status="{st}">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <div style="font-weight:600;">From: {r['requester_handle']}</div>
                    <div style="font-size:13px; color:#475569;">Note: {r.get('note', '')} | Status: {st}</div>
                </div>
                <div style="text-align:right;">
                    <div data-testid="request-amount-{rid}" style="font-weight:600; margin-bottom:6px;">{amt_fmt}</div>
                    <div>{actions}</div>
                </div>
            </div>
        </div>
        """)

    out_items = []
    for r in outgoing:
        rid = r["request_id"]
        st = r["status"]
        amt_fmt = money_format(r["amount"], minor_units, currency)
        actions = ""
        if st == "pending":
            actions = f"""
            <button type="button" data-testid="request-cancel-{rid}" onclick="cancelRequest('{rid}')" style="background:#ef4444;">Cancel</button>
            """
        out_items.append(f"""
        <div class="item" data-testid="request-item-{rid}" data-status="{st}">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <div style="font-weight:600;">To: {r['payer_handle']}</div>
                    <div style="font-size:13px; color:#475569;">Note: {r.get('note', '')} | Status: {st}</div>
                </div>
                <div style="text-align:right;">
                    <div data-testid="request-amount-{rid}" style="font-weight:600; margin-bottom:6px;">{amt_fmt}</div>
                    <div>{actions}</div>
                </div>
            </div>
        </div>
        """)

    empty_block = '<div data-testid="empty-requests" style="color:#64748b; padding:12px 0;">No incoming or outgoing requests.</div>' if (not has_incoming and not has_outgoing) else ''

    body_content = f"""
    {empty_block}
    <div style="margin-bottom:24px;">
        <h3>Incoming Requests</h3>
        <div data-testid="incoming-list">{"".join(inc_items) if inc_items else '<div style="color:#64748b; font-size:14px;">No incoming requests.</div>'}</div>
    </div>
    <div>
        <h3>Outgoing Requests</h3>
        <div data-testid="outgoing-list">{"".join(out_items) if out_items else '<div style="color:#64748b; font-size:14px;">No outgoing requests.</div>'}</div>
    </div>
    """

    content = f"""
    <div class="card">
        <h2>Payment Requests</h2>
        {err_elem}
        {body_content}
    </div>
    <script>
    function getCookie(name) {{
        const value = `; ${{document.cookie}}`;
        const parts = value.split(`; ${{name}}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return '';
    }}

    async function payRequest(rid) {{
        const token = getCookie("token");
        const errEl = document.getElementById("reqErrorContainer") || document.querySelector('[data-testid="request-error"]');
        try {{
            const res = await fetch(`/requests/${{rid}}/pay`, {{
                method: "POST",
                headers: {{
                    "Content-Type": "application/json",
                    "Idempotency-Key": crypto.randomUUID(),
                    ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}})
                }},
                body: JSON.stringify({{ visibility: "public" }})
            }});
            if (!res.ok) {{
                const d = await res.json().catch(() => ({{}}));
                const msg = (d.error && d.error.message) || "Payment failed";
                if (errEl) errEl.innerHTML = `<div data-testid="request-error" class="error">${{msg}}</div>`;
                return;
            }}
            window.location.reload();
        }} catch(e) {{
            if (errEl) errEl.innerHTML = `<div data-testid="request-error" class="error">${{e.message}}</div>`;
        }}
    }}

    async function declineRequest(rid) {{
        const token = getCookie("token");
        await fetch(`/requests/${{rid}}/decline`, {{
            method: "POST",
            headers: token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}}
        }});
        window.location.reload();
    }}

    async function cancelRequest(rid) {{
        const token = getCookie("token");
        await fetch(`/requests/${{rid}}/cancel`, {{
            method: "POST",
            headers: token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}}
        }});
        window.location.reload();
    }}
    </script>
    """
    return page_frame("Requests", content, user)


def render_split(
    user: Dict[str, Any],
    currency: str,
    minor_units: int,
    error: Optional[str] = None
) -> str:
    err_elem = f'<div data-testid="split-error" class="error">{error}</div>' if error else '<div id="splitErrorContainer"></div>'

    content = f"""
    <div class="card" style="max-width:500px; margin:40px auto;">
        <h2>Split a Bill</h2>
        <form id="splitForm" onsubmit="handleSplitSubmit(event)">
            <div style="margin-bottom:12px;">
                <label style="font-size:13px;">Total Amount</label><br/>
                <input type="text" data-testid="split-amount" id="split_amount" oninput="updateSplitPreview()" required style="width:100%;" />
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:13px;">Participant Handles (comma-separated, in exact order)</label><br/>
                <input type="text" data-testid="split-handles" id="split_handles" oninput="updateSplitPreview()" required style="width:100%;" placeholder="ada,bob,cy" />
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:13px;">Note (optional)</label><br/>
                <input type="text" id="split_note" style="width:100%;" />
            </div>

            <!-- Dynamic Split Preview -->
            <div id="previewWrapper" style="display:none; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:12px; margin-bottom:16px;">
                <div style="font-size:13px; font-weight:600; color:#475569; margin-bottom:8px;">Share Preview:</div>
                <div data-testid="split-preview" id="splitPreviewContainer"></div>
            </div>

            <button type="submit" data-testid="split-submit" style="width:100%;">Create Split</button>
            {err_elem}
        </form>
    </div>

    <script>
    const MINOR_UNITS = {minor_units};
    const CURRENCY = "{currency}";

    function parseAmountToMinor(val, minorUnits) {{
        val = val.trim();
        if (!val) throw new Error("Amount cannot be empty");
        if (minorUnits === 0) {{
            if (val.includes(".")) throw new Error("Decimals not permitted");
            if (!/^[0-9]+$/.test(val)) throw new Error("Invalid digits");
            return parseInt(val, 10);
        }}
        if (val.includes(".")) {{
            const parts = val.split(".");
            if (parts.length !== 2 || !/^[0-9]+$/.test(parts[0]) || !/^[0-9]+$/.test(parts[1])) throw new Error("Malformed decimal");
            if (parts[1].length > minorUnits) throw new Error("Too many decimal places");
            const frac = parts[1].padEnd(minorUnits, "0");
            return parseInt(parts[0], 10) * Math.pow(10, minorUnits) + parseInt(frac, 10);
        }} else {{
            if (!/^[0-9]+$/.test(val)) throw new Error("Invalid digits");
            return parseInt(val, 10) * Math.pow(10, minorUnits);
        }}
    }}

    function formatMoney(minor, minorUnits, curr) {{
        if (minorUnits === 0) return `${{minor}} ${{curr}}`;
        let s = minor.toString().padStart(minorUnits + 1, '0');
        return `${{s.slice(0, -minorUnits)}}.${{s.slice(-minorUnits)}} ${{curr}}`;
    }}

    function equalSplit(amount, n) {{
        if (n <= 0) return [];
        const base = Math.floor(amount / n);
        const remainder = amount - base * n;
        const shares = [];
        for (let i = 0; i < n; i++) {{
            shares.push(base + (i < remainder ? 1 : 0));
        }}
        return shares;
    }}

    function updateSplitPreview() {{
        const amtStr = document.getElementById("split_amount").value.trim();
        const handlesStr = document.getElementById("split_handles").value.trim();
        const wrapper = document.getElementById("previewWrapper");
        const container = document.getElementById("splitPreviewContainer");

        if (!amtStr || !handlesStr) {{
            wrapper.style.display = "none";
            container.innerHTML = "";
            return;
        }}

        let minorAmt;
        try {{
            minorAmt = parseAmountToMinor(amtStr, MINOR_UNITS);
        }} catch(e) {{
            wrapper.style.display = "none";
            return;
        }}

        const handles = handlesStr.split(",").map(h => h.trim().toLowerCase()).filter(h => h.length > 0);
        if (handles.length === 0) {{
            wrapper.style.display = "none";
            return;
        }}

        const shares = equalSplit(minorAmt, handles.length);
        const items = handles.map((h, idx) => `
            <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                <span>@${{h}}</span>
                <span data-testid="split-share-${{h}}" style="font-weight:600;">${{formatMoney(shares[idx], MINOR_UNITS, CURRENCY)}}</span>
            </div>
        `).join("");

        container.innerHTML = items;
        wrapper.style.display = "block";
    }}

    async function handleSplitSubmit(e) {{
        e.preventDefault();
        const errContainer = document.getElementById("splitErrorContainer");
        errContainer.innerHTML = "";

        const amtStr = document.getElementById("split_amount").value.trim();
        const handlesStr = document.getElementById("split_handles").value.trim();
        const note = document.getElementById("split_note").value.trim();

        let minorAmt;
        try {{
            minorAmt = parseAmountToMinor(amtStr, MINOR_UNITS);
        }} catch(err) {{
            errContainer.innerHTML = `<div data-testid="split-error" class="error">${{err.message}}</div>`;
            return;
        }}

        const handles = handlesStr.split(",").map(h => h.trim()).filter(h => h.length > 0);
        const body = {{ amount: minorAmt, participant_handles: handles, note: note }};

        const token = (`; ${{document.cookie}}`).split("; token=").pop().split(";").shift();
        try {{
            const res = await fetch("/splits", {{
                method: "POST",
                headers: {{
                    "Content-Type": "application/json",
                    "Idempotency-Key": crypto.randomUUID(),
                    ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}})
                }},
                body: JSON.stringify(body)
            }});

            if (!res.ok) {{
                const d = await res.json().catch(() => ({{}}));
                const msg = (d.error && d.error.message) || "Split request refused";
                errContainer.innerHTML = `<div data-testid="split-error" class="error">${{msg}}</div>`;
                return;
            }}

            window.location.href = "/requests";
        }} catch(err) {{
            errContainer.innerHTML = `<div data-testid="split-error" class="error">${{err.message}}</div>`;
        }}
    }}
    </script>
    """
    return page_frame("Split", content, user)


def render_authorizations(
    user: Dict[str, Any],
    authorizations: List[Dict[str, Any]],
    currency: str,
    minor_units: int,
    error: Optional[str] = None
) -> str:
    err_elem = f'<div data-testid="authorization-error" class="error">{error}</div>' if error else '<div id="authzErrorContainer"></div>'

    if not authorizations:
        list_content = '<div data-testid="empty-authorizations" style="color:#64748b; padding:16px 0;">No authorizations found.</div>'
    else:
        items = []
        for a in authorizations:
            aid = a["authorization_id"]
            st = a["status"]
            amt_fmt = money_format(a["amount"], minor_units, currency)
            eat = a["expires_at"]

            captured_elem = ""
            if st == "captured":
                camt_fmt = money_format(a.get("captured_amount", a["amount"]), minor_units, currency)
                captured_elem = f'<div data-testid="authorization-captured-{aid}" style="font-size:13px; color:#16a34a;">Captured: {camt_fmt}</div>'

            actions = ""
            rem = a.get("remaining_amount", a["amount"] - a.get("captured_amount", 0))
            if st == "open":
                if a["to_user_id"] == user["id"]:
                    rem_decimal = str(rem) if minor_units == 0 else f"{rem / (10**minor_units):.{minor_units}f}"
                    actions = f"""
                    <div style="margin-top:8px;">
                        <input type="text" data-testid="authorization-capture-amount-{aid}" id="cap_amt_{aid}" value="{rem_decimal}" style="width:90px; margin-right:6px;" />
                        <button type="button" data-testid="authorization-capture-{aid}" onclick="captureHold('{aid}')">Capture</button>
                    </div>
                    """
                elif a["from_user_id"] == user["id"]:
                    actions = f"""
                    <div style="margin-top:8px;">
                        <button type="button" data-testid="authorization-void-{aid}" onclick="voidHold('{aid}')" style="background:#64748b;">Void</button>
                    </div>
                    """

            items.append(f"""
            <div class="item" data-testid="authorization-item-{aid}" data-status="{st}">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div style="font-weight:600;">{a['from_handle']} -> {a['to_handle']}</div>
                        <div style="font-size:13px; color:#64748b;">Expires: <span data-testid="authorization-expires-{aid}">{eat}</span> | Status: {st}</div>
                        {captured_elem}
                    </div>
                    <div style="text-align:right;">
                        <div data-testid="authorization-amount-{aid}" style="font-weight:600;">{amt_fmt}</div>
                        {actions}
                    </div>
                </div>
            </div>
            """)
        list_content = f'<div data-testid="authorization-list">{"".join(items)}</div>'

    content = f"""
    <!-- Authorize Form Card -->
    <div class="card">
        <h3>Create Payment Authorization (Hold)</h3>
        <form id="authzForm" onsubmit="handleAuthorizeSubmit(event)">
            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-bottom:10px;">
                <div>
                    <label style="font-size:13px;">Recipient Handle</label><br/>
                    <input type="text" data-testid="authorize-handle" id="auth_handle" required style="width:100%;" />
                </div>
                <div>
                    <label style="font-size:13px;">Amount</label><br/>
                    <input type="text" data-testid="authorize-amount" id="auth_amount" required style="width:100%;" />
                </div>
            </div>
            <div style="display:grid; grid-template-columns: 2fr 1fr; gap:12px; margin-bottom:12px;">
                <div>
                    <label style="font-size:13px;">Note</label><br/>
                    <input type="text" data-testid="authorize-note" id="auth_note" style="width:100%;" />
                </div>
                <div>
                    <label style="font-size:13px;">Visibility</label><br/>
                    <select data-testid="authorize-visibility" id="auth_visibility" style="width:100%;">
                        <option value="public">public</option>
                        <option value="private">private</option>
                    </select>
                </div>
            </div>
            <button type="submit" data-testid="authorize-submit">Authorize Hold</button>
            <div id="authCreateError"></div>
        </form>
    </div>

    <!-- Authorizations List Card -->
    <div class="card">
        <h2>Authorizations</h2>
        {err_elem}
        {list_content}
    </div>

    <script>
    const MINOR_UNITS = {minor_units};

    function getCookie(name) {{
        const value = `; ${{document.cookie}}`;
        const parts = value.split(`; ${{name}}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return '';
    }}

    function parseAmountToMinor(val, minorUnits) {{
        val = val.trim();
        if (!val) throw new Error("Amount cannot be empty");
        if (minorUnits === 0) {{
            if (val.includes(".")) throw new Error("Decimals not permitted");
            if (!/^[0-9]+$/.test(val)) throw new Error("Invalid digits");
            return parseInt(val, 10);
        }}
        if (val.includes(".")) {{
            const parts = val.split(".");
            if (parts.length !== 2 || !/^[0-9]+$/.test(parts[0]) || !/^[0-9]+$/.test(parts[1])) throw new Error("Malformed decimal");
            if (parts[1].length > minorUnits) throw new Error("Too many decimal places");
            const frac = parts[1].padEnd(minorUnits, "0");
            return parseInt(parts[0], 10) * Math.pow(10, minorUnits) + parseInt(frac, 10);
        }} else {{
            if (!/^[0-9]+$/.test(val)) throw new Error("Invalid digits");
            return parseInt(val, 10) * Math.pow(10, minorUnits);
        }}
    }}

    async function handleAuthorizeSubmit(e) {{
        e.preventDefault();
        const errEl = document.getElementById("authCreateError");
        errEl.innerHTML = "";

        const handle = document.getElementById("auth_handle").value.trim();
        const rawAmt = document.getElementById("auth_amount").value.trim();
        const note = document.getElementById("auth_note").value.trim();
        const visibility = document.getElementById("auth_visibility").value;

        let minorAmt;
        try {{
            minorAmt = parseAmountToMinor(rawAmt, MINOR_UNITS);
        }} catch(err) {{
            errEl.innerHTML = `<div data-testid="authorize-error" class="error">${{err.message}}</div>`;
            return;
        }}

        const body = {{ to_handle: handle, amount: minorAmt, note: note, visibility: visibility }};
        const token = getCookie("token");

        try {{
            const res = await fetch("/authorizations", {{
                method: "POST",
                headers: {{
                    "Content-Type": "application/json",
                    "Idempotency-Key": crypto.randomUUID(),
                    ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}})
                }},
                body: JSON.stringify(body)
            }});

            if (!res.ok) {{
                const d = await res.json().catch(() => ({{}}));
                const msg = (d.error && d.error.message) || "Authorization refused";
                errEl.innerHTML = `<div data-testid="authorize-error" class="error">${{msg}}</div>`;
                return;
            }}
            window.location.reload();
        }} catch(err) {{
            errEl.innerHTML = `<div data-testid="authorize-error" class="error">${{err.message}}</div>`;
        }}
    }}

    async function captureHold(aid) {{
        const token = getCookie("token");
        const rawAmt = document.getElementById(`cap_amt_${{aid}}`).value.trim();
        const errContainer = document.getElementById("authzErrorContainer") || document.querySelector('[data-testid="authorization-error"]');

        let minorAmt;
        try {{
            minorAmt = parseAmountToMinor(rawAmt, MINOR_UNITS);
        }} catch(err) {{
            if (errContainer) errContainer.innerHTML = `<div data-testid="authorization-error" class="error">${{err.message}}</div>`;
            return;
        }}

        try {{
            const res = await fetch(`/authorizations/${{aid}}/capture`, {{
                method: "POST",
                headers: {{
                    "Content-Type": "application/json",
                    "Idempotency-Key": crypto.randomUUID(),
                    ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}})
                }},
                body: JSON.stringify({{ amount: minorAmt }})
            }});

            if (!res.ok) {{
                const d = await res.json().catch(() => ({{}}));
                const msg = (d.error && d.error.message) || "Capture refused";
                if (errContainer) errContainer.innerHTML = `<div data-testid="authorization-error" class="error">${{msg}}</div>`;
                return;
            }}
            window.location.reload();
        }} catch(e) {{
            if (errContainer) errContainer.innerHTML = `<div data-testid="authorization-error" class="error">${{e.message}}</div>`;
        }}
    }}

    async function voidHold(aid) {{
        const token = getCookie("token");
        const errContainer = document.getElementById("authzErrorContainer") || document.querySelector('[data-testid="authorization-error"]');
        try {{
            const res = await fetch(`/authorizations/${{aid}}/void`, {{
                method: "POST",
                headers: token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}}
            }});
            if (!res.ok) {{
                const d = await res.json().catch(() => ({{}}));
                const msg = (d.error && d.error.message) || "Void refused";
                if (errContainer) errContainer.innerHTML = `<div data-testid="authorization-error" class="error">${{msg}}</div>`;
                return;
            }}
            window.location.reload();
        }} catch(e) {{
            if (errContainer) errContainer.innerHTML = `<div data-testid="authorization-error" class="error">${{e.message}}</div>`;
        }}
    }}
    </script>
    """
    return page_frame("Authorizations", content, user)
